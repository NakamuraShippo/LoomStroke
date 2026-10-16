"""Proposed frame geometry for the existing non-mutating MCP preview artifact."""

from PySide6.QtCore import QPointF
from PySide6.QtGui import QColor, QPainter, QPen, QPolygonF

from loomstroke.core.comic_panels.membership import PANEL_KEY, PART_KEY
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.plugins.comic_tools import layout_tree as lt
from loomstroke.plugins.comic_tools.model import LAYOUT_LAYER_NAMESPACE, PanelRecord
from loomstroke.plugins.comic_tools.panel_convex_geometry import (
    compute_convex_panels,
    has_inclined_cuts,
)
from loomstroke.plugins.comic_tools.panel_frame_layout_adapter import layout_snapshot
from loomstroke.plugins.comic_tools.service import ComicPageController

from .panel_frame_changeset import OPERATION, prepare_frame_operation


def _command_layout(cmd, layout, frames_layer_id):
    after = getattr(cmd, "after", {})
    metadata = after.get("metadata") or {}
    if "metadata" in after and getattr(cmd, "layer_id", None) == frames_layer_id:
        layout = lt.layout_from_payload(metadata.get("plugins", {}).get(LAYOUT_LAYER_NAMESPACE))
    for updates in (metadata, *getattr(cmd, "_layer_metadata_updates", {}).values()):
        payload = updates.get("plugins", {}).get(LAYOUT_LAYER_NAMESPACE)
        if payload:
            layout = lt.layout_from_payload(payload)
    return layout


def prepare_frame_preview(canvas, changeset, validation, *, asset_loader=None):
    operations = [op for op in changeset.get("operations", ()) if op.get("op") == OPERATION]
    if not operations:
        return None
    try:
        if len(operations) != 1 or len(changeset["operations"]) != 1:
            raise PanelFrameError("preview each panel frame edit in a separate changeset")
        images = {}
        if operations[0].get("action") == "place_image":
            if not callable(asset_loader):
                raise PanelFrameError("panel image asset loader is unavailable")
            asset_id = operations[0].get("assetId")
            images[asset_id] = asset_loader(asset_id)
        command = prepare_frame_operation(canvas, operations[0], asset_images=images)
        state = ComicPageController().read_state(canvas, page_size=canvas.get_canvas_size())
        records, layout = dict(state.panel_records), state.layout

        def walk(cmd):
            nonlocal layout
            after = getattr(cmd, "after", {})
            metadata = after.get("metadata") or {}
            layout = _command_layout(cmd, layout, state.frames_layer_id)
            if metadata.get(PART_KEY, {}).get("role") in {"restore", "retired_carrier"}:
                for pid, (source_id, _) in state.panel_sources.items():
                    if source_id == getattr(cmd, "layer_id", None):
                        records.pop(pid, None)
            objects = after.get("vector_objects", ())
            objects = (
                *objects,
                *(obj for _, obj in getattr(cmd, "_additions", ())),
                *(obj for _, obj in getattr(cmd, "_replacements", ())),
            )
            carrier = getattr(cmd, "_carrier", None)
            if carrier:
                objects = (*objects, carrier)
            for obj in objects:
                record = obj.meta.get(PANEL_KEY)
                if record:
                    records[record["panel_id"]] = PanelRecord.from_meta(record)
            for pid, (_, obj) in state.panel_sources.items():
                if obj.id in getattr(cmd, "_removals", ()):
                    records.pop(pid, None)
            for child in getattr(cmd, "commands", ()):
                walk(child)

        walk(command)
        size = canvas.get_canvas_size()
        if layout is None:
            panels = []
        elif has_inclined_cuts(layout.root):
            panels = [
                {"panelId": pid, "points": list(poly.points)}
                for pid, poly in compute_convex_panels(layout, size).items()
            ]
        else:
            panels = [
                {
                    "panelId": region.panel_id,
                    "points": [
                        (region.rect[0], region.rect[1]),
                        (region.rect[0] + region.rect[2], region.rect[1]),
                        (region.rect[0] + region.rect[2], region.rect[1] + region.rect[3]),
                        (region.rect[0], region.rect[1] + region.rect[3]),
                    ],
                }
                for region in layout_snapshot(layout, size, records).regions
            ]
        return {
            "action": operations[0]["action"],
            "panelId": operations[0].get("panelId"),
            "proposedPanels": panels,
            "canvasSize": list(size),
            "sourceUnchanged": True,
            "historyUnchanged": True,
        }
    except (PanelFrameError, ValueError, KeyError, TypeError) as exc:
        validation["status"] = "blocked"
        validation["errors"].append({"code": "invalid_panel_frame_preview", "message": str(exc)})
        return None


def draw_frame_preview(image, preview):
    proposal = preview.get("panelFramePreview")
    if not proposal:
        return
    width, height = proposal["canvasSize"]
    sx, sy = image.width() / width, image.height() / height
    painter = QPainter(image)
    try:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(30, 100, 220, 230), 1.5))
        for panel in proposal["proposedPanels"]:
            painter.drawPolygon(QPolygonF([QPointF(x * sx, y * sy) for x, y in panel["points"]]))
    finally:
        painter.end()
