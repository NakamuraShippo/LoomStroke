"""Cancel known opacity previews at editing boundaries and acknowledge commits."""

from __future__ import annotations

from inspect import getattr_static
from typing import Any

from .canvas_history_clear_continuation import _stored_state
from .commands_layer_lookup import find_layer
from .commands_layer_opacity import SetLayerOpacityCommand
from .document_capture_guard import is_document_capture_blocked
from .gradient_preview_boundary import close_gradient_preview_before_edit
from .layer_command_commit_context import _LayerCommandContext
from .layer_opacity_preview_owner import _OpacityPreviewOwner


def _supports_opacity_gesture(canvas: Any) -> bool:
    from .canvas_service_simple_command_api import _DEFAULT_RESOURCE_METADATA_HOOKS  # noqa: PLC0415
    from .layer_opacity_submit import _standard_opacity_route  # noqa: PLC0415

    return getattr_static(canvas, "submit", None) is _DEFAULT_RESOURCE_METADATA_HOOKS[
        "submit"
    ] and _standard_opacity_route(canvas, SetLayerOpacityCommand("", 1.0))


def _begin_opacity_preview(canvas: Any, layer_id: str) -> _OpacityPreviewOwner | None:
    if not _supports_opacity_gesture(canvas):
        return None
    layer = find_layer(canvas, layer_id)
    if layer is None:
        return None
    scope = _LayerCommandContext(canvas, (layer,))
    active = canvas.current_layer().id
    if not close_gradient_preview_before_edit(canvas) or not _close_opacity_before_edit(canvas):
        return None
    if (
        not scope.current()
        or canvas.current_layer().id != active
        or layer.id != active
        or is_document_capture_blocked(canvas)
        or canvas._in_transaction
        or canvas._active_history_group is not None
        or _stored_state(canvas).get("_opacity_preview_owner") is not None
    ):
        return None
    owner = _OpacityPreviewOwner(canvas, layer, scope, active)
    lease = canvas._begin_transient_document_edit()
    owner.lease = lease
    canvas._opacity_preview_owner = owner
    if not scope.current() or canvas.current_layer().id != active:
        owner.phase = "recovered"
        owner.cancel()
        return None
    return owner


def _close_opacity_before_edit(canvas: Any, command: Any = None) -> bool:
    owner = _stored_state(canvas).get("_opacity_preview_owner")
    if not isinstance(owner, _OpacityPreviewOwner):
        return True
    if owner.phase == "committing":
        return owner.command is command
    context = _LayerCommandContext(canvas)
    active = canvas.current_layer().id
    if not owner.cancel():
        return False
    return (
        context.current()
        and canvas.current_layer().id == active
        and _stored_state(canvas).get("_opacity_preview_owner") is None
        and _stored_state(canvas).get("_gradient_preview_owner") is None
    )


def _opacity_command_owner(canvas: Any, command: Any) -> _OpacityPreviewOwner | None:
    owner = getattr(command, "_opacity_preview_owner", None)
    return (
        owner
        if (
            isinstance(owner, _OpacityPreviewOwner)
            and owner.canvas is canvas
            and owner.command is command
        )
        else None
    )
