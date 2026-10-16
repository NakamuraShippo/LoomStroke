"""Atomic destination writes for export (CT-09 Phase E1).

The invariant (spec 09 §12/§15): **an existing output file is never
damaged**.  The encoder writes to a sibling temp file; only a fully
flushed temp is moved over the destination (``os.replace`` — atomic on
the same volume).  Any failure — encode error, disk full, cancel —
leaves the previous file untouched and no temp behind.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

__all__ = ["write_atomically"]


def write_atomically(destination: Path, encode: Callable[[Path], None]) -> int:
    """Run ``encode(temp_path)`` and atomically move the result in place.

    Returns the number of bytes written.  Raises whatever ``encode``
    raises (after cleaning up the temp file); an empty temp is treated
    as a failed encode rather than replacing the destination with
    nothing.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temp_path = destination.with_name(destination.name + ".tmp")
    try:
        encode(temp_path)
        if not temp_path.exists() or temp_path.stat().st_size == 0:
            raise OSError(f"encoder produced no output for {destination.name}")
        size = temp_path.stat().st_size
        os.replace(temp_path, destination)
        return size
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise
