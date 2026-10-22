"""Validate the optional, versioned path snapshot inside existing text metadata."""

import math
from collections.abc import Mapping


def path_settings(payload):  # noqa: PLR0911, PLR0912 - reject untrusted optional metadata
    raw = payload.get("path_text") if isinstance(payload, Mapping) else None
    if not isinstance(raw, Mapping) or raw.get("version") != 1:
        return None
    path = raw.get("path")
    if not isinstance(path, Mapping):
        return None
    anchors = path.get("anchors")
    if not isinstance(anchors, (tuple, list)) or not 2 <= len(anchors) <= 8192:
        return None
    try:
        for anchor in anchors:
            for key in ("pos", "h_in", "h_out"):
                value = anchor.get(key)
                if value is None and key != "pos":
                    continue
                if not isinstance(value, (tuple, list)) or len(value) != 2:
                    return None
                if not all(math.isfinite(float(v)) and abs(float(v)) <= 10000 for v in value):
                    return None
        for key, default in (("start", 0.0), ("end", 1.0), ("offset", 0.0)):
            if not math.isfinite(float(raw.get(key, default))):
                return None
        if not all(
            0 <= float(raw.get(key, default)) <= 1 for key, default in (("start", 0), ("end", 1))
        ):
            return None
        if abs(float(raw.get("offset", 0))) > 10000:
            return None
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None
    return raw
