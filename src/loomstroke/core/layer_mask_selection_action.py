"""Commit a standard selection-to-mask action without changing low-level setters."""

from __future__ import annotations

import logging
from collections.abc import Callable
from inspect import getattr_static
from typing import Any, cast

from PySide6.QtGui import QImage

from .canvas_history_clear_continuation import _stored_state
from .canvas_layer_mask_service import _selection_to_layer_mask_image
from .commands_layer_lookup import find_layer
from .commands_layer_tree import SetLayerMaskCommand
from .document_capture_guard import is_document_capture_blocked
from .gradient_preview_boundary import close_gradient_preview_before_edit
from .layer_command_commit_context import _LayerCommandContext
from .layer_mask_submit import _standard_mask_route
from .layer_opacity_preview_boundary import _close_opacity_before_edit

logger = logging.getLogger(__name__)


def _supports_selection_mask(canvas: Any, command: SetLayerMaskCommand) -> bool:
    from .canvas_service_simple_command_api import _DEFAULT_RESOURCE_METADATA_HOOKS  # noqa: PLC0415
    from .canvas_service_simple_layer_api import _DEFAULT_SELECTION_TO_LAYER_MASK  # noqa: PLC0415

    return (
        _standard_mask_route(canvas, command)
        and getattr_static(canvas, "submit", None) is _DEFAULT_RESOURCE_METADATA_HOOKS["submit"]
        and getattr_static(canvas, "selection_to_layer_mask", None)
        is _DEFAULT_SELECTION_TO_LAYER_MASK
    )


def _selection_mask_action(  # noqa: PLR0911 - retain initial fallback versus superseded refusal
    canvas: Any, layer_id: str, *, caller_current: Callable[[], bool]
) -> bool | None:
    """Return refresh permission; None alone requests the original legacy call."""
    command = SetLayerMaskCommand(layer_id, QImage())
    if not _supports_selection_mask(canvas, command):
        return None
    try:
        layer = find_layer(canvas, layer_id)
        selection = _stored_state(canvas).get("_selection_mask")
        if layer is None or not isinstance(selection, QImage) or selection.isNull():
            return False
        scope = _LayerCommandContext(canvas, (layer,))
        selection_key = selection.cacheKey()
        dimensions = canvas._width, canvas._height

        def current() -> bool:
            return (
                scope.current()
                and caller_current()
                and scope.current()
                and _supports_selection_mask(canvas, command)
                and _stored_state(canvas).get("_selection_mask") is selection
                and cast(QImage, selection).cacheKey() == selection_key
                and (canvas._width, canvas._height) == dimensions
            )

        if not current():
            return False
        if not close_gradient_preview_before_edit(canvas) or not _close_opacity_before_edit(canvas):
            return False
        if not current() or is_document_capture_blocked(canvas):
            return False
        candidate = _selection_to_layer_mask_image(selection, *dimensions)
        if not current() or candidate.isNull():
            return False
        command = SetLayerMaskCommand(layer_id, candidate, mask_enabled=True)
        if not current():
            return False
        accepted = bool(canvas.submit(command))
        return accepted and current()
    except Exception:
        logger.exception("Selection-to-mask action failed")
        return False
