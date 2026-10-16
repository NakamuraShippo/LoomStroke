"""Prepare existing fill commands from detached arrays without touching Qt on workers."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from numpy.typing import NDArray

from loomstroke.core.commands import UpdateSelectionMaskCommand
from loomstroke.core.fill.commands_fill import PatternFillCommand, _reference_array
from loomstroke.core.layer_tree_service import LayerTreeQueryService

from .editing_common import check_gui_thread, current_revision, invalid
from .types import McpStructuredError, ReasonCode

MAX_BUCKET_PIXELS = 1_048_576


@dataclass(frozen=True, slots=True)
class _LayerSnapshot:
    id: str
    buffer: NDArray[Any]
    offset_x: int
    offset_y: int


@dataclass(frozen=True, slots=True)
class _CanvasSnapshot:
    _width: int
    _height: int
    active_layer: _LayerSnapshot
    _selection_mask: NDArray[Any] | None
    _selection_bounds: tuple[int, int, int, int] | None

    def get_layer_by_id(self, layer_id: str) -> _LayerSnapshot | None:
        return self.active_layer if self.active_layer.id == layer_id else None


async def prepare_fill(service: Any, canvas: Any, layer: Any, command: Any) -> bool:
    """Reuse command span/selection caches, then recheck before submitting the command."""
    check_gui_thread()
    width, height = int(canvas._width), int(canvas._height)
    if width * height > MAX_BUCKET_PIXELS:
        raise invalid("Bucket fill canvas exceeds the 1,048,576-pixel limit.", "layerId")
    if layer.image.width() * layer.image.height() > MAX_BUCKET_PIXELS:
        raise invalid("Bucket fill reference exceeds the 1,048,576-pixel limit.", "layerId")
    before_revision = current_revision(service)
    reference = _reference_array(layer)
    if reference is None:
        raise invalid("The reference layer has no readable raster image.", "layerId")
    mask, bounds = UpdateSelectionMaskCommand._read_selection(canvas)
    mask_array = (
        UpdateSelectionMaskCommand._mask_to_numpy(mask, width, height).copy()
        if mask is not None
        else None
    )
    snapshot = _CanvasSnapshot(
        width,
        height,
        _LayerSnapshot(layer.id, reference, int(layer.offset_x), int(layer.offset_y)),
        mask_array,
        bounds,
    )
    prepared = await asyncio.to_thread(_prepare_command, command, snapshot)
    ensure_edit_unchanged(service, canvas, layer, before_revision)
    return prepared


def _prepare_command(command: Any, snapshot: _CanvasSnapshot) -> bool:
    # No Qt objects, live canvas, service, or layer are passed to this worker.
    prepared = bool(command._ensure_spans(snapshot))
    if prepared and isinstance(command, PatternFillCommand):
        command._pattern = command._scaled_pattern()
        command._pattern_scale = 1.0
    return prepared


def ensure_edit_unchanged(service: Any, canvas: Any, layer: Any, revision: str | None) -> None:
    from .editing_readiness import document_state  # noqa: PLC0415 - avoid tool registration cycle

    check_gui_thread()
    if (
        service.canvas_service is not canvas
        or document_state(service).get("present") is False
        or current_revision(service) != revision
        or getattr(canvas, "_in_transaction", False)
        or LayerTreeQueryService(canvas).find_layer_by_id_tree(layer.id) is not layer
    ):
        raise McpStructuredError(
            ReasonCode.CONFLICT, "The document changed during fill or gradient preparation."
        )
