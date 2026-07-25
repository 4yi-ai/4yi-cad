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
import sys

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


def test_worker_nonzero_exit_is_failure_not_hang():
    worker = [PY, "-c", "import sys;sys.exit(3)"]

    res = run_sandboxed({}, timeout_s=5, worker_argv=worker)

    assert res.success is False
    assert res.timed_out is False
    assert res.error


def test_request_is_delivered_to_worker_on_stdin():
    worker = [
        PY,
        "-c",
        "import sys,json;req=json.load(sys.stdin);print(json.dumps({'echo':req['script']}))",
    ]

    res = run_sandboxed({"script": "box(1,2,3)"}, timeout_s=5, worker_argv=worker)

    assert res.success is True
    assert res.result == {"echo": "box(1,2,3)"}
