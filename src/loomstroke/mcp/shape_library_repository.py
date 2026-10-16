"""Register through Shape Manager with an atomic existing-format index update."""

from __future__ import annotations

import os
import uuid
from copy import deepcopy

from loomstroke.plugins.shape_manager.model.shape_index import ShapeIndex

from .types import McpStructuredError, ReasonCode


def register_shape(panel, obj, args):
    index = panel._index
    record = panel._persist_shape_from_vector(
        args["name"],
        deepcopy(obj),
        category=args.get("category", "custom"),
        tags=tuple(args.get("tags", [])),
    )
    path = index.path.resolve()
    staging = path.with_name(f".{path.name}.mcp-{uuid.uuid4().hex}.tmp")
    try:
        staged = ShapeIndex(staging)
        staged.replace_all([*index.all(), record])
        os.replace(staging, path)
    except OSError as exc:
        # Only files owned by this new UUID record are eligible for cleanup.
        # No existing library record or arbitrary tree is removed.
        staging.unlink(missing_ok=True)
        root = panel._data_root.resolve()
        directory = root / "shapes" / record.shape_id
        if directory.resolve().is_relative_to(root / "shapes"):
            for relative in (record.svg_path, record.template_path, record.thumb_path):
                artifact = (root / relative).resolve()
                if artifact.parent == directory.resolve():
                    artifact.unlink(missing_ok=True)
            if directory.exists() and not any(directory.iterdir()):
                directory.rmdir()
        raise McpStructuredError(
            ReasonCode.BACKEND_FAILED, "The shape library index could not be committed."
        ) from exc
    index.load()
    panel._refresh_categories()
    panel._refresh_tags()
    panel._refresh_list()
    return record
