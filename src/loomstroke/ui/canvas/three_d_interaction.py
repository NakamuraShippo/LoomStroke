"""Direct canvas interaction for editable 3D layers."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import Qt

from loomstroke.core.assets_3d.scene import Scene3DDocument
from loomstroke.core.commands import SetLayer3DCameraCommand, SetLayer3DOffsetCommand
from loomstroke.core.layer_3d_rendering import orbit_camera_state
from loomstroke.rendering_3d import OrbitCameraState

logger = logging.getLogger(__name__)

_ROTATE_DEGREES_PER_PIXEL = 0.35
_PITCH_DEGREES_PER_PIXEL = 0.25
_WHEEL_ZOOM_STEP = 1.12


@dataclass(slots=True)
class _InteractionState:
    layer: Any
    mode: str
    start_widget: tuple[float, float]
    start_canvas: tuple[float, float]
    start_camera: OrbitCameraState
    start_offset: tuple[int, int]
    changed: bool = False


class Canvas3DLayerInteractionController:
    """Handle 3D layer orbit, zoom, and placement from the canvas surface."""

    def __init__(self, canvas_widget: Any) -> None:
        self._widget = canvas_widget
        self._state: _InteractionState | None = None
        self._drag_mode = "orbit"

    def handle_mouse_press(self, event: Any) -> bool:
        if self._state is not None:
            event.accept()
            return True
        layer = self._active_3d_layer()
        if layer is None:
            return False
        widget_pos = _event_widget_pos(event)
        canvas_pos = self._widget.viewport.widget_to_canvas(*widget_pos)
        if not self._is_inside_canvas(canvas_pos):
            return False
        button = event.button()
        modifiers = event.modifiers()
        if button == Qt.MouseButton.LeftButton:
            mode = (
                "move"
                if _has_modifier(modifiers, Qt.KeyboardModifier.ShiftModifier)
                else self._drag_mode
            )
        elif button == Qt.MouseButton.MiddleButton:
            mode = "move"
        else:
            return False
        scene = getattr(layer, "scene_3d", None)
        if not isinstance(scene, Scene3DDocument):
            return False
        self._state = _InteractionState(
            layer=layer,
            mode=mode,
            start_widget=widget_pos,
            start_canvas=(float(canvas_pos[0]), float(canvas_pos[1])),
            start_camera=orbit_camera_state(getattr(layer, "camera_3d", None), scene),
            start_offset=(
                int(getattr(layer, "offset_x", 0)),
                int(getattr(layer, "offset_y", 0)),
            ),
        )
        event.accept()
        return True

    def handle_mouse_move(self, event: Any) -> bool:
        state = self._state
        if state is None:
            return False
        widget_pos = _event_widget_pos(event)
        if state.mode == "orbit":
            dx = widget_pos[0] - state.start_widget[0]
            dy = widget_pos[1] - state.start_widget[1]
            camera = state.start_camera.rotated(
                yaw_delta=dx * _ROTATE_DEGREES_PER_PIXEL,
                pitch_delta=-dy * _PITCH_DEGREES_PER_PIXEL,
            )
            self._apply_camera(state.layer, camera)
        else:
            canvas_pos = self._widget.viewport.widget_to_canvas(*widget_pos)
            dx = float(canvas_pos[0]) - state.start_canvas[0]
            dy = float(canvas_pos[1]) - state.start_canvas[1]
            self._apply_offset(
                state.layer,
                (
                    round(state.start_offset[0] + dx),
                    round(state.start_offset[1] + dy),
                ),
            )
        state.changed = True
        event.accept()
        return True

    def handle_mouse_release(self, event: Any) -> bool:
        state = self._state
        if state is None:
            return False
        self._state = None
        if state.changed:
            if state.mode == "orbit":
                self._commit_camera(state)
            else:
                self._commit_offset(state)
        event.accept()
        return True

    def handle_wheel(self, event: Any) -> bool:
        layer = self._active_3d_layer()
        if layer is None:
            return False
        widget_pos = _event_widget_pos(event)
        canvas_pos = self._widget.viewport.widget_to_canvas(*widget_pos)
        if not self._is_inside_canvas(canvas_pos):
            return False
        scene = getattr(layer, "scene_3d", None)
        if not isinstance(scene, Scene3DDocument):
            return False
        delta = float(event.angleDelta().y()) / 120.0
        if delta == 0.0:
            return False
        factor = _WHEEL_ZOOM_STEP**delta
        start_camera = orbit_camera_state(getattr(layer, "camera_3d", None), scene)
        camera = start_camera.zoomed(factor)
        self._apply_camera(layer, camera)
        self._submit(
            SetLayer3DCameraCommand(
                str(getattr(layer, "id", "")),
                _camera_payload(camera),
                old_camera=_camera_payload(start_camera),
                merge_key="3d-layer-wheel-zoom",
            )
        )
        event.accept()
        return True

    def is_active(self) -> bool:
        return self._state is not None

    def drag_mode(self) -> str:
        return self._drag_mode

    def set_drag_mode(self, mode: str) -> bool:
        token = str(mode or "").strip().lower()
        if token not in {"orbit", "move"}:
            return False
        self._drag_mode = token
        return True

    def cancel(self) -> None:
        state = self._state
        self._state = None
        if state is None:
            return
        if state.mode == "orbit":
            self._apply_camera(state.layer, state.start_camera)
        else:
            self._apply_offset(state.layer, state.start_offset)

    def _commit_camera(self, state: _InteractionState) -> None:
        camera = orbit_camera_state(
            getattr(state.layer, "camera_3d", None),
            getattr(state.layer, "scene_3d", None),
        )
        if _camera_payload(camera) == _camera_payload(state.start_camera):
            return
        self._submit(
            SetLayer3DCameraCommand(
                str(getattr(state.layer, "id", "")),
                _camera_payload(camera),
                old_camera=_camera_payload(state.start_camera),
                merge_key="3d-layer-drag-camera",
            )
        )

    def _commit_offset(self, state: _InteractionState) -> None:
        current = (
            int(getattr(state.layer, "offset_x", 0)),
            int(getattr(state.layer, "offset_y", 0)),
        )
        if current == state.start_offset:
            return
        self._submit(
            SetLayer3DOffsetCommand(
                str(getattr(state.layer, "id", "")),
                current,
                old_offset=state.start_offset,
                merge_key="3d-layer-drag-offset",
            )
        )

    def _apply_camera(self, layer: Any, camera: OrbitCameraState) -> None:
        layer.camera_3d = _camera_payload(camera)
        self._refresh_layer(layer)

    def _apply_offset(self, layer: Any, offset: tuple[int, int]) -> None:
        layer.offset_x, layer.offset_y = offset
        self._refresh_layer(layer)

    def _refresh_layer(self, layer: Any) -> None:
        canvas = self._canvas_service()
        layer.texture_version = int(getattr(layer, "texture_version", 0)) + 1
        invalidate = getattr(canvas, "_invalidate_layer_cache", None)
        if callable(invalidate):
            invalidate()
        recompose = getattr(canvas, "_recompose", None)
        if callable(recompose):
            recompose()
        self._widget.update()

    def _submit(self, command: Any) -> bool:
        canvas = self._canvas_service()
        submit = getattr(canvas, "submit", None)
        try:
            return bool(submit(command)) if callable(submit) else bool(command.apply(canvas))
        except Exception:
            logger.warning("3D canvas interaction commit failed", exc_info=True)
            return False

    def _active_3d_layer(self) -> Any | None:
        layer = self._active_layer()
        if layer is None or str(getattr(layer, "layer_type", "")) != "3d":
            return None
        if bool(getattr(layer, "locked", False)):
            return None
        if not isinstance(getattr(layer, "scene_3d", None), Scene3DDocument):
            return None
        return layer

    def _active_layer(self) -> Any | None:
        canvas = self._canvas_service()
        active = getattr(canvas, "active_layer", None)
        if active is not None and not callable(active):
            return active
        if callable(active):
            return active()
        getter = getattr(canvas, "_get_active_layer", None) or getattr(
            canvas,
            "get_active_layer",
            None,
        )
        if callable(getter):
            return getter()
        layers = getattr(canvas, "_layers", None)
        index = int(getattr(canvas, "_active_index", -1))
        if isinstance(layers, list) and 0 <= index < len(layers):
            return layers[index]
        return None

    def _canvas_service(self) -> Any:
        getter = getattr(self._widget, "_get_canvas_service", None)
        if callable(getter):
            canvas = getter()
            if canvas is not None:
                return canvas
        host = getattr(self._widget, "_host_api", None)
        canvas = getattr(host, "canvas", None)
        if canvas is not None:
            return canvas
        return self._widget

    def _is_inside_canvas(self, canvas_pos: tuple[float, float]) -> bool:
        width = int(getattr(getattr(self._widget, "state", None), "width", 0))
        height = int(getattr(getattr(self._widget, "state", None), "height", 0))
        if width <= 0 or height <= 0:
            size_fn = getattr(self._canvas_service(), "get_canvas_size", None)
            if callable(size_fn):
                width, height = (int(value) for value in size_fn())
        return 0.0 <= canvas_pos[0] <= float(width) and 0.0 <= canvas_pos[1] <= float(height)


def _event_widget_pos(event: Any) -> tuple[float, float]:
    pos = event.position()
    return float(pos.x()), float(pos.y())


def _has_modifier(modifiers: Any, modifier: Qt.KeyboardModifier) -> bool:
    return bool(modifiers & modifier)


def _camera_payload(camera: OrbitCameraState) -> dict[str, Any]:
    return {
        "yaw_degrees": float(camera.yaw_degrees),
        "pitch_degrees": float(camera.pitch_degrees),
        "zoom": float(camera.zoom),
        "target": list(camera.target),
        "distance": float(camera.distance),
    }
