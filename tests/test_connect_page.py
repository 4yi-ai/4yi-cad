"""Tests for the `/connect` page (P3a) and addon packaging metadata.

`/connect` is the "连接本地 FreeCAD" self-serve page: it lets a user issue and
manage a per-install API token (P1 endpoints under `/api/tokens`, same-origin,
SSO-protected — no bearer token needed) and walks them through installing the
`fouryi_cad_companion` FreeCAD addon from the standalone `cad-addon` repo.

It deliberately is NOT under GUARDED_PREFIXES (see app/main.py) — it must be
reachable the same way /api/tokens is: same-origin browser session, no bearer.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.session_store import SqliteSessionStore

ROOT = Path(__file__).resolve().parents[1]


def _make_store(tmp_path):
    return SqliteSessionStore(tmp_path / "sessions.sqlite3")


def _local_client(tmp_path):
    app = create_app(session_store=_make_store(tmp_path))
    return TestClient(app)


def _external_client(tmp_path):
    """Simulates a request from outside the container/localhost.

    Mirrors tests/test_api_tokens.py::_external_client — a public-looking
    client host exercises the non-exempt path through the bearer middleware,
    proving /connect is not accidentally caught by GUARDED_PREFIXES.
    """
    app = create_app(session_store=_make_store(tmp_path))
    return TestClient(app, client=("203.0.113.9", 12345))


def test_connect_page_returns_html(tmp_path):
    client = _local_client(tmp_path)

    resp = client.get("/connect")

    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]


def test_connect_page_contains_title_and_key_content(tmp_path):
    client = _local_client(tmp_path)

    resp = client.get("/connect")
    body = resp.text

    assert "连接本地 FreeCAD" in body
    assert "github.com/4yi-ai/cad-addon" in body
    assert "/api/tokens" in body


def test_connect_page_lists_all_three_os_mod_paths(tmp_path):
    client = _local_client(tmp_path)

    body = client.get("/connect").text

    assert ".local/share/FreeCAD/Mod" in body  # Linux
    assert "%APPDATA%\\FreeCAD\\Mod" in body  # Windows
    assert "Library/Application Support/FreeCAD/Mod" in body  # macOS


def test_connect_page_not_blocked_by_bearer_guard_for_external_client(tmp_path):
    client = _external_client(tmp_path)

    resp = client.get("/connect")

    assert resp.status_code == 200


def test_connect_page_not_under_guarded_prefixes():
    from app.main import GUARDED_PREFIXES

    assert not "/connect".startswith(GUARDED_PREFIXES)
    for prefix in GUARDED_PREFIXES:
        assert not prefix.startswith("/connect")


def test_package_xml_declares_freecadmin_minimum_version():
    package_xml = (ROOT / "freecad-addon" / "fouryi_cad_companion" / "package.xml").read_text()

    assert "<freecadmin>" in package_xml


def test_package_xml_repository_url_points_at_cad_addon_repo():
    package_xml = (ROOT / "freecad-addon" / "fouryi_cad_companion" / "package.xml").read_text()

    assert "4yi-ai/cad-addon" in package_xml
    assert 'type="repository"' in package_xml


def test_package_xml_version():
    package_xml = (ROOT / "freecad-addon" / "fouryi_cad_companion" / "package.xml").read_text()

    assert "<version>0.5.0</version>" in package_xml
