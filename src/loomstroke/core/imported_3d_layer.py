"""Commit prepared 3D scenes as an undoable layer creation."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from contextlib import nullcontext
from copy import deepcopy
from inspect import getattr_static
from typing import Any

from loomstroke.host_api import LayerRef

from .assets_3d.scene import Scene3DDocument
from .canvas_layer_model import _Layer
from .command_types import CompositeCommand
from .commands_layer_lookup import find_layer
from .commands_layer_tree import AddLayerCommand
from .document_capture_guard import is_document_capture_blocked
from .events import DocumentChangeEvent
from .layer_command_commit_context import _CommandHistoryReceipt, _LayerCommandContext
from .layer_tree_service import LayerCreationService

logger = logging.getLogger(__name__)


def _prepared_3d_layer_values(
    *,
    scene: Any,
    camera: Any = None,
    lighting: Any = None,
    display_mode: str = "solid",
    gen_3d: Any = None,
    origin: Any = None,
) -> dict[str, Any]:
    return {
        "scene_3d": scene,
        "camera_3d": deepcopy(camera) if isinstance(camera, dict) else camera,
        "lighting_3d": deepcopy(lighting) if isinstance(lighting, dict) else lighting,
        "display_mode_3d": str(display_mode or "solid"),
        "gen_3d": deepcopy(gen_3d) if isinstance(gen_3d, dict) else gen_3d,
        "origin": deepcopy(origin) if isinstance(origin, dict) else origin,
    }


def _prepare_generated_3d_output_layer(canvas: Any, name: str, **values: Any) -> _Layer | None:
    fields = _prepared_3d_layer_values(**values)
    if not isinstance(fields["scene_3d"], Scene3DDocument):
        return None
    layer = canvas._layer_tree()._prepare_layer_for_history(name, layer_type="3d")
    if type(layer) is not _Layer:
        return None
    for field, value in fields.items():
        setattr(layer, field, value)
    return layer


def _retained_scene_bytes(scene: Any) -> int:
    if not isinstance(scene, Scene3DDocument):
        return 0
    arrays = {
        id(array): array.nbytes
        for mesh in scene.meshes
        for primitive in mesh.primitives
        for array in (primitive.vertices, primitive.indices, primitive.normals, primitive.uvs)
        if array is not None
    }
    textures = {}
    for texture in scene.textures:
        data = texture.metadata.get("data")
        if isinstance(data, (bytes, bytearray, memoryview)):
            textures[id(data)] = memoryview(data).nbytes
    return sum(arrays.values()) + sum(textures.values())


class _AddPrepared3DLayerCommand(AddLayerCommand):
    def __init__(
        self,
        name: str,
        *,
        scene: Any,
        camera: Any = None,
        lighting: Any = None,
        display_mode: str = "solid",
        gen_3d: Any = None,
        origin: Any = None,
    ) -> None:
        super().__init__(name, layer_type="3d")
        self._values = _prepared_3d_layer_values(
            scene=scene,
            camera=camera,
            lighting=lighting,
            display_mode=display_mode,
            gen_3d=gen_3d,
            origin=origin,
        )

    @property
    def retained_bytes(self) -> int:
        return super().retained_bytes + _retained_scene_bytes(
            getattr(self._created.layer, "scene_3d", None)
        )

    def apply(self, canvas: Any) -> bool:
        previous = canvas.current_layer()
        if not self._created.create(canvas, self._name, "3d", self._values):
            return False
        self._values.clear()
        self._executed = True
        canvas._emit_layer_selected(previous, self._created.layer)
        return True

    def owns_layer(self, canvas: Any) -> bool:
        layer = self._created.layer
        return layer is not None and find_layer(canvas, layer.id) is layer

    @property
    def last_layer_id(self) -> str:
        return self._created.layer.id


class _AddPrepared3DLayersCommand(CompositeCommand):
    def __init__(self, layers: Sequence[_AddPrepared3DLayerCommand]) -> None:
        super().__init__(layers)
        self._layers = tuple(layers)

    def owns_layer(self, canvas: Any) -> bool:
        return all(layer.owns_layer(canvas) for layer in self._layers)

    @property
    def last_layer_id(self) -> str:
        return self._layers[-1].last_layer_id

    @property
    def retained_bytes(self) -> int:
        return sum(layer.retained_bytes for layer in self._layers)


_Prepared3DLayerCommand = _AddPrepared3DLayerCommand | _AddPrepared3DLayersCommand


def _supports_prepared_3d_layer_history(canvas: Any) -> bool:
    from .canvas_service_simple import SimpleCanvasService  # noqa: PLC0415
    from .canvas_service_simple_command_api import _DEFAULT_RESOURCE_METADATA_HOOKS  # noqa: PLC0415
    from .canvas_service_simple_layer_api import _DEFAULT_ADD_3D_LAYER  # noqa: PLC0415

    return (
        isinstance(canvas, SimpleCanvasService)
        and vars(canvas).get("_command_dispatcher") is not None
        and getattr_static(canvas, "add_3d_layer", None) is _DEFAULT_ADD_3D_LAYER
        and getattr_static(canvas, "submit", None) is _DEFAULT_RESOURCE_METADATA_HOOKS["submit"]
    )


def capture_generated_3d_layer_owner(canvas: Any) -> Callable[[], bool] | None:
    """Keep model preparation attached to the original standard canvas owner."""
    if not _supports_prepared_3d_layer_history(canvas):
        return None
    return _LayerCommandContext(canvas).current


def try_add_3d_layer_with_history(canvas: Any, name: str | None, **values: Any) -> LayerRef | None:
    """Create on the standard canvas, or return None for the caller's legacy path."""
    if not _supports_prepared_3d_layer_history(canvas):
        return None
    if any(layer.native_cmyk is not None for layer in canvas._flatten_layers()):
        raise ValueError("3D layers require an RGB document")
    default_index = sum(1 for layer in canvas._layers if layer.layer_type == "3d") + 1
    command = _AddPrepared3DLayerCommand(name or f"3D Layer {default_index}", **values)
    if not canvas.submit(command):
        raise RuntimeError("3D layer could not be added")
    return LayerCreationService.layer_ref(command._created.layer)


def add_generated_3d_layers_with_history(canvas: Any, layers: Sequence[dict[str, Any]]) -> int:
    if not layers or not _supports_prepared_3d_layer_history(canvas):
        return 0
    if any(layer.native_cmyk is not None for layer in canvas._flatten_layers()):
        raise ValueError("3D layers require an RGB document")
    commands = [_AddPrepared3DLayerCommand(**layer) for layer in layers]
    command = commands[0] if len(commands) == 1 else _AddPrepared3DLayersCommand(commands)
    return len(commands) if canvas.submit(command) else 0


def _record_prepared_layer(
    canvas: Any, command: _Prepared3DLayerCommand, history: _CommandHistoryReceipt
) -> bool:
    try:
        if not command.apply(canvas):
            return False
        if not history.scope.current() or not command.owns_layer(canvas):
            return False
        canvas._record_history_command("Add3DLayer", command)
        if not history.recorded(command) or not command.owns_layer(canvas):
            raise RuntimeError("3D layer history was not recorded")
        return True
    except Exception:
        if command.owns_layer(canvas) and history.can_rollback(command) and command.revert(canvas):
            history.restore()
        logger.exception("3D layer creation failed before history commitment")
        return False


def submit_prepared_3d_layer(canvas: Any, command: _Prepared3DLayerCommand) -> bool:
    if (
        is_document_capture_blocked(canvas)
        or canvas._in_transaction
        or getattr(canvas, "_panel_edit_events", None) is not None
    ):
        return False
    scope = _LayerCommandContext(canvas)
    history = _CommandHistoryReceipt(canvas, scope)
    notifier = getattr(scope.pages, "_history_state_notifier", None)
    batch = notifier.batch() if notifier is not None else nullcontext()
    capture = canvas._begin_transient_document_edit()
    events: list[tuple[Any, dict[str, Any]]] = []
    canvas._panel_edit_events = events
    canvas.defer_recompose()
    try:
        with batch:
            try:
                committed = _record_prepared_layer(canvas, command, history)
            finally:
                if canvas._panel_edit_events is events:
                    canvas._panel_edit_events = None
            if not committed:
                return False
            try:
                canvas._emit_doc_changed(
                    DocumentChangeEvent(
                        change_type="3d_layer_added", layer_id=command.last_layer_id
                    )
                )
                capture.release()
                for event_name, arguments in events:
                    if not scope.current() or not command.owns_layer(canvas):
                        break
                    canvas._publish_layer_event(event_name, **arguments)
            except Exception:
                logger.exception("3D layer notification failed after history commitment")
        return True
    finally:
        capture.release()
        try:
            canvas.resume_recompose()
        except Exception:
            logger.exception("3D layer recompose failed")
