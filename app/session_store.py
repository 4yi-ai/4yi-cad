"""SQLite-backed CAD session/version metadata store."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.storage_paths import writable_platform_data_dir

DEFAULT_DB_PATH = "/tmp/4yi-cad/sessions.sqlite3"

API_TOKEN_PREFIX = "4yi-cad-tok-"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_db_path() -> str:
    explicit = os.environ.get("CAD_SESSION_DB_PATH", "").strip()
    if explicit:
        return explicit
    data_dir = writable_platform_data_dir()
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


@dataclass(frozen=True)
class StoredRemoteFreeCadSession:
    id: str
    workbench_session_id: str
    base_version_id: str | None
    current_version_id: str | None
    status: str
    remote_url: str | None
    bridge_status: str
    created_at: str
    started_at: str | None
    last_active_at: str
    stopped_at: str | None
    metadata: dict[str, Any]


@dataclass(frozen=True)
class StoredRemoteFreeCadSessionEvent:
    id: str
    remote_session_id: str
    event_type: str
    metadata: dict[str, Any]
    occurred_at: str


@dataclass(frozen=True)
class StoredRemoteFreeCadCommand:
    id: str
    remote_session_id: str
    op: str
    input: dict[str, Any]
    base_version_id: str | None
    status: str
    result: dict[str, Any] | None
    error: str | None
    created_at: str
    dispatched_at: str | None
    completed_at: str | None
    metadata: dict[str, Any]


class SessionStore:
    def create_session(self, *, title: str | None = None) -> StoredSession:
        raise NotImplementedError

    def list_sessions(self, *, limit: int = 20) -> list[dict[str, Any]]:
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

    def create_or_reuse_remote_freecad_session(
        self,
        *,
        remote_session_id: str | None = None,
        workbench_session_id: str,
        base_version_id: str | None = None,
        reuse: bool = True,
        remote_url: str | None = None,
        status: str = "starting",
        bridge_status: str = "pending",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[StoredRemoteFreeCadSession, bool]:
        raise NotImplementedError

    def list_remote_freecad_sessions(
        self,
        *,
        workbench_session_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_remote_freecad_session(
        self,
        remote_session_id: str,
    ) -> StoredRemoteFreeCadSession | None:
        raise NotImplementedError

    def update_remote_freecad_session(
        self,
        *,
        remote_session_id: str,
        status: str | None = None,
        current_version_id: str | None = None,
        remote_url: str | None = None,
        bridge_status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StoredRemoteFreeCadSession:
        raise NotImplementedError

    def stop_remote_freecad_session(
        self,
        *,
        remote_session_id: str,
        reason: str | None = None,
    ) -> StoredRemoteFreeCadSession:
        raise NotImplementedError

    def add_remote_freecad_session_event(
        self,
        *,
        remote_session_id: str,
        event_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> StoredRemoteFreeCadSessionEvent:
        raise NotImplementedError

    def create_remote_freecad_session_command(
        self,
        *,
        remote_session_id: str,
        op: str,
        input: dict[str, Any] | None = None,
        base_version_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StoredRemoteFreeCadCommand:
        raise NotImplementedError

    def claim_pending_remote_freecad_session_commands(
        self,
        *,
        remote_session_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def get_remote_freecad_session_command(
        self,
        *,
        remote_session_id: str,
        command_id: str,
    ) -> StoredRemoteFreeCadCommand | None:
        raise NotImplementedError

    def complete_remote_freecad_session_command(
        self,
        *,
        remote_session_id: str,
        command_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StoredRemoteFreeCadCommand:
        raise NotImplementedError

    def list_remote_freecad_session_events(
        self,
        *,
        remote_session_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def create_api_token(self, label: str | None = None) -> dict[str, Any]:
        raise NotImplementedError

    def list_api_tokens(self) -> list[dict[str, Any]]:
        raise NotImplementedError

    def revoke_api_token(self, token_id: str) -> bool:
        raise NotImplementedError

    def verify_api_token(self, token: str) -> bool:
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

    def list_sessions(self, *, limit: int = 20) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit or 20), 100))
        with self._connect() as con:
            session_rows = con.execute(
                """
                select id, title, active_version_id, created_at, updated_at
                from design_sessions
                order by updated_at desc, created_at desc
                limit ?
                """,
                (bounded_limit,),
            ).fetchall()
            summaries: list[dict[str, Any]] = []
            for session_row in session_rows:
                session = _session_from_row(session_row)
                version_count = int(
                    con.execute(
                        "select count(*) from design_versions where session_id = ?",
                        (session.id,),
                    ).fetchone()[0]
                )
                active_version = None
                if session.active_version_id:
                    version_row = con.execute(
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
                        (session.id, session.active_version_id),
                    ).fetchone()
                    active_version = _version_from_row(version_row) if version_row else None
                summaries.append(_session_summary(session, active_version, version_count))
        return summaries

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

    def create_or_reuse_remote_freecad_session(
        self,
        *,
        remote_session_id: str | None = None,
        workbench_session_id: str,
        base_version_id: str | None = None,
        reuse: bool = True,
        remote_url: str | None = None,
        status: str = "starting",
        bridge_status: str = "pending",
        metadata: dict[str, Any] | None = None,
    ) -> tuple[StoredRemoteFreeCadSession, bool]:
        now = utc_now()
        with self._connect() as con:
            session_row = con.execute(
                "select active_version_id from design_sessions where id = ?",
                (workbench_session_id,),
            ).fetchone()
            if session_row is None:
                raise KeyError(workbench_session_id)

            resolved_base_version_id = base_version_id or session_row["active_version_id"]
            if resolved_base_version_id is not None:
                version_row = con.execute(
                    """
                    select id from design_versions
                    where session_id = ? and id = ?
                    """,
                    (workbench_session_id, resolved_base_version_id),
                ).fetchone()
                if version_row is None:
                    raise KeyError(resolved_base_version_id)

            explicit_remote_session_id = (remote_session_id or "").strip() or None
            if explicit_remote_session_id:
                explicit_row = self._remote_session_row_by_id(con, explicit_remote_session_id)
                if explicit_row is not None:
                    existing_metadata = _json_load(explicit_row["metadata_json"])
                    next_bridge_status = bridge_status
                    if (
                        bridge_status == "pending"
                        and explicit_row["bridge_status"] in {"connected", "pending"}
                    ):
                        next_bridge_status = explicit_row["bridge_status"]
                    con.execute(
                        """
                        update freecad_remote_sessions
                        set
                            workbench_session_id = ?,
                            base_version_id = ?,
                            current_version_id = ?,
                            status = ?,
                            remote_url = coalesce(?, remote_url),
                            bridge_status = ?,
                            started_at = coalesce(started_at, ?),
                            stopped_at = null,
                            metadata_json = ?,
                            last_active_at = ?
                        where id = ?
                        """,
                        (
                            workbench_session_id,
                            resolved_base_version_id,
                            resolved_base_version_id,
                            status,
                            remote_url,
                            next_bridge_status,
                            now if status in {"starting", "ready"} else None,
                            _json_dump({**existing_metadata, **(metadata or {})}),
                            now,
                            explicit_remote_session_id,
                        ),
                    )
                    updated = self._remote_session_row_by_id(con, explicit_remote_session_id)
                    return _remote_session_from_row(updated), True

            if reuse:
                reusable_row = con.execute(
                    """
                    select
                        id,
                        workbench_session_id,
                        base_version_id,
                        current_version_id,
                        status,
                        remote_url,
                        bridge_status,
                        created_at,
                        started_at,
                        last_active_at,
                        stopped_at,
                        metadata_json
                    from freecad_remote_sessions
                    where workbench_session_id = ?
                        and coalesce(base_version_id, '') = coalesce(?, '')
                        and status in ('starting', 'ready', 'idle', 'paused')
                    order by last_active_at desc, created_at desc
                    limit 1
                    """,
                    (workbench_session_id, resolved_base_version_id),
                ).fetchone()
                if reusable_row is not None:
                    con.execute(
                        """
                        update freecad_remote_sessions
                        set last_active_at = ?
                        where id = ?
                        """,
                        (now, reusable_row["id"]),
                    )
                    updated = self._remote_session_row_by_id(con, reusable_row["id"])
                    return _remote_session_from_row(updated), True

            remote_session = StoredRemoteFreeCadSession(
                id=explicit_remote_session_id or uuid.uuid4().hex,
                workbench_session_id=workbench_session_id,
                base_version_id=resolved_base_version_id,
                current_version_id=resolved_base_version_id,
                status=status,
                remote_url=remote_url,
                bridge_status=bridge_status,
                created_at=now,
                started_at=now if status in {"starting", "ready"} else None,
                last_active_at=now,
                stopped_at=None,
                metadata=metadata or {},
            )
            con.execute(
                """
                insert into freecad_remote_sessions
                    (
                        id,
                        workbench_session_id,
                        base_version_id,
                        current_version_id,
                        status,
                        remote_url,
                        bridge_status,
                        created_at,
                        started_at,
                        last_active_at,
                        stopped_at,
                        metadata_json
                    )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    remote_session.id,
                    remote_session.workbench_session_id,
                    remote_session.base_version_id,
                    remote_session.current_version_id,
                    remote_session.status,
                    remote_session.remote_url,
                    remote_session.bridge_status,
                    remote_session.created_at,
                    remote_session.started_at,
                    remote_session.last_active_at,
                    remote_session.stopped_at,
                    _json_dump(remote_session.metadata),
                ),
            )
        return remote_session, False

    def list_remote_freecad_sessions(
        self,
        *,
        workbench_session_id: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit or 20), 100))
        where = ""
        params: tuple[Any, ...] = (bounded_limit,)
        if workbench_session_id:
            where = "where workbench_session_id = ?"
            params = (workbench_session_id, bounded_limit)
        with self._connect() as con:
            rows = con.execute(
                f"""
                select
                    id,
                    workbench_session_id,
                    base_version_id,
                    current_version_id,
                    status,
                    remote_url,
                    bridge_status,
                    created_at,
                    started_at,
                    last_active_at,
                    stopped_at,
                    metadata_json
                from freecad_remote_sessions
                {where}
                order by last_active_at desc, created_at desc
                limit ?
                """,
                params,
            ).fetchall()
        return [_remote_session_to_dict(_remote_session_from_row(row)) for row in rows]

    def get_remote_freecad_session(
        self,
        remote_session_id: str,
    ) -> StoredRemoteFreeCadSession | None:
        with self._connect() as con:
            row = self._remote_session_row_by_id(con, remote_session_id)
        return _remote_session_from_row(row) if row is not None else None

    def update_remote_freecad_session(
        self,
        *,
        remote_session_id: str,
        status: str | None = None,
        current_version_id: str | None = None,
        remote_url: str | None = None,
        bridge_status: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StoredRemoteFreeCadSession:
        now = utc_now()
        with self._connect() as con:
            existing = self._remote_session_row_by_id(con, remote_session_id)
            if existing is None:
                raise KeyError(remote_session_id)
            con.execute(
                """
                update freecad_remote_sessions
                set
                    status = coalesce(?, status),
                    current_version_id = coalesce(?, current_version_id),
                    remote_url = coalesce(?, remote_url),
                    bridge_status = coalesce(?, bridge_status),
                    metadata_json = coalesce(?, metadata_json),
                    last_active_at = ?,
                    stopped_at = case
                        when ? in ('stopped', 'failed') then coalesce(stopped_at, ?)
                        else stopped_at
                    end
                where id = ?
                """,
                (
                    status,
                    current_version_id,
                    remote_url,
                    bridge_status,
                    _json_dump(metadata) if metadata is not None else None,
                    now,
                    status,
                    now,
                    remote_session_id,
                ),
            )
            row = self._remote_session_row_by_id(con, remote_session_id)
        return _remote_session_from_row(row)

    def stop_remote_freecad_session(
        self,
        *,
        remote_session_id: str,
        reason: str | None = None,
    ) -> StoredRemoteFreeCadSession:
        now = utc_now()
        with self._connect() as con:
            existing = self._remote_session_row_by_id(con, remote_session_id)
            if existing is None:
                raise KeyError(remote_session_id)
            metadata = _json_load(existing["metadata_json"])
            if reason:
                metadata["stop_reason"] = reason
            con.execute(
                """
                update freecad_remote_sessions
                set
                    status = 'stopped',
                    bridge_status = 'disconnected',
                    metadata_json = ?,
                    last_active_at = ?,
                    stopped_at = coalesce(stopped_at, ?)
                where id = ?
                """,
                (_json_dump(metadata), now, now, remote_session_id),
            )
            row = self._remote_session_row_by_id(con, remote_session_id)
        return _remote_session_from_row(row)

    def add_remote_freecad_session_event(
        self,
        *,
        remote_session_id: str,
        event_type: str,
        metadata: dict[str, Any] | None = None,
    ) -> StoredRemoteFreeCadSessionEvent:
        now = utc_now()
        event = StoredRemoteFreeCadSessionEvent(
            id=uuid.uuid4().hex,
            remote_session_id=remote_session_id,
            event_type=event_type,
            metadata=metadata or {},
            occurred_at=now,
        )
        with self._connect() as con:
            if self._remote_session_row_by_id(con, remote_session_id) is None:
                raise KeyError(remote_session_id)
            con.execute(
                """
                insert into freecad_remote_session_events
                    (id, remote_session_id, event_type, metadata_json, occurred_at)
                values (?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.remote_session_id,
                    event.event_type,
                    _json_dump(event.metadata),
                    event.occurred_at,
                ),
            )
            con.execute(
                """
                update freecad_remote_sessions
                set last_active_at = ?
                where id = ?
                """,
                (now, remote_session_id),
            )
        return event

    def create_remote_freecad_session_command(
        self,
        *,
        remote_session_id: str,
        op: str,
        input: dict[str, Any] | None = None,
        base_version_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StoredRemoteFreeCadCommand:
        now = utc_now()
        command = StoredRemoteFreeCadCommand(
            id=f"cmd_{uuid.uuid4().hex}",
            remote_session_id=remote_session_id,
            op=op,
            input=input or {},
            base_version_id=base_version_id,
            status="pending",
            result=None,
            error=None,
            created_at=now,
            dispatched_at=None,
            completed_at=None,
            metadata=metadata or {},
        )
        with self._connect() as con:
            if self._remote_session_row_by_id(con, remote_session_id) is None:
                raise KeyError(remote_session_id)
            con.execute(
                """
                insert into freecad_remote_session_commands
                    (
                        id,
                        remote_session_id,
                        op,
                        input_json,
                        base_version_id,
                        status,
                        result_json,
                        error,
                        created_at,
                        dispatched_at,
                        completed_at,
                        metadata_json
                    )
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    command.id,
                    command.remote_session_id,
                    command.op,
                    _json_dump(command.input),
                    command.base_version_id,
                    command.status,
                    None,
                    command.error,
                    command.created_at,
                    command.dispatched_at,
                    command.completed_at,
                    _json_dump(command.metadata),
                ),
            )
            con.execute(
                """
                update freecad_remote_sessions
                set last_active_at = ?
                where id = ?
                """,
                (now, remote_session_id),
            )
        return command

    def claim_pending_remote_freecad_session_commands(
        self,
        *,
        remote_session_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit or 10), 50))
        now = utc_now()
        with self._connect() as con:
            if self._remote_session_row_by_id(con, remote_session_id) is None:
                raise KeyError(remote_session_id)
            rows = con.execute(
                """
                select
                    id,
                    remote_session_id,
                    op,
                    input_json,
                    base_version_id,
                    status,
                    result_json,
                    error,
                    created_at,
                    dispatched_at,
                    completed_at,
                    metadata_json
                from freecad_remote_session_commands
                where remote_session_id = ?
                    and status = 'pending'
                order by created_at asc, id asc
                limit ?
                """,
                (remote_session_id, bounded_limit),
            ).fetchall()
            command_ids = [row["id"] for row in rows]
            if command_ids:
                placeholders = ",".join("?" for _ in command_ids)
                con.execute(
                    f"""
                    update freecad_remote_session_commands
                    set status = 'dispatched',
                        dispatched_at = coalesce(dispatched_at, ?)
                    where id in ({placeholders})
                    """,
                    (now, *command_ids),
                )
                rows = con.execute(
                    f"""
                    select
                        id,
                        remote_session_id,
                        op,
                        input_json,
                        base_version_id,
                        status,
                        result_json,
                        error,
                        created_at,
                        dispatched_at,
                        completed_at,
                        metadata_json
                    from freecad_remote_session_commands
                    where id in ({placeholders})
                    order by created_at asc, id asc
                    """,
                    tuple(command_ids),
                ).fetchall()
            con.execute(
                """
                update freecad_remote_sessions
                set last_active_at = ?
                where id = ?
                """,
                (now, remote_session_id),
            )
        return [_remote_command_to_dict(_remote_command_from_row(row)) for row in rows]

    def get_remote_freecad_session_command(
        self,
        *,
        remote_session_id: str,
        command_id: str,
    ) -> StoredRemoteFreeCadCommand | None:
        with self._connect() as con:
            if self._remote_session_row_by_id(con, remote_session_id) is None:
                raise KeyError(remote_session_id)
            row = self._remote_command_row_by_id(
                con,
                remote_session_id=remote_session_id,
                command_id=command_id,
            )
        return _remote_command_from_row(row) if row is not None else None

    def complete_remote_freecad_session_command(
        self,
        *,
        remote_session_id: str,
        command_id: str,
        status: str,
        result: dict[str, Any] | None = None,
        error: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> StoredRemoteFreeCadCommand:
        now = utc_now()
        with self._connect() as con:
            existing = self._remote_command_row_by_id(
                con,
                remote_session_id=remote_session_id,
                command_id=command_id,
            )
            if existing is None:
                raise KeyError(command_id)
            existing_metadata = _json_load(existing["metadata_json"])
            next_metadata = {**existing_metadata, **(metadata or {})}
            con.execute(
                """
                update freecad_remote_session_commands
                set status = ?,
                    result_json = ?,
                    error = ?,
                    completed_at = ?,
                    metadata_json = ?
                where remote_session_id = ?
                    and id = ?
                """,
                (
                    status,
                    _json_dump(result) if result is not None else None,
                    error,
                    now,
                    _json_dump(next_metadata),
                    remote_session_id,
                    command_id,
                ),
            )
            con.execute(
                """
                update freecad_remote_sessions
                set last_active_at = ?
                where id = ?
                """,
                (now, remote_session_id),
            )
            row = self._remote_command_row_by_id(
                con,
                remote_session_id=remote_session_id,
                command_id=command_id,
            )
        return _remote_command_from_row(row)

    def list_remote_freecad_session_events(
        self,
        *,
        remote_session_id: str,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        bounded_limit = max(1, min(int(limit or 50), 200))
        with self._connect() as con:
            if self._remote_session_row_by_id(con, remote_session_id) is None:
                raise KeyError(remote_session_id)
            rows = con.execute(
                """
                select id, remote_session_id, event_type, metadata_json, occurred_at
                from freecad_remote_session_events
                where remote_session_id = ?
                order by occurred_at asc, id asc
                limit ?
                """,
                (remote_session_id, bounded_limit),
            ).fetchall()
        return [_remote_session_event_to_dict(_remote_session_event_from_row(row)) for row in rows]

    def create_api_token(self, label: str | None = None) -> dict[str, Any]:
        now = utc_now()
        token = API_TOKEN_PREFIX + secrets.token_hex(24)
        token_id = uuid.uuid4().hex
        with self._connect() as con:
            con.execute(
                """
                insert into api_tokens
                    (id, token_hash, label, created_at, last_used_at, revoked_at)
                values (?, ?, ?, ?, ?, ?)
                """,
                (
                    token_id,
                    _hash_token(token),
                    label,
                    now,
                    None,
                    None,
                ),
            )
        return {
            "id": token_id,
            "token": token,
            "label": label,
            "created_at": now,
        }

    def list_api_tokens(self) -> list[dict[str, Any]]:
        with self._connect() as con:
            rows = con.execute(
                """
                select id, label, created_at, last_used_at, revoked_at
                from api_tokens
                order by created_at desc, id desc
                """
            ).fetchall()
        return [
            {
                "id": row["id"],
                "label": row["label"],
                "created_at": row["created_at"],
                "last_used_at": row["last_used_at"],
                "revoked_at": row["revoked_at"],
            }
            for row in rows
        ]

    def revoke_api_token(self, token_id: str) -> bool:
        now = utc_now()
        with self._connect() as con:
            row = con.execute(
                "select revoked_at from api_tokens where id = ?",
                (token_id,),
            ).fetchone()
            if row is None:
                return False
            if row["revoked_at"] is None:
                con.execute(
                    """
                    update api_tokens
                    set revoked_at = ?
                    where id = ?
                    """,
                    (now, token_id),
                )
        return True

    def verify_api_token(self, token: str) -> bool:
        if not token or not token.startswith(API_TOKEN_PREFIX):
            return False
        token_hash = _hash_token(token)
        now = utc_now()
        with self._connect() as con:
            rows = con.execute(
                "select id, token_hash, revoked_at from api_tokens where revoked_at is null"
            ).fetchall()
            matched_id = None
            for row in rows:
                if hmac.compare_digest(row["token_hash"], token_hash):
                    matched_id = row["id"]
                    break
            if matched_id is None:
                return False
            con.execute(
                """
                update api_tokens
                set last_used_at = ?
                where id = ?
                """,
                (now, matched_id),
            )
        return True

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        con.execute("pragma foreign_keys = on")
        con.execute("pragma journal_mode = wal")
        return con

    def _remote_session_row_by_id(
        self,
        con: sqlite3.Connection,
        remote_session_id: str,
    ) -> sqlite3.Row | None:
        return con.execute(
            """
            select
                id,
                workbench_session_id,
                base_version_id,
                current_version_id,
                status,
                remote_url,
                bridge_status,
                created_at,
                started_at,
                last_active_at,
                stopped_at,
                metadata_json
            from freecad_remote_sessions
            where id = ?
            """,
            (remote_session_id,),
        ).fetchone()

    def _remote_command_row_by_id(
        self,
        con: sqlite3.Connection,
        *,
        remote_session_id: str,
        command_id: str,
    ) -> sqlite3.Row | None:
        return con.execute(
            """
            select
                id,
                remote_session_id,
                op,
                input_json,
                base_version_id,
                status,
                result_json,
                error,
                created_at,
                dispatched_at,
                completed_at,
                metadata_json
            from freecad_remote_session_commands
            where remote_session_id = ?
                and id = ?
            """,
            (remote_session_id, command_id),
        ).fetchone()

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

                create table if not exists freecad_remote_sessions (
                    id text primary key,
                    workbench_session_id text not null references design_sessions(id)
                        on delete cascade,
                    base_version_id text,
                    current_version_id text,
                    status text not null,
                    remote_url text,
                    bridge_status text not null,
                    created_at text not null,
                    started_at text,
                    last_active_at text not null,
                    stopped_at text,
                    metadata_json text not null default '{}'
                );

                create table if not exists freecad_remote_session_events (
                    id text primary key,
                    remote_session_id text not null references freecad_remote_sessions(id)
                        on delete cascade,
                    event_type text not null,
                    metadata_json text not null default '{}',
                    occurred_at text not null
                );

                create table if not exists freecad_remote_session_commands (
                    id text primary key,
                    remote_session_id text not null references freecad_remote_sessions(id)
                        on delete cascade,
                    op text not null,
                    input_json text not null default '{}',
                    base_version_id text,
                    status text not null,
                    result_json text,
                    error text,
                    created_at text not null,
                    dispatched_at text,
                    completed_at text,
                    metadata_json text not null default '{}'
                );

                create index if not exists idx_freecad_remote_sessions_workbench
                    on freecad_remote_sessions(workbench_session_id, last_active_at);

                create index if not exists idx_freecad_remote_session_events_session
                    on freecad_remote_session_events(remote_session_id, occurred_at);

                create index if not exists idx_freecad_remote_session_commands_pending
                    on freecad_remote_session_commands(remote_session_id, status, created_at);

                create table if not exists api_tokens (
                    id text primary key,
                    token_hash text not null unique,
                    label text,
                    created_at text not null,
                    last_used_at text,
                    revoked_at text
                );
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


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


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


def _session_summary(
    session: StoredSession,
    active_version: StoredVersion | None,
    version_count: int,
) -> dict[str, Any]:
    return {
        "session": _session_to_dict(session),
        "active_version": _version_summary(active_version) if active_version else None,
        "version_count": version_count,
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


def _remote_session_from_row(row: sqlite3.Row) -> StoredRemoteFreeCadSession:
    return StoredRemoteFreeCadSession(
        id=row["id"],
        workbench_session_id=row["workbench_session_id"],
        base_version_id=row["base_version_id"],
        current_version_id=row["current_version_id"],
        status=row["status"],
        remote_url=row["remote_url"],
        bridge_status=row["bridge_status"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        last_active_at=row["last_active_at"],
        stopped_at=row["stopped_at"],
        metadata=_json_load(row["metadata_json"]),
    )


def _remote_session_to_dict(session: StoredRemoteFreeCadSession) -> dict[str, Any]:
    return {
        "id": session.id,
        "session_id": session.id,
        "workbench_session_id": session.workbench_session_id,
        "base_version_id": session.base_version_id,
        "current_version_id": session.current_version_id,
        "status": session.status,
        "remote_url": session.remote_url,
        "bridge_status": session.bridge_status,
        "created_at": session.created_at,
        "started_at": session.started_at,
        "last_active_at": session.last_active_at,
        "stopped_at": session.stopped_at,
        "metadata": session.metadata,
    }


def _remote_session_event_from_row(row: sqlite3.Row) -> StoredRemoteFreeCadSessionEvent:
    return StoredRemoteFreeCadSessionEvent(
        id=row["id"],
        remote_session_id=row["remote_session_id"],
        event_type=row["event_type"],
        metadata=_json_load(row["metadata_json"]),
        occurred_at=row["occurred_at"],
    )


def _remote_session_event_to_dict(event: StoredRemoteFreeCadSessionEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "remote_session_id": event.remote_session_id,
        "event_type": event.event_type,
        "metadata": event.metadata,
        "occurred_at": event.occurred_at,
    }


def _remote_command_from_row(row: sqlite3.Row) -> StoredRemoteFreeCadCommand:
    return StoredRemoteFreeCadCommand(
        id=row["id"],
        remote_session_id=row["remote_session_id"],
        op=row["op"],
        input=_json_load(row["input_json"]),
        base_version_id=row["base_version_id"],
        status=row["status"],
        result=_json_load(row["result_json"]) if row["result_json"] else None,
        error=row["error"],
        created_at=row["created_at"],
        dispatched_at=row["dispatched_at"],
        completed_at=row["completed_at"],
        metadata=_json_load(row["metadata_json"]),
    )


def _remote_command_to_dict(command: StoredRemoteFreeCadCommand) -> dict[str, Any]:
    return {
        "id": command.id,
        "command_id": command.id,
        "remote_session_id": command.remote_session_id,
        "session_id": command.remote_session_id,
        "op": command.op,
        "input": command.input,
        "base_version_id": command.base_version_id,
        "status": command.status,
        "result": command.result,
        "error": command.error,
        "created_at": command.created_at,
        "dispatched_at": command.dispatched_at,
        "completed_at": command.completed_at,
        "metadata": command.metadata,
    }
