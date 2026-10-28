"""Commit one selection-derived PNG before registering its brush swatch."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .brush_store import BrushStore
from .brush_tip_png import _commit_png, _file_state, _FileContents, _read_owned, _require_absent

logger = logging.getLogger(__name__)


def _require_unreferenced(store: BrushStore, db: Any, swatch_id: str, destination: Path) -> None:
    if not isinstance(db, dict) or not isinstance(db.get("swatches", []), list):
        raise ValueError("Selection tip brush database is not a swatch collection")
    for existing in db.get("swatches", []):
        if not isinstance(existing, dict):
            raise ValueError("Selection tip brush database contains an unknown entry")
        if existing.get("id") == swatch_id:
            raise ValueError("Selection tip swatch id already exists")
        tip_path = existing.get("tip_path")
        if isinstance(tip_path, str) and store.resolve_tip_path(tip_path) == destination.resolve():
            raise ValueError("Selection tip destination is already referenced")


def _reclaim_unregistered(
    store: BrushStore,
    destination: Path,
    swatch_id: str,
    before: _FileContents,
    png: _FileContents,
) -> None:
    try:
        if _read_owned(store.db_path) != before:
            raise OSError("Selection tip registration status changed")
        _require_unreferenced(store, store.load_db(), swatch_id, destination)
        if _read_owned(store.db_path) != before or _read_owned(destination) != png:
            raise OSError("Selection tip cleanup ownership changed")
        # The memory/query and file reads above may invoke injected callbacks.
        # Recheck both files before removing only our still-unused PNG.
        if _read_owned(store.db_path) != before or _file_state(destination) != png.state:
            raise OSError("Selection tip cleanup evidence changed")
        destination.unlink()
    except Exception:
        logger.warning(
            "Selection tip PNG retained because its unused ownership could not be confirmed"
        )


def commit_selection_tip(
    store: BrushStore, image: Any, filename: str, swatch: dict[str, Any]
) -> None:
    """Keep registration/UI separate from the owned PNG's failure cleanup.

    Keep the existing swatch schema, relative path and None-returning add API.
    Unknown database/PNG state is preserved. Final check/rename and check/unlink
    pairs do not exclude another process and do not promise crash durability.
    """
    destination = store.brush_tips_dir / filename
    swatch_id = swatch["id"]
    cached = store.load_db()
    _require_unreferenced(store, cached, swatch_id, destination)
    before = _read_owned(store.db_path)
    current = json.loads(before.raw.decode("utf-8"))
    _require_unreferenced(store, current, swatch_id, destination)
    # Compare the existing JSON meaning, including its tuple/nonfinite wire
    # normalization, rather than NaN-sensitive Python equality or whitespace.
    if json.dumps(cached, sort_keys=True) != json.dumps(current, sort_keys=True):
        raise OSError("Selection tip brush database changed outside this store")
    _require_absent(destination)
    png = _commit_png(destination, image)
    try:
        if _read_owned(store.db_path) != before or _read_owned(destination) != png:
            raise OSError("Selection tip inputs changed before registration")
        # A successful BrushStore registration returns None, not a bool.
        store.add_swatch(swatch)
    except Exception:
        _reclaim_unregistered(store, destination, swatch_id, before, png)
        raise
