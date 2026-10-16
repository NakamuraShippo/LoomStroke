"""Lazy blank-panel drawing target: create only with the first valid stroke."""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass

from PySide6.QtGui import QImage

from .membership import owner_for_parent
from .models import PanelFrameError
from .part_structure_command import PanelLayerInsertCommand


@dataclass
class PendingPanelDrawing:
    panel_id: str
    command: PanelLayerInsertCommand


def queue_panel_drawing(canvas, *, panel_id, parent_id, name="Rough drawing"):
    if getattr(canvas, "_in_transaction", False):
        raise PanelFrameError("finish the current stroke first")
    if owner_for_parent(canvas._layer_tree(), parent_id) != panel_id:
        raise PanelFrameError("blank drawing target does not belong to this panel")
    _validate_parent(canvas, parent_id)
    image = QImage(1, 1, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(0)
    command = PanelLayerInsertCommand(
        f"panel-rough-{uuid.uuid4().hex}",
        parent_id=parent_id,
        attributes={"name": name, "layer_type": "raster", "image": image},
    )
    from .stroke_upload import remember_blank_source  # noqa: PLC0415

    remember_blank_source(command._layer)
    canvas._pending_panel_drawing = PendingPanelDrawing(panel_id, command)
    return command.layer_id


def _validate_parent(canvas, parent_id):
    node = canvas._layer_tree().find_layer_by_id_tree(parent_id)
    seen = set()
    while node is not None:
        if node.id in seen or node.locked or not node.visible or node.mask_edit_mode:
            raise PanelFrameError("drawing target is locked, hidden or editing a mask")
        seen.add(node.id)
        parent_id = node.parent_id
        node = canvas._layer_tree().find_layer_by_id_tree(parent_id) if parent_id else None
        if parent_id and node is None:
            raise PanelFrameError("drawing target has a missing parent")


def cancel_pending_drawing(canvas):
    canvas._pending_panel_drawing = None


def pending_drawing_layer(canvas):
    pending = getattr(canvas, "_pending_panel_drawing", None)
    return pending.command._layer if pending else None


def consume_pending_drawing(  # noqa: PLR0911 - transient intent gates
    canvas, cmd, *, allow_new=True
):
    pending = getattr(canvas, "_pending_panel_drawing", None)
    if pending is None:
        return True
    if not allow_new:
        return False
    if not getattr(cmd, "points", None):
        return False
    brush = cmd.brush
    if (
        not all(math.isfinite(v) for p in cmd.points for v in (p.x, p.y, p.pressure))
        or not all(math.isfinite(v) and v > 0 for v in (brush.size, brush.opacity, brush.flow))
        or not any(p.pressure > 0 for p in cmd.points)
    ):
        return False
    try:
        _validate_parent(canvas, pending.command.parent_id)
    except PanelFrameError:
        return False
    # A tool's normal stroke transaction is the single history owner.
    if not getattr(canvas, "_in_transaction", False):
        raise PanelFrameError("first panel stroke requires a stroke transaction")
    command = pending.command
    canvas._pending_panel_drawing = None
    if not canvas.submit(command):
        canvas._pending_panel_drawing = pending
        return False
    canvas.set_active_layer(command.layer_id)
    from .stroke_upload import remember_blank_source  # noqa: PLC0415

    proof = getattr(command._layer, "_panel_blank_source", None)
    if proof is not None and proof[1] == command._layer.image.cacheKey():
        # Generic command submission may bump the fresh layer's version.
        # Rebind only while its original transparent source is unchanged.
        remember_blank_source(command._layer)
    return True
