"""Register through Shape Manager with an atomic existing-format index update."""

from __future__ import annotations

import os  # noqa: F401 -- Preserve the existing os.replace fault-instrumentation namespace.
from copy import deepcopy

from .shape_library_registration_assets import _RegistrationAssets
from .types import McpStructuredError, ReasonCode


def register_shape(panel, obj, args):
    index = panel._index
    try:
        assets = _RegistrationAssets(index, panel._data_root)
        if not assets.target_current(panel._index, panel._data_root):
            raise OSError("The shape registration target changed during preparation")
    except Exception as exc:
        raise McpStructuredError(
            ReasonCode.BACKEND_FAILED, "The shape library index could not be committed."
        ) from exc
    record = panel._persist_shape_from_vector(
        args["name"],
        deepcopy(obj),
        category=args.get("category", "custom"),
        tags=tuple(args.get("tags", [])),
    )
    assets.observe(record)
    if not assets.target_current(panel._index, panel._data_root):
        # A replacement owner may already use the output; preserve its state
        # and the uncertain assets rather than applying or cleaning the old plan.
        raise McpStructuredError(
            ReasonCode.BACKEND_FAILED, "The shape library index could not be committed."
        )
    try:
        index.add(record)
    except Exception as exc:
        if assets.target_current(panel._index, panel._data_root):
            assets.reclaim_unused(record)
        raise McpStructuredError(
            ReasonCode.BACKEND_FAILED, "The shape library index could not be committed."
        ) from exc
    panel._refresh_categories()
    panel._refresh_tags()
    panel._refresh_list()
    return record
