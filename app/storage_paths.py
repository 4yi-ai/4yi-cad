"""Runtime storage path helpers."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def writable_platform_data_dir() -> str | None:
    raw = os.environ.get("CAD_DATA_DIR", "").strip()
    if not raw:
        return None

    path = Path(raw)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    try:
        fd, probe = tempfile.mkstemp(prefix=".write-test-", dir=str(path))
        os.close(fd)
        os.unlink(probe)
    except OSError:
        return None
    return str(path)
