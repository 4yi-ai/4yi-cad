"""SQLite-backed CAD session/version metadata store."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = "/tmp/4yi-cad/sessions.sqlite3"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_db_path() -> str:
    explicit = os.environ.get("CAD_SESSION_DB_PATH", "").strip()
    if explicit:
        return explicit
    data_dir = os.environ.get("CAD_DATA_DIR", "").strip()
    if data_dir:
        return str(Path(data_dir) / "sessions.sqlite3")
    return DEFAULT_DB_PATH


@dataclass(frozen=True)
class StoredSession:
    id: str
    title: str
    active_version_id: str | None
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class StoredVersion:
    id: str
    session_id: str
    version_number: int
    parent_version_id: str | None
    intent: str
    user_instruction: str | None
    design_state: dict[str, Any]
    script: str
    geometry_summary: dict[str, Any]
    patch: dict[str, Any] | None
    metadata: dict[str, Any]
    status: str
    error: str | None
    created_at: str


class SessionStore:
    def create_session(self, *, title: str | None = None) -> StoredSession:
        raise NotImplementedError

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    def get_version(self, session_id: str, version_id: str) -> StoredVersion | None:
        raise NotImplementedError

    def add_version(
        self,
        *,
        session_id: str,
        intent: str,
        design_state: dict[str, Any],
        script: str,
        geometry_summary: dict[str, Any],
        user_instruction: str | None = None,
        patch: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "ok",
        error: str | None = None,
    ) -> StoredVersion:
        raise NotImplementedError

    def update_version_metadata(
        self,
        *,
        session_id: str,
        version_id: str,
        metadata: dict[str, Any],
    ) -> StoredVersion:
        raise NotImplementedError


class SqliteSessionStore(SessionStore):
    def __init__(self, db_path: str | os.PathLike[str] | None = None) -> None:
        self.db_path = Path(db_path or default_db_path())
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def create_session(self, *, title: str | None = None) -> StoredSession:
        now = utc_now()
        session = StoredSession(
            id=uuid.uuid4().hex,
            title=(title or "Untitled CAD session").strip() or "Untitled CAD session",
            active_version_id=None,
            created_at=now,
            updated_at=now,
        )
        with self._connect() as con:
            con.execute(
                """
                insert into design_sessions
                    (id, title, active_version_id, created_at, updated_at)
                values (?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.title,
                    session.active_version_id,
                    session.created_at,
                    session.updated_at,
                ),
            )
        return session

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as con:
            session_row = con.execute(
                """
                select id, title, active_version_id, created_at, updated_at
                from design_sessions
                where id = ?
                """,
                (session_id,),
            ).fetchone()
            if session_row is None:
                return None

            version_rows = con.execute(
                """
                select
                    id,
                    session_id,
                    version_number,
                    parent_version_id,
                    intent,
                    user_instruction,
                    design_state_json,
                    script,
                    geometry_summary_json,
                    patch_json,
                    metadata_json,
                    status,
                    error,
                    created_at
                from design_versions
                where session_id = ?
                order by version_number asc
                """,
                (session_id,),
            ).fetchall()

        session = _session_from_row(session_row)
        versions = [_version_from_row(row) for row in version_rows]
        active_version = next(
            (version for version in versions if version.id == session.active_version_id),
            versions[-1] if versions else None,
        )
        return {
            "session": _session_to_dict(session),
            "active_version": _version_to_dict(active_version) if active_version else None,
            "versions": [_version_summary(version) for version in versions],
        }

    def get_version(self, session_id: str, version_id: str) -> StoredVersion | None:
        with self._connect() as con:
            row = con.execute(
                """
                select
                    id,
                    session_id,
                    version_number,
                    parent_version_id,
                    intent,
                    user_instruction,
                    design_state_json,
                    script,
                    geometry_summary_json,
                    patch_json,
                    metadata_json,
                    status,
                    error,
                    created_at
                from design_versions
                where session_id = ? and id = ?
                """,
                (session_id, version_id),
            ).fetchone()
        return _version_from_row(row) if row is not None else None

    def add_version(
        self,
        *,
        session_id: str,
        intent: str,
        design_state: dict[str, Any],
        script: str,
        geometry_summary: dict[str, Any],
        user_instruction: str | None = None,
        patch: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "ok",
        error: str | None = None,
    ) -> StoredVersion:
        now = utc_now()
        with self._connect() as con:
            session_row = con.execute(
                "select active_version_id from design_sessions where id = ?",
                (session_id,),
            ).fetchone()
            if session_row is None:
                raise KeyError(session_id)

            current_max = con.execute(
                "select coalesce(max(version_number), 0) from design_versions where session_id = ?",
                (session_id,),
            ).fetchone()[0]
            version = StoredVersion(
                id=uuid.uuid4().hex,
                session_id=session_id,
                version_number=int(current_max) + 1,
                parent_version_id=session_row["active_version_id"],
                intent=intent,
                user_instruction=user_instruction,
                design_state=design_state,
                script=script,
                geometry_summary=geometry_summary,
                patch=patch,
                metadata=metadata or {},
                status=status,
                error=error,
                created_at=now,
            )
            con.execute(
                """
                insert into design_versions
                    (
                        id,
                        session_id,
                        version_number,
                        parent_version_id,
                        intent,
                        user_instruction,
                        design_state_json,
                        script,
                        geometry_summary_json,
                        patch_json,
                        metadata_json,
                        status,
                        error,
                        created_at
                    )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    version.id,
                    version.session_id,
                    version.version_number,
                    version.parent_version_id,
                    version.intent,
                    version.user_instruction,
                    _json_dump(version.design_state),
                    version.script,
                    _json_dump(version.geometry_summary),
                    _json_dump(version.patch) if version.patch is not None else None,
                    _json_dump(version.metadata),
                    version.status,
                    version.error,
                    version.created_at,
                ),
            )
            con.execute(
                """
                update design_sessions
                set active_version_id = ?, updated_at = ?
                where id = ?
                """,
                (version.id, now, session_id),
            )
        return version

    def update_version_metadata(
        self,
        *,
        session_id: str,
        version_id: str,
        metadata: dict[str, Any],
    ) -> StoredVersion:
        with self._connect() as con:
            row = con.execute(
                "select id from design_versions where session_id = ? and id = ?",
                (session_id, version_id),
            ).fetchone()
            if row is None:
                raise KeyError(version_id)
            con.execute(
                """
                update design_versions
                set metadata_json = ?
                where session_id = ? and id = ?
                """,
                (_json_dump(metadata), session_id, version_id),
            )
        version = self.get_version(session_id, version_id)
        if version is None:
            raise KeyError(version_id)
        return version

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("pragma foreign_keys = on")
        con.execute("pragma journal_mode = wal")
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.executescript(
                """
                create table if not exists design_sessions (
                    id text primary key,
                    title text not null,
                    active_version_id text,
                    created_at text not null,
                    updated_at text not null
                );

                create table if not exists design_versions (
                    id text primary key,
                    session_id text not null references design_sessions(id) on delete cascade,
                    version_number integer not null,
                    parent_version_id text,
                    intent text not null,
                    user_instruction text,
                    design_state_json text not null,
                    script text not null,
                    geometry_summary_json text not null,
                    patch_json text,
                    metadata_json text not null default '{}',
                    status text not null,
                    error text,
                    created_at text not null,
                    unique(session_id, version_number)
                );

                create index if not exists idx_design_versions_session
                    on design_versions(session_id, version_number);
                """
            )
            columns = {
                row["name"]
                for row in con.execute("pragma table_info(design_versions)").fetchall()
            }
            if "metadata_json" not in columns:
                con.execute(
                    "alter table design_versions add column metadata_json text not null default '{}'"
                )


def _json_dump(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load(value: str | None) -> dict[str, Any]:
    if not value:
        return {}
    return json.loads(value)


def _session_from_row(row: sqlite3.Row) -> StoredSession:
    return StoredSession(
        id=row["id"],
        title=row["title"],
        active_version_id=row["active_version_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _version_from_row(row: sqlite3.Row) -> StoredVersion:
    return StoredVersion(
        id=row["id"],
        session_id=row["session_id"],
        version_number=row["version_number"],
        parent_version_id=row["parent_version_id"],
        intent=row["intent"],
        user_instruction=row["user_instruction"],
        design_state=_json_load(row["design_state_json"]),
        script=row["script"],
        geometry_summary=_json_load(row["geometry_summary_json"]),
        patch=_json_load(row["patch_json"]) if row["patch_json"] else None,
        metadata=_json_load(row["metadata_json"]),
        status=row["status"],
        error=row["error"],
        created_at=row["created_at"],
    )


def _session_to_dict(session: StoredSession) -> dict[str, Any]:
    return {
        "id": session.id,
        "title": session.title,
        "active_version_id": session.active_version_id,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def _version_to_dict(version: StoredVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "session_id": version.session_id,
        "version_number": version.version_number,
        "parent_version_id": version.parent_version_id,
        "intent": version.intent,
        "user_instruction": version.user_instruction,
        "design_state": version.design_state,
        "script": version.script,
        "geometry_summary": version.geometry_summary,
        "patch": version.patch,
        "metadata": version.metadata,
        "status": version.status,
        "error": version.error,
        "created_at": version.created_at,
    }


def _version_summary(version: StoredVersion) -> dict[str, Any]:
    return {
        "id": version.id,
        "version_number": version.version_number,
        "parent_version_id": version.parent_version_id,
        "intent": version.intent,
        "user_instruction": version.user_instruction,
        "geometry_summary": version.geometry_summary,
        "patch": version.patch,
        "metadata": version.metadata,
        "status": version.status,
        "error": version.error,
        "created_at": version.created_at,
    }
