"""Tests for the FreeCAD addon's remote-mode config/overlay/auth layer.

The addon module (freecad-addon/fouryi_cad_companion/FourYiCadCompanion.py)
is loaded by file path (mirrors tests/test_freecad_workbench_addon.py) so it
can be imported without FreeCAD installed: App/Gui are None-guarded.
"""

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ADDON_PATH = ROOT / "freecad-addon/fouryi_cad_companion/FourYiCadCompanion.py"


def _load_addon():
    spec = importlib.util.spec_from_file_location("FourYiCadCompanion", ADDON_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class FakeParams:
    """dict-backed stand-in for FreeCAD.ParamGet(...)."""

    def __init__(self, values=None):
        self._values = dict(values or {})

    def GetString(self, name, default=""):
        return self._values.get(name, default)

    def SetString(self, name, value):
        self._values[name] = value


class FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


# ---------------------------------------------------------------------------
# remote_overlay_env
# ---------------------------------------------------------------------------


def test_container_mode_ignores_param_layer_entirely():
    addon = _load_addon()
    base_env = {"CAD_BRIDGE_POLL_URL": "http://127.0.0.1:9000/api/freecad/sessions/x/bridge/poll"}
    params = FakeParams({"ServerUrl": "https://cad.example.com/", "ApiToken": "should-not-leak"})

    overlay = addon.remote_overlay_env(base_env=base_env, params=params)

    assert overlay == base_env
    assert overlay is not base_env
    assert "CAD_API_TOKEN" not in overlay


def test_remote_mode_synthesizes_bridge_urls_and_session_id():
    addon = _load_addon()
    params = FakeParams({"ServerUrl": "https://cad.example.com/", "ApiToken": "4yi-cad-tok-xyz"})

    overlay = addon.remote_overlay_env(base_env={}, params=params)

    sid = overlay["CAD_REMOTE_SESSION_ID"]
    assert sid.startswith("local-")
    assert overlay["CAD_BRIDGE_MODE"] == "workbench"
    assert overlay["CAD_BRIDGE_AUTOSTART"] == "1"
    assert overlay["CAD_BRIDGE_POLL_URL"] == (
        f"https://cad.example.com/api/freecad/sessions/{sid}/bridge/poll"
    )
    assert overlay["CAD_BRIDGE_HEARTBEAT_URL"] == (
        f"https://cad.example.com/api/freecad/sessions/{sid}/bridge/heartbeat"
    )
    assert overlay["CAD_BRIDGE_SAVE_URL"] == (
        f"https://cad.example.com/api/freecad/sessions/{sid}/bridge/save"
    )
    assert overlay["CAD_CONTROL_PLANE_URL"] == "https://cad.example.com"
    assert overlay["CAD_API_TOKEN"] == "4yi-cad-tok-xyz"

    # LocalSessionId is persisted on the param object; a second call is stable.
    assert params.GetString("LocalSessionId", "") == sid
    overlay_again = addon.remote_overlay_env(base_env={}, params=params)
    assert overlay_again["CAD_REMOTE_SESSION_ID"] == sid


def test_remote_mode_without_token_omits_api_token_key():
    addon = _load_addon()
    params = FakeParams({"ServerUrl": "https://cad.example.com"})

    overlay = addon.remote_overlay_env(base_env={}, params=params)

    assert "CAD_API_TOKEN" not in overlay


def test_remote_mode_preserves_other_base_env_keys():
    addon = _load_addon()
    params = FakeParams({"ServerUrl": "https://cad.example.com"})
    base_env = {"UNRELATED_KEY": "kept"}

    overlay = addon.remote_overlay_env(base_env=base_env, params=params)

    assert overlay["UNRELATED_KEY"] == "kept"


def test_unconfigured_returns_base_env_unchanged():
    addon = _load_addon()
    base_env = {"SOME_OTHER_VAR": "1"}
    params = FakeParams({})

    overlay = addon.remote_overlay_env(base_env=base_env, params=params)

    assert overlay == base_env


def test_unconfigured_with_no_params_object_returns_base_env_unchanged():
    addon = _load_addon()
    base_env = {"SOME_OTHER_VAR": "1"}

    # App is None in this test environment, so addon_params() -> None; the
    # module must not blow up when params is unavailable.
    overlay = addon.remote_overlay_env(base_env=base_env, params=None)

    assert overlay == base_env


# ---------------------------------------------------------------------------
# local_session_id
# ---------------------------------------------------------------------------


def test_local_session_id_generates_and_persists():
    addon = _load_addon()
    params = FakeParams({})

    sid = addon.local_session_id(params)

    assert sid.startswith("local-")
    assert params.GetString("LocalSessionId", "") == sid
    assert addon.local_session_id(params) == sid


def test_local_session_id_reuses_existing_value():
    addon = _load_addon()
    params = FakeParams({"LocalSessionId": "local-abcdef123456"})

    assert addon.local_session_id(params) == "local-abcdef123456"


# ---------------------------------------------------------------------------
# auth_headers
# ---------------------------------------------------------------------------


def test_auth_headers_with_token():
    addon = _load_addon()

    assert addon.auth_headers({"CAD_API_TOKEN": "tok-123"}) == {
        "Authorization": "Bearer tok-123"
    }


def test_auth_headers_without_token():
    addon = _load_addon()

    assert addon.auth_headers({}) == {}
    assert addon.auth_headers({"CAD_API_TOKEN": ""}) == {}


# ---------------------------------------------------------------------------
# post_json bearer injection
# ---------------------------------------------------------------------------


def test_post_json_injects_bearer_header_from_env(monkeypatch):
    addon = _load_addon()
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        return FakeResponse(b'{"ok": true}')

    monkeypatch.setattr(addon.urllib.request, "urlopen", fake_urlopen)

    result = addon.post_json(
        "http://control.test/api/freecad/sessions/local-1/bridge/poll",
        {"a": 1},
        timeout=5.0,
        env={"CAD_API_TOKEN": "tok-123"},
    )

    assert result == {"ok": True}
    assert captured["request"].get_header("Authorization") == "Bearer tok-123"


def test_post_json_without_env_token_has_no_authorization_header(monkeypatch):
    addon = _load_addon()
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        return FakeResponse(b"{}")

    monkeypatch.setattr(addon.urllib.request, "urlopen", fake_urlopen)

    addon.post_json("http://control.test/poll", {"a": 1}, timeout=5.0, env={})

    assert captured["request"].get_header("Authorization") is None


def test_post_json_default_env_is_none_no_authorization_header(monkeypatch):
    # Container mode: callers that don't pass env (unchanged default) must
    # continue to produce zero-header-change behaviour.
    addon = _load_addon()
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        return FakeResponse(b"{}")

    monkeypatch.setattr(addon.urllib.request, "urlopen", fake_urlopen)

    addon.post_json("http://control.test/poll", {"a": 1}, timeout=5.0)

    assert captured["request"].get_header("Authorization") is None


# ---------------------------------------------------------------------------
# load_model_bytes bearer injection
# ---------------------------------------------------------------------------


def test_load_model_bytes_download_injects_bearer_header(monkeypatch):
    addon = _load_addon()
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        return FakeResponse(b"FCSTDBYTES")

    monkeypatch.setattr(addon.urllib.request, "urlopen", fake_urlopen)

    data = addon.load_model_bytes(
        {"fcstd_url": "http://control.test/api/freecad/sessions/x/versions/v1/artifacts/fcstd"},
        {"CAD_API_TOKEN": "tok-abc", "CAD_CONTROL_PLANE_URL": "http://control.test"},
        5.0,
    )

    assert data == b"FCSTDBYTES"
    assert captured["request"].get_header("Authorization") == "Bearer tok-abc"


def test_load_model_bytes_does_not_leak_token_to_foreign_host(monkeypatch):
    # An absolute artifact URL on a different host (e.g. a presigned S3/CDN
    # link) must NOT receive the platform Bearer token.
    addon = _load_addon()
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        return FakeResponse(b"FCSTDBYTES")

    monkeypatch.setattr(addon.urllib.request, "urlopen", fake_urlopen)

    addon.load_model_bytes(
        {"fcstd_url": "https://presigned.s3.amazonaws.com/bucket/model.FCStd?sig=abc"},
        {"CAD_API_TOKEN": "tok-secret", "CAD_CONTROL_PLANE_URL": "http://control.test"},
        5.0,
    )

    assert captured["request"].get_header("Authorization") is None


def test_load_model_bytes_download_without_token_has_no_authorization_header(monkeypatch):
    addon = _load_addon()
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        return FakeResponse(b"FCSTDBYTES")

    monkeypatch.setattr(addon.urllib.request, "urlopen", fake_urlopen)

    addon.load_model_bytes(
        {"fcstd_url": "http://control.test/api/freecad/sessions/x/versions/v1/artifacts/fcstd"},
        {},
        5.0,
    )

    assert captured["request"].get_header("Authorization") is None


# ---------------------------------------------------------------------------
# Bridge runtime loop carries the bearer header (default post_json path)
# ---------------------------------------------------------------------------


def test_bridge_runtime_default_post_carries_bearer_header_in_remote_mode(monkeypatch):
    # The heartbeat/poll/command-result/save endpoints are under the server's
    # guarded prefix, so the bridge loop's own HTTP calls must carry the token
    # in remote mode. The runtime constructed with the DEFAULT post_json (no
    # injected fake) must bind its env so every loop request is authorized.
    addon = _load_addon()
    requests = []

    def fake_urlopen(request, timeout=None):
        requests.append(request)
        return FakeResponse(b'{"commands": []}')

    monkeypatch.setattr(addon.urllib.request, "urlopen", fake_urlopen)

    runtime = addon.InProcessBridgeRuntime(
        env={
            "CAD_API_TOKEN": "tok-loop",
            "CAD_BRIDGE_HEARTBEAT_URL": "http://control.test/api/freecad/sessions/local-1/bridge/heartbeat",
            "CAD_BRIDGE_POLL_URL": "http://control.test/api/freecad/sessions/local-1/bridge/poll",
        },
    )
    runtime.run_once()

    assert requests, "expected the bridge loop to issue heartbeat/poll requests"
    for request in requests:
        assert request.get_header("Authorization") == "Bearer tok-loop"


def test_bridge_runtime_injected_post_is_left_untouched():
    # Injected 3-arg fakes (tests, alternate transports) must pass through the
    # constructor unchanged — the env-binding wrap applies only to the default.
    addon = _load_addon()

    def fake_post(url, payload, timeout):
        return {"commands": []}

    runtime = addon.InProcessBridgeRuntime(env={}, http_post=fake_post)

    assert runtime.http_post is fake_post


def test_bridge_runtime_default_wrapper_resolves_post_json_at_call_time(monkeypatch):
    # The default wrapper must resolve the module-level post_json at CALL time,
    # so monkeypatching addon.post_json is honored by a default-constructed
    # runtime (avoids the default-arg identity footgun).
    addon = _load_addon()
    calls = []

    def fake_post_json(url, payload, timeout, env=None):
        calls.append((url, env))
        return {"commands": []}

    monkeypatch.setattr(addon, "post_json", fake_post_json)

    runtime = addon.InProcessBridgeRuntime(
        env={"CAD_BRIDGE_POLL_URL": "http://control.test/poll"},
    )
    runtime.run_once()

    assert calls, "default-constructed runtime should route through patched post_json"
    assert all(env == runtime.env for _, env in calls)


# ---------------------------------------------------------------------------
# test_connection / save_connection_params (connection settings dialog logic)
# ---------------------------------------------------------------------------


class FakeHealthResponse:
    """Context-manager stand-in for urlopen()'s response, GET /healthz shape."""

    def __init__(self, status: int = 200):
        self.status = status

    def getcode(self):
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_connection_ok_on_2xx(monkeypatch):
    addon = _load_addon()

    def fake_urlopen(request, timeout=None):
        assert timeout == 5.0
        return FakeHealthResponse(200)

    monkeypatch.setattr(addon.urllib.request, "urlopen", fake_urlopen)

    ok, message = addon.test_connection("https://cad.example.com")

    assert ok is True
    assert isinstance(message, str) and message


def test_connection_strips_trailing_slash_and_hits_healthz(monkeypatch):
    addon = _load_addon()
    captured = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        return FakeHealthResponse(200)

    monkeypatch.setattr(addon.urllib.request, "urlopen", fake_urlopen)

    addon.test_connection("https://cad.example.com/", timeout=5.0)

    assert captured["url"] == "https://cad.example.com/healthz"
    assert captured["method"] == "GET"


def test_connection_url_error_returns_false_with_reason(monkeypatch):
    addon = _load_addon()

    def fake_urlopen(request, timeout=None):
        raise addon.urllib.error.URLError("boom")

    monkeypatch.setattr(addon.urllib.request, "urlopen", fake_urlopen)

    ok, message = addon.test_connection("https://cad.example.com")

    assert ok is False
    assert "boom" in message


def test_connection_http_error_returns_false_with_reason(monkeypatch):
    addon = _load_addon()

    def fake_urlopen(request, timeout=None):
        raise addon.urllib.error.HTTPError(
            "https://cad.example.com/healthz", 503, "Service Unavailable", None, None
        )

    monkeypatch.setattr(addon.urllib.request, "urlopen", fake_urlopen)

    ok, message = addon.test_connection("https://cad.example.com")

    assert ok is False
    assert "503" in message


def test_save_connection_params_writes_server_url_and_token():
    addon = _load_addon()
    params = FakeParams({})

    addon.save_connection_params("https://cad.example.com/ ", "tok-abc", params=params)

    assert params.GetString("ServerUrl", "") == "https://cad.example.com/"
    assert params.GetString("ApiToken", "") == "tok-abc"


def test_save_connection_params_empty_token_does_not_overwrite_existing():
    addon = _load_addon()
    params = FakeParams({"ServerUrl": "https://old.example.com", "ApiToken": "existing-tok"})

    addon.save_connection_params("https://cad.example.com", "", params=params)

    assert params.GetString("ServerUrl", "") == "https://cad.example.com"
    assert params.GetString("ApiToken", "") == "existing-tok"


def test_save_connection_params_uses_addon_params_when_none_given(monkeypatch):
    addon = _load_addon()
    params = FakeParams({})
    monkeypatch.setattr(addon, "addon_params", lambda: params)

    addon.save_connection_params("https://cad.example.com", "tok-xyz")

    assert params.GetString("ServerUrl", "") == "https://cad.example.com"
    assert params.GetString("ApiToken", "") == "tok-xyz"
