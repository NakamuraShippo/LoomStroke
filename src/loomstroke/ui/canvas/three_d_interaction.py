"""Direct canvas interaction for editable 3D layers."""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any

from PySide6.QtCore import Qt

from loomstroke.core.assets_3d.scene import Scene3DDocument
from loomstroke.core.commands import (
    SetLayer3DCameraCommand,
    SetLayer3DLightPositionCommand,
)
from loomstroke.core.drawing_figures.projection import screen_delta_to_world
from loomstroke.core.layer_3d_light import light_position
from loomstroke.core.layer_3d_rendering import orbit_camera_state
from loomstroke.rendering_3d import OrbitCameraState

from . import three_d_light_handle
from .drawing_figure_interaction import DrawingFigureInteraction
from .guide_canvas_bridge import update_guide_overlay

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
    start_light: tuple[float, float, float]
    start_lighting: Any
    light_drag_origin: tuple[float, float] | None = None
    light_proxy_direction: tuple[float, float] | None = None
    light_drag_axis: int | None = None
    changed: bool = False


class Canvas3DLayerInteractionController:
    """Handle 3D layer orbit, zoom, and placement from the canvas surface."""

    def __init__(self, canvas_widget: Any) -> None:
        self._widget = canvas_widget
        self._state: _InteractionState | None = None
        self._drag_mode = "orbit"
        self.figure = DrawingFigureInteraction(self)

    def handle_mouse_press(self, event: Any) -> bool:  # noqa: PLR0911 - input gates
        if self.is_active():
            event.accept()
            return True
        self.figure.hover.clear()
        layer = self._active_3d_layer()
        if layer is None:
            return False
        widget_pos = _event_widget_pos(event)
        canvas_pos = self._widget.viewport.widget_to_canvas(*widget_pos)
        light_marker = None
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._drag_mode == "light"
            and not _has_modifier(event.modifiers(), Qt.KeyboardModifier.ShiftModifier)
        ):
            light_marker = three_d_light_handle.placement(layer, self._widget)
        light_target = (
            three_d_light_handle.target_at(layer, self._widget, widget_pos, light_marker)
            if light_marker is not None
            else None
        )
        light_hit = light_target is not None
        if not self._is_inside_canvas(canvas_pos) and not light_hit:
            return False
        button = event.button()
        modifiers = event.modifiers()
        if button == Qt.MouseButton.LeftButton:
            mode = (
                "move"
                if _has_modifier(modifiers, Qt.KeyboardModifier.ShiftModifier)
                else self._drag_mode
            )
            if mode == "light" and not light_hit:
                mode = "orbit"
            if mode == "pose" or (
                mode == "orbit" and self._drag_mode == "orbit" and self.figure.hand_editor_active
            ):
                if self.figure.press(event):
                    return True
                mode = "orbit"
        elif button == Qt.MouseButton.MiddleButton:
            mode = "move"
        else:
            return False
        self._state = _InteractionState(
            layer=layer,
            mode=mode,
            start_widget=widget_pos,
            start_canvas=(float(canvas_pos[0]), float(canvas_pos[1])),
            start_camera=orbit_camera_state(getattr(layer, "camera_3d", None), layer.scene_3d),
            start_light=light_position(layer),
            start_lighting=getattr(layer, "lighting_3d", None),
            light_drag_origin=(
                light_marker.light_canvas
                if mode == "light" and light_target == "proxy" and light_marker is not None
                else None
            ),
            light_proxy_direction=(
                _proxy_direction(light_marker, self._widget)
                if mode == "light" and light_target == "proxy" and light_marker is not None
                else None
            ),
            light_drag_axis=(
                "xyz".index(light_target)
                if mode == "light" and light_target in {"x", "y", "z"}
                else None
            ),
        )
        event.accept()
        return True

    def handle_mouse_move(self, event: Any) -> bool:
        if self.figure.state is not None:
            return self.figure.move(event)
        state = self._state
        if state is None:
            return self.figure.hover.move(event)
        if self._active_3d_layer() is not state.layer:
            self.cancel()
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
        elif state.mode == "light":
            if state.light_proxy_direction is not None:
                dx = widget_pos[0] - state.start_widget[0]
                dy = widget_pos[1] - state.start_widget[1]
                along = dx * state.light_proxy_direction[0] + dy * state.light_proxy_direction[1]
                if abs(along) < 4.0:
                    event.accept()
                    return True
                widget_pos = (
                    state.start_widget[0] + along * state.light_proxy_direction[0],
                    state.start_widget[1] + along * state.light_proxy_direction[1],
                )
            canvas_pos = self._widget.viewport.widget_to_canvas(*widget_pos)
            origin = state.light_drag_origin or state.start_canvas
            delta = (
                float(canvas_pos[0]) - origin[0],
                float(canvas_pos[1]) - origin[1],
            )
            world_delta = screen_delta_to_world(
                delta,
                state.layer.scene_3d,
                state.start_camera,
                int(self._widget.state.width),
                int(self._widget.state.height),
            )
            position = list(state.start_light)
            if state.light_drag_axis is not None:
                axis = state.light_drag_axis
                rotation = state.start_camera.rotation_matrix()
                projected_sq = float(rotation[0, axis] ** 2 + rotation[1, axis] ** 2)
                position[axis] += float(world_delta[axis]) / projected_sq
            else:
                position = [
                    float(a + b) for a, b in zip(state.start_light, world_delta, strict=True)
                ]
            self._apply_light(
                state.layer,
                tuple(position),
            )
        else:
            canvas_pos = self._widget.viewport.widget_to_canvas(*widget_pos)
            world_delta = screen_delta_to_world(
                (
                    float(canvas_pos[0]) - state.start_canvas[0],
                    float(canvas_pos[1]) - state.start_canvas[1],
                ),
                state.layer.scene_3d,
                state.start_camera,
                int(self._widget.state.width),
                int(self._widget.state.height),
            )
            self._apply_camera(
                state.layer,
                replace(
                    state.start_camera,
                    target=tuple(
                        float(a - b)
                        for a, b in zip(state.start_camera.target, world_delta, strict=True)
                    ),
                ),
            )
        state.changed = True
        event.accept()
        return True

    def handle_mouse_release(self, event: Any) -> bool:
        if self.figure.state is not None:
            handled = self.figure.release(event)
            self.figure.hover.move(event)
            return handled
        state = self._state
        if state is None:
            return False
        if self._active_3d_layer() is not state.layer:
            self.cancel()
            return False
        self._state = None
        if state.changed:
            if state.mode in {"orbit", "move"}:
                self._commit_camera(state)
            elif state.mode == "light":
                self._commit_light(state)
        event.accept()
        if self._drag_mode == "pose":
            self.figure.hover.move(event)
        return True

    def handle_wheel(self, event: Any) -> bool:
        self.figure.cancel()
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
        return self._state is not None or self.figure.state is not None

    def draw_overlay(self, painter: Any) -> None:
        self.figure.draw(painter)
        if self._drag_mode == "light":
            layer = self._active_3d_layer()
            if layer is not None:
                three_d_light_handle.draw(painter, layer, self._widget)

    def drag_mode(self) -> str:
        return self._drag_mode

    def set_drag_mode(self, mode: str) -> bool:
        token = str(mode or "").strip().lower()
        if token not in {"orbit", "move", "pose", "light"}:
            return False
        self.cancel()
        self._drag_mode = token
        update_guide_overlay(self._widget)
        return True

    def cancel(self) -> None:
        self.figure.cancel()
        state = self._state
        self._state = None
        if state is None:
            return
        if state.mode in {"orbit", "move"}:
            self._apply_camera(state.layer, state.start_camera)
        elif state.mode == "light":
            self._restore_light(state)

    def cancel_if_invalid(self) -> None:
        if self._state is not None and self._active_3d_layer() is not self._state.layer:
            self.cancel()

    def _commit_light(self, state: _InteractionState) -> None:
        position = light_position(state.layer)
        if position == state.start_light:
            self._restore_light(state)
            return
        self._restore_light(state)
        if not self._submit(
            SetLayer3DLightPositionCommand(
                str(getattr(state.layer, "id", "")),
                position,
                merge_key="3d-layer-drag-light",
            )
        ):
            self._restore_light(state)

    def _restore_light(self, state: _InteractionState) -> None:
        state.layer.lighting_3d = state.start_lighting
        self._refresh_layer(state.layer)

    def _apply_light(self, layer: Any, position: tuple[float, float, float]) -> None:
        lighting = getattr(layer, "lighting_3d", None)
        layer.lighting_3d = {
            **(lighting if isinstance(lighting, dict) else {}),
            "position": list(position),
        }
        self._refresh_layer(layer)

    def _commit_camera(self, state: _InteractionState) -> None:
        camera = orbit_camera_state(
            getattr(state.layer, "camera_3d", None),
            getattr(state.layer, "scene_3d", None),
        )
        if _camera_payload(camera) == _camera_payload(state.start_camera):
            return
        submitted = self._submit(
            SetLayer3DCameraCommand(
                str(getattr(state.layer, "id", "")),
                _camera_payload(camera),
                old_camera=_camera_payload(state.start_camera),
                merge_key=("3d-layer-drag-pan" if state.mode == "move" else "3d-layer-drag-camera"),
            )
        )
        if not submitted and state.mode == "move":
            self._apply_camera(state.layer, state.start_camera)

    def _apply_camera(self, layer: Any, camera: OrbitCameraState) -> None:
        layer.camera_3d = _camera_payload(camera)
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
        if self._drag_mode in {"pose", "light"}:
            update_guide_overlay(self._widget)

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


def _proxy_direction(
    marker: three_d_light_handle.LightHandlePlacement, widget: Any
) -> tuple[float, float]:
    light_widget = widget.viewport.canvas_to_widget(*marker.light_canvas)
    dx = light_widget[0] - marker.widget[0]
    dy = light_widget[1] - marker.widget[1]
    length = (dx * dx + dy * dy) ** 0.5
    return dx / length, dy / length


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
