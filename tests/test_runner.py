"""Unit tests for the sandbox orchestration in app.cad.runner.

These test the process-isolation contract WITHOUT needing cadquery installed,
by injecting a fake worker command. The real worker (app/cad/worker.py) and the
actual CadQuery execution are exercised in the Docker integration smoke, not here.

The security invariants under test (plan review I2):
  - the per-org gateway token (OPENAI_API_KEY) and XCLAW_* are NEVER visible to
    the sandboxed process (env is allowlisted, not inherited)
  - a runaway script is killed at the wall-clock deadline
  - CPU rlimit is applied to the child
  - a crashing/garbage worker is reported as failure, not a hang
"""

import json
import os
import signal
import sys
from types import SimpleNamespace

import pytest

from app.cad.runner import SandboxResult, run_sandboxed

PY = sys.executable


def test_gateway_token_and_xclaw_env_are_scrubbed(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "xclaw-bsl-secret")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://platform.example/api/v1")
    monkeypatch.setenv("XCLAW_CONSUMER_ORG_ID", "org-123")

    worker = [
        PY,
        "-c",
        "import os,json;print(json.dumps({"
        "'has_key':'OPENAI_API_KEY' in os.environ,"
        "'has_base':'OPENAI_BASE_URL' in os.environ,"
        "'has_xclaw':any(k.startswith('XCLAW_') for k in os.environ),"
        "'has_path':'PATH' in os.environ}))",
    ]

    res = run_sandboxed({}, timeout_s=5, worker_argv=worker)

    assert isinstance(res, SandboxResult)
    assert res.success is True
    assert res.result == {
        "has_key": False,
        "has_base": False,
        "has_xclaw": False,
        "has_path": True,
    }


def test_freecadcmd_binary_env_is_allowlisted_but_secrets_still_scrubbed(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "xclaw-bsl-secret")
    monkeypatch.setenv("FREECADCMD_BINARY", "/opt/freecad/bin/FreeCADCmd")

    worker = [
        PY,
        "-c",
        "import os,json;print(json.dumps({"
        "'freecadcmd':os.environ.get('FREECADCMD_BINARY'),"
        "'has_key':'OPENAI_API_KEY' in os.environ}))",
    ]

    res = run_sandboxed({}, timeout_s=5, worker_argv=worker)

    assert res.success is True
    assert res.result == {
        "freecadcmd": "/opt/freecad/bin/FreeCADCmd",
        "has_key": False,
    }


def test_wall_clock_timeout_kills_runaway_worker():
    worker = [PY, "-c", "import time;time.sleep(30)"]

    res = run_sandboxed({}, timeout_s=0.3, worker_argv=worker)

    assert res.success is False
    assert res.timed_out is True


def test_cpu_rlimit_applied_to_child():
    worker = [
        PY,
        "-c",
        "import resource,json;print(json.dumps({'cpu':resource.getrlimit(resource.RLIMIT_CPU)[0]}))",
    ]

    res = run_sandboxed({}, timeout_s=5, cpu_seconds=7, worker_argv=worker)

    assert res.success is True
    assert res.result["cpu"] == 7


def test_address_space_rlimit_does_not_break_subprocess_setup():
    worker = [PY, "-c", "import json;print(json.dumps({'ok': True}))"]

    res = run_sandboxed({}, timeout_s=5, address_space_mb=4096, worker_argv=worker)

    assert res.success is True
    assert res.result == {"ok": True}


def test_worker_nonzero_exit_is_failure_not_hang():
    worker = [PY, "-c", "import sys;sys.exit(3)"]

    res = run_sandboxed({}, timeout_s=5, worker_argv=worker)

    assert res.success is False
    assert res.timed_out is False
    assert res.error


def test_worker_signal_exit_reports_signal_name(monkeypatch):
    sigxcpu = getattr(signal, "SIGXCPU", None)
    if sigxcpu is None:
        pytest.skip("SIGXCPU is not available on this platform")

    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=-int(sigxcpu), stdout="", stderr="")

    monkeypatch.setattr("app.cad.runner.subprocess.run", fake_run)

    res = run_sandboxed({}, timeout_s=5, worker_argv=[PY, "-c", "print('{}')"])

    assert res.success is False
    assert "SIGXCPU" in res.error
    assert "CPU time limit exceeded" in res.error


def test_sigkill_exit_reports_possible_memory_limit(monkeypatch):
    def fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=-int(signal.SIGKILL), stdout="", stderr="")

    monkeypatch.setattr("app.cad.runner.subprocess.run", fake_run)

    res = run_sandboxed({}, timeout_s=5, worker_argv=[PY, "-c", "print('{}')"])

    assert res.success is False
    assert "SIGKILL" in res.error
    assert "memory/container limit" in res.error


def test_subprocess_setup_error_is_failure_not_exception(monkeypatch):
    def raise_setup_error(*args, **kwargs):
        raise subprocess.SubprocessError("preexec failed")

    import subprocess

    monkeypatch.setattr("app.cad.runner.subprocess.run", raise_setup_error)

    res = run_sandboxed({}, timeout_s=5, worker_argv=[PY, "-c", "print('{}')"])

    assert res.success is False
    assert res.timed_out is False
    assert "preexec failed" in res.error


def test_request_is_delivered_to_worker_on_stdin():
    worker = [
        PY,
        "-c",
        "import sys,json;req=json.load(sys.stdin);print(json.dumps({'echo':req['script']}))",
    ]

    res = run_sandboxed({"script": "box(1,2,3)"}, timeout_s=5, worker_argv=worker)

    assert res.success is True
    assert res.result == {"echo": "box(1,2,3)"}


def test_nested_freecadcmd_runtime_inherits_scrubbed_env(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "xclaw-bsl-secret")
    monkeypatch.setenv("XCLAW_CONSUMER_ORG_ID", "org-123")

    fake_freecadcmd = tmp_path / "fake_freecadcmd.py"
    fake_freecadcmd.write_text(
        "#!" + PY + "\n" + """
import json
import os
import pathlib

out = pathlib.Path(os.environ["FOURYI_FREECAD_OUT"])
(out / "model.step").write_text("STEP")
(out / "model.stl").write_text("solid x\\nendsolid x\\n")
(out / "model.FCStd").write_text("FCStd")
payload = {
    "ok": True,
    "step_path": str(out / "model.step"),
    "stl_path": str(out / "model.stl"),
    "fcstd_path": str(out / "model.FCStd"),
    "patch_results": [{
        "env": {
            "has_openai_key": "OPENAI_API_KEY" in os.environ,
            "has_xclaw": any(key.startswith("XCLAW_") for key in os.environ),
        }
    }],
    "freecad_version": "fake",
}
print("__4YI_FREECAD_RESULT__" + json.dumps(payload))
""",
        encoding="utf-8",
    )
    os.chmod(fake_freecadcmd, 0o755)
    monkeypatch.setenv("FREECADCMD_BINARY", str(fake_freecadcmd))

    worker = [
        PY,
        "-c",
        (
            "import json;"
            "import app.cad.freecad_worker as fw;"
            "fw.render_preview_isolated=lambda path: None;"
            "res=fw.run_freecad_script('result = None', timeout=5);"
            "print(json.dumps({'ok':res['ok'], 'env':res['patch_results'][0]['env']}))"
        ),
    ]

    res = run_sandboxed({}, timeout_s=10, worker_argv=worker)

    assert res.success is True
    assert res.result == {
        "ok": True,
        "env": {
            "has_openai_key": False,
            "has_xclaw": False,
        },
    }
