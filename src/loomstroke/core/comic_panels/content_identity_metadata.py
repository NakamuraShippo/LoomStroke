"""Remap whole-unit and raster part identities, preserving unknown metadata."""

import copy

from .membership import UNIT_KEY
from .models import PanelFrameError

RASTER_NAMESPACE = "loomstroke.comictools.balloon_raster"


def remap_content_identity(metadata, balloon_ids):
    result = copy.deepcopy(metadata or {})
    for payload, version_key in (
        (result.get(UNIT_KEY), "version"),
        ((result.get("plugins") or {}).get(RASTER_NAMESPACE), "schema_version"),
    ):
        if payload is None:
            continue
        if (
            not isinstance(payload, dict)
            or type(payload.get(version_key)) is not int
            or payload[version_key] != 1
        ):
            raise PanelFrameError("unsupported copied balloon identity metadata")
        old = payload.get("balloon_id")
        if not isinstance(old, str) or old not in balloon_ids:
            raise PanelFrameError("copied balloon unit has an unresolved identity")
        payload["balloon_id"] = balloon_ids[old]
    return result
