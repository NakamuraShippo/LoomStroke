"""Utilities for converting UI layout snapshots to/from serialisable payloads."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..host_api import PanelLayout, UILayoutSnapshot

__all__ = ["payload_to_snapshot", "snapshot_to_payload"]


def snapshot_to_payload(snapshot: UILayoutSnapshot) -> dict[str, Any]:
    """Convert a :class:`UILayoutSnapshot` into a JSON-serialisable dict."""

    return {
        "window_state": snapshot.window_state,
        "window_geometry": snapshot.window_geometry,
        "panels": [
            {
                "id": panel.panel_id,
                "geometry": [int(value) for value in panel.geometry],
                "dock_area": panel.dock_area,
                "floating": panel.floating,
                "visible": panel.visible,
                "z_index": panel.z_index,
                "metadata": dict(panel.metadata or {}),
            }
            for panel in snapshot.panels
        ],
    }


def payload_to_snapshot(payload: Mapping[str, Any]) -> UILayoutSnapshot:
    """Convert a mapping produced by :func:`snapshot_to_payload` back to a snapshot."""

    panels: list[PanelLayout] = []
    raw_panels = payload.get("panels", [])
    if isinstance(raw_panels, Sequence):
        for item in raw_panels:
            if not isinstance(item, Mapping):
                continue
            geometry_raw = item.get("geometry", (0, 0, 0, 0))
            geometry = _coerce_geometry(geometry_raw)
            panels.append(
                PanelLayout(
                    panel_id=str(item.get("id", "")),
                    geometry=geometry,
                    dock_area=item.get("dock_area"),
                    floating=bool(item.get("floating", False)),
                    visible=bool(item.get("visible", True)),
                    z_index=int(item.get("z_index", 0)),
                    metadata=dict(item.get("metadata") or {}),
                )
            )

    return UILayoutSnapshot(
        window_state=payload.get("window_state"),
        window_geometry=payload.get("window_geometry"),
        panels=tuple(panels),
    )


def _coerce_geometry(raw: Any) -> tuple[int, int, int, int]:
    values: list[int] = []
    if isinstance(raw, Sequence):
        for value in list(raw)[:4]:
            try:
                values.append(int(value))
            except (TypeError, ValueError):
                values.append(0)
    while len(values) < 4:
        values.append(0)
    return values[0], values[1], values[2], values[3]
