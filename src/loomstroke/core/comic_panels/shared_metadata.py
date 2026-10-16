"""Shared display references in an existing group's optional metadata.

The layer's own ID/transform/pixels remain the single source authority.
No render instance or second persisted geometry is introduced here.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

from .models import PanelFrameError, SharedContent

SHARED_NAMESPACE = "loomstroke.comictools.shared_content"


def _payload(metadata: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if SHARED_NAMESPACE not in metadata:
        return None
    payload = metadata[SHARED_NAMESPACE]
    if (
        not isinstance(payload, Mapping)
        or type(payload.get("version")) is not int
        or payload["version"] != 1
    ):
        raise PanelFrameError("unsupported shared content metadata")
    return payload


def read_shared_content(
    metadata: Mapping[str, Any] | None, *, group_id: str
) -> SharedContent | None:
    payload = _payload(metadata or {})
    if payload is None:
        return None
    page_id, targets = payload.get("page_id"), payload.get("panel_ids")
    if (
        not isinstance(page_id, str)
        or not isinstance(targets, (list, tuple))
        or any(not isinstance(target, str) for target in targets)
    ):
        raise PanelFrameError("invalid shared display references")
    return SharedContent(page_id, group_id, tuple(targets))


def write_shared_content(
    metadata: Mapping[str, Any] | None, shared: SharedContent
) -> dict[str, Any]:
    result = copy.deepcopy(dict(metadata or {}))
    payload = dict(_payload(result) or {})
    payload.update(version=1, page_id=shared.page_id, panel_ids=list(shared.panel_ids))
    result[SHARED_NAMESPACE] = payload
    return result


def remove_shared_target(shared: SharedContent, panel_id: str) -> SharedContent | None:
    """Detach a view; return None for the last view, without deleting the source.

    The later Command decides the explicit source disposition. This planner
    never transfers ownership or replaces source pixels with independent copies.
    """
    if panel_id not in shared.panel_ids:
        raise PanelFrameError("shared display target does not exist")
    targets = tuple(p for p in shared.panel_ids if p != panel_id)
    return SharedContent(shared.page_id, shared.group_id, targets) if targets else None
