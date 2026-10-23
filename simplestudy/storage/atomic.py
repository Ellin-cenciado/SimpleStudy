"""Small filesystem helpers shared by the storage modules."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def write_text_atomic(path: Path, payload: str) -> None:
    """Write ``payload`` to ``path`` atomically with an fsync.

    A crash mid-write can never leave a truncated or corrupt file behind: the
    data is written to a temporary file in the same directory, flushed, and
    then moved into place with :func:`os.replace`.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.stem}-", suffix=".tmp"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
