"""Filesystem-backed CAD artifact persistence.

SQLite stores session metadata only. Preview PNGs and CAD exports are written to
per-session/per-version directories so large binary artifacts stay out of the DB.
"""

from __future__ import annotations

import base64
import binascii
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DEFAULT_ARTIFACT_ROOT = "/tmp/4yi-cad/artifacts"


ARTIFACTS = {
    "preview": ("preview.png", "image/png"),
    "step": ("model.step", "application/step"),
    "stl": ("model.stl", "model/stl"),
    "fcstd": ("model.FCStd", "application/vnd.freecad"),
    "viewer_scene": ("viewer-scene.json", "application/json"),
    "techdraw_svg": ("drawing.svg", "image/svg+xml"),
    "techdraw_dxf": ("drawing.dxf", "image/vnd.dxf"),
    "techdraw_pdf": ("drawing.pdf", "application/pdf"),
}


def default_artifact_root() -> str:
    explicit = os.environ.get("CAD_ARTIFACT_ROOT", "").strip()
    if explicit:
        return explicit
    data_dir = os.environ.get("CAD_DATA_DIR", "").strip()
    if data_dir:
        return str(Path(data_dir) / "artifacts")
    return DEFAULT_ARTIFACT_ROOT


@dataclass(frozen=True)
class ArtifactFile:
    path: Path
    media_type: str
    filename: str


class ArtifactStore:
    def save_version_artifacts(
        self,
        *,
        session_id: str,
        version_id: str,
        preview_png_b64: str | None = None,
        exports: dict[str, str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        raise NotImplementedError

    def copy_version_artifacts(
        self,
        *,
        session_id: str,
        source_version_id: str,
        dest_version_id: str,
    ) -> dict[str, dict[str, Any]]:
        raise NotImplementedError

    def get_artifact(
        self, *, session_id: str, version_id: str, artifact_name: str
    ) -> ArtifactFile | None:
        raise NotImplementedError


class FileArtifactStore(ArtifactStore):
    def __init__(self, root: str | os.PathLike[str] | None = None) -> None:
        self.root = Path(root or default_artifact_root())
        self.root.mkdir(parents=True, exist_ok=True)

    def save_version_artifacts(
        self,
        *,
        session_id: str,
        version_id: str,
        preview_png_b64: str | None = None,
        exports: dict[str, str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        refs: dict[str, dict[str, Any]] = {}
        if preview_png_b64:
            refs["preview"] = self._write_b64(
                session_id=session_id,
                version_id=version_id,
                artifact_name="preview",
                data_b64=preview_png_b64,
            )
        for name, data_b64 in (exports or {}).items():
            normalized = name.lower()
            if normalized not in ARTIFACTS or normalized == "preview" or not data_b64:
                continue
            refs[normalized] = self._write_b64(
                session_id=session_id,
                version_id=version_id,
                artifact_name=normalized,
                data_b64=data_b64,
            )
        return refs

    def copy_version_artifacts(
        self,
        *,
        session_id: str,
        source_version_id: str,
        dest_version_id: str,
    ) -> dict[str, dict[str, Any]]:
        refs: dict[str, dict[str, Any]] = {}
        source_dir = self._version_dir(session_id, source_version_id)
        if not source_dir.is_dir():
            return refs
        dest_dir = self._version_dir(session_id, dest_version_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        for artifact_name, (filename, _) in ARTIFACTS.items():
            source = source_dir / filename
            if not source.is_file():
                continue
            dest = dest_dir / filename
            shutil.copyfile(source, dest)
            refs[artifact_name] = self._ref(session_id, dest_version_id, artifact_name, dest)
        return refs

    def get_artifact(
        self, *, session_id: str, version_id: str, artifact_name: str
    ) -> ArtifactFile | None:
        normalized = artifact_name.lower()
        if normalized not in ARTIFACTS:
            return None
        filename, media_type = ARTIFACTS[normalized]
        path = self._version_dir(session_id, version_id) / filename
        if not path.is_file():
            return None
        return ArtifactFile(path=path, media_type=media_type, filename=filename)

    def _write_b64(
        self,
        *,
        session_id: str,
        version_id: str,
        artifact_name: str,
        data_b64: str,
    ) -> dict[str, Any]:
        filename, _ = ARTIFACTS[artifact_name]
        try:
            data = base64.b64decode(data_b64, validate=True)
        except binascii.Error as exc:
            raise ValueError(f"invalid base64 for artifact {artifact_name}") from exc
        if not data:
            raise ValueError(f"empty artifact {artifact_name}")
        version_dir = self._version_dir(session_id, version_id)
        version_dir.mkdir(parents=True, exist_ok=True)
        path = version_dir / filename
        path.write_bytes(data)
        return self._ref(session_id, version_id, artifact_name, path)

    def _ref(
        self,
        session_id: str,
        version_id: str,
        artifact_name: str,
        path: Path,
    ) -> dict[str, Any]:
        filename, media_type = ARTIFACTS[artifact_name]
        return {
            "name": artifact_name,
            "filename": filename,
            "mime_type": media_type,
            "bytes": path.stat().st_size,
            "url": (
                f"/api/sessions/{session_id}/versions/{version_id}"
                f"/artifacts/{artifact_name}"
            ),
        }

    def _version_dir(self, session_id: str, version_id: str) -> Path:
        return self.root / _safe_id(session_id) / _safe_id(version_id)


def _safe_id(value: str) -> str:
    safe = "".join(ch for ch in value if ch.isalnum() or ch in {"-", "_"})
    if not safe:
        raise ValueError("empty artifact identifier")
    return safe
