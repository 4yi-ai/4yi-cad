"""Sandboxed execution of LLM-generated CAD code.

Executing model-generated Python is the dominant security surface of this app
(plan review I2). Defense in depth: this module owns the *process-level*
isolation that must hold regardless of container settings —

  - env allowlist: the child gets only PATH/HOME/TMPDIR/LANG/PYTHONPATH, so the
    per-org gateway token (OPENAI_API_KEY) and XCLAW_* org identifiers are never
    reachable by generated code (cannot be exfiltrated).
  - wall-clock deadline: a runaway/looping script is killed.
  - CPU + address-space rlimits on the child (POSIX).

Network egress blocking, non-root, read-only rootfs and seccomp are enforced at
the container/k8s layer (Dockerfile + securityContext) and verified in the
integration smoke — they are complementary, not a substitute, for the above.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[2])

DEFAULT_WORKER_ARGV = [sys.executable, "-m", "app.cad.worker"]
_CGROUP_MEMORY_LIMIT_FILES = (
    "/sys/fs/cgroup/memory.max",
    "/sys/fs/cgroup/memory/memory.limit_in_bytes",
)
_MB = 1024 * 1024


@dataclass
class SandboxResult:
    success: bool
    timed_out: bool = False
    result: dict | None = None
    error: str | None = None
    stdout: str = ""
    stderr: str = ""
    duration_s: float = 0.0


def _safe_env(workdir: str) -> dict[str, str]:
    """Allowlisted environment — inherits NOTHING that isn't listed here."""
    env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": workdir,
        "TMPDIR": workdir,
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "PYTHONPATH": _REPO_ROOT,
        "PYTHONUNBUFFERED": "1",
    }
    freecadcmd = os.environ.get("FREECADCMD_BINARY")
    if freecadcmd:
        env["FREECADCMD_BINARY"] = freecadcmd
    return env


def _read_cgroup_memory_limit_mb(paths: tuple[str, ...] = _CGROUP_MEMORY_LIMIT_FILES) -> int | None:
    for path in paths:
        try:
            raw = Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not raw or raw == "max":
            continue
        try:
            limit_bytes = int(raw)
        except ValueError:
            continue
        if limit_bytes <= 0 or limit_bytes >= 1 << 60:
            continue
        return max(1, limit_bytes // _MB)
    return None


def _effective_address_space_mb(address_space_mb: int | None, *, platform: str | None = None) -> int | None:
    if address_space_mb is None:
        return None
    platform = platform or sys.platform
    if platform == "darwin":
        return None

    cgroup_limit_mb = _read_cgroup_memory_limit_mb()
    if cgroup_limit_mb is None:
        return address_space_mb

    reserve_mb = 256 if cgroup_limit_mb >= 1024 else 96
    container_safe_mb = max(128, cgroup_limit_mb - reserve_mb)
    return min(address_space_mb, container_safe_mb)


def _rlimit_preexec(cpu_seconds: int | None, address_space_mb: int | None):
    address_space_mb = _effective_address_space_mb(address_space_mb)

    if cpu_seconds is None and address_space_mb is None:
        return None

    def _apply():  # runs in the child, after fork, before exec
        import resource

        if cpu_seconds is not None:
            resource.setrlimit(resource.RLIMIT_CPU, (cpu_seconds, cpu_seconds))
        if address_space_mb is not None:
            nbytes = address_space_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (nbytes, nbytes))

    return _apply


def _returncode_error(
    returncode: int,
    *,
    requested_address_space_mb: int | None = None,
    effective_address_space_mb: int | None = None,
    cgroup_memory_limit_mb: int | None = None,
) -> str:
    if returncode >= 0:
        return f"worker exited with code {returncode}"
    signum = -returncode
    try:
        signal_name = signal.Signals(signum).name
    except ValueError:
        signal_name = f"signal {signum}"
    if signal_name == "SIGXCPU":
        return f"worker terminated by signal {signum} ({signal_name}); CPU time limit exceeded"
    if signal_name == "SIGKILL":
        memory_context = ""
        if cgroup_memory_limit_mb:
            memory_context = f" (container memory limit {cgroup_memory_limit_mb}Mi"
            if requested_address_space_mb and effective_address_space_mb:
                memory_context += (
                    f", requested worker address space {requested_address_space_mb}Mi,"
                    f" effective {effective_address_space_mb}Mi"
                )
            memory_context += ")"
        return (
            f"worker terminated by signal {signum} ({signal_name}); possible memory/container "
            f"limit exceeded{memory_context}, simplify the geometry or use fewer high-resolution operations"
        )
    return f"worker terminated by signal {signum} ({signal_name})"


def run_sandboxed(
    request: dict,
    *,
    timeout_s: float,
    cpu_seconds: int | None = None,
    address_space_mb: int | None = None,
    workdir: str | None = None,
    worker_argv: list[str] | None = None,
) -> SandboxResult:
    """Run the CAD worker on `request` in an isolated subprocess.

    The request is delivered as JSON on the child's stdin. The child must print a
    single JSON object to stdout, which is returned as `result`. Success requires
    a zero exit code AND parseable JSON stdout.
    """
    argv = worker_argv or DEFAULT_WORKER_ARGV
    wd = workdir or os.environ.get("TMPDIR", "/tmp")
    payload = json.dumps(request)
    effective_address_space_mb = _effective_address_space_mb(address_space_mb)
    cgroup_memory_limit_mb = _read_cgroup_memory_limit_mb()

    try:
        proc = subprocess.run(
            argv,
            input=payload,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            env=_safe_env(wd),
            cwd=wd,
            preexec_fn=_rlimit_preexec(cpu_seconds, effective_address_space_mb),
        )
    except subprocess.TimeoutExpired as exc:
        return SandboxResult(
            success=False,
            timed_out=True,
            error=f"execution exceeded {timeout_s}s wall-clock limit",
            stdout=(exc.stdout or b"").decode(errors="replace")
            if isinstance(exc.stdout, bytes)
            else (exc.stdout or ""),
            stderr=(exc.stderr or b"").decode(errors="replace")
            if isinstance(exc.stderr, bytes)
            else (exc.stderr or ""),
            duration_s=timeout_s,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return SandboxResult(
            success=False,
            error=f"sandbox subprocess setup failed: {exc}",
        )

    if proc.returncode != 0:
        return SandboxResult(
            success=False,
            error=_returncode_error(
                proc.returncode,
                requested_address_space_mb=address_space_mb,
                effective_address_space_mb=effective_address_space_mb,
                cgroup_memory_limit_mb=cgroup_memory_limit_mb,
            ),
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    try:
        result = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return SandboxResult(
            success=False,
            error=f"worker did not produce valid JSON: {exc}",
            stdout=proc.stdout,
            stderr=proc.stderr,
        )

    return SandboxResult(
        success=True,
        result=result,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )
