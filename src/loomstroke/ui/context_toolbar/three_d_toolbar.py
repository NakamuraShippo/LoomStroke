"""Context toolbar controls for editable 3D layers."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

from PySide6.QtCore import QRect
from PySide6.QtWidgets import QButtonGroup, QComboBox, QHBoxLayout, QLabel, QPushButton, QWidget

from loomstroke.core.assets_3d.scene import Scene3DDocument
from loomstroke.core.commands import SetLayer3DCameraCommand, SetLayer3DDisplayModeCommand
from loomstroke.core.events import Events
from loomstroke.core.layer_3d_rendering import orbit_camera_state, preview_display_mode
from loomstroke.plugins.sdk.tool import ContextToolbarSpec
from loomstroke.rendering_3d import OrbitCameraState, PreviewDisplayMode

logger = logging.getLogger(__name__)

THREE_D_LAYER_CONTEXT_PROVIDER_ID = "loomstroke.3d_layer.context_toolbar"


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("ThreeDLayerContextToolbar", text, use_fallback=True)


class ThreeDLayerContextToolbarController:
    """Show canvas-local controls while the active layer is a 3D layer."""

    provider_id = THREE_D_LAYER_CONTEXT_PROVIDER_ID

    def __init__(self, manager: Any, canvas_service: Any, event_bus: Any) -> None:
        self._manager = manager
        self._canvas = canvas_service
        self._event_bus = event_bus
        self._canvas_widget = getattr(manager, "_canvas_widget", None)
        self._interaction_mode = "orbit"
        self._widget: ThreeDLayerToolbarWidget | None = None
        self._event_tokens: list[tuple[str, Any]] = []
        self._event_handlers = [self._on_layer_event]

        manager.register_external_spec(
            self.provider_id,
            ContextToolbarSpec(
                show_trigger="tool_active",
                anchor_mode="stroke_bbox",
                placement="anchor",
                mode="settings",
                build_widget_fn=self._build_widget,
            ),
            [],
        )
        self._subscribe()
        self.refresh()

    def refresh(self) -> bool:
        layer = self._active_3d_layer()
        if layer is None:
            self._hide()
            return False
        if self._widget is not None:
            self._widget.sync_from_layer(layer)
        self._manager.show_external(self.provider_id, self._anchor_rect(layer))
        if self._widget is not None:
            self._widget.sync_from_layer(layer)
        return True

    def fit_to_view(self) -> bool:
        layer = self._active_3d_layer()
        if layer is None:
            return False
        scene = getattr(layer, "scene_3d", None)
        if not isinstance(scene, Scene3DDocument):
            return False
        return self._submit_camera(layer, OrbitCameraState.fit_to_scene(scene))

    def rotate(self, *, yaw_delta: float = 0.0, pitch_delta: float = 0.0) -> bool:
        layer = self._active_3d_layer()
        if layer is None:
            return False
        scene = getattr(layer, "scene_3d", None)
        if not isinstance(scene, Scene3DDocument):
            return False
        camera = orbit_camera_state(getattr(layer, "camera_3d", None), scene)
        return self._submit_camera(
            layer,
            camera.rotated(yaw_delta=yaw_delta, pitch_delta=pitch_delta),
        )

    def zoom(self, factor: float) -> bool:
        layer = self._active_3d_layer()
        if layer is None:
            return False
        scene = getattr(layer, "scene_3d", None)
        if not isinstance(scene, Scene3DDocument):
            return False
        camera = orbit_camera_state(getattr(layer, "camera_3d", None), scene)
        return self._submit_camera(layer, camera.zoomed(factor))

    def set_display_mode(self, mode: PreviewDisplayMode) -> bool:
        layer = self._active_3d_layer()
        if layer is None:
            return False
        submit = getattr(self._canvas, "submit", None)
        command = SetLayer3DDisplayModeCommand(
            str(getattr(layer, "id", "")),
            mode.value,
            merge_key="3d-layer-display-mode",
        )
        try:
            submitted = (
                bool(submit(command)) if callable(submit) else bool(command.apply(self._canvas))
            )
        except Exception:
            logger.warning("3D display mode update failed", exc_info=True)
            return False
        if submitted and self._widget is not None:
            self._widget.sync_from_layer(layer)
        return submitted

    def interaction_mode(self) -> str:
        controller = self._interaction_controller()
        getter = getattr(controller, "drag_mode", None)
        if callable(getter):
            mode = str(getter())
            if mode in {"orbit", "move"}:
                self._interaction_mode = mode
        return self._interaction_mode

    def set_interaction_mode(self, mode: str) -> bool:
        token = str(mode or "").strip().lower()
        if token not in {"orbit", "move"}:
            return False
        self._interaction_mode = token
        controller = self._interaction_controller()
        setter = getattr(controller, "set_drag_mode", None)
        if callable(setter):
            with suppress(Exception):
                setter(token)
        if self._widget is not None:
            self._widget.sync_interaction_mode(token)
        return True

    def _build_widget(self, parent: QWidget, _on_changed: Any) -> QWidget:
        widget = ThreeDLayerToolbarWidget(self, parent=parent)
        self._widget = widget
        layer = self._active_3d_layer()
        if layer is not None:
            widget.sync_from_layer(layer)
        return widget

    def _subscribe(self) -> None:
        subscribe = getattr(self._event_bus, "on", None) or getattr(
            self._event_bus, "subscribe", None
        )
        if not callable(subscribe):
            return
        handler = self._event_handlers[0]
        for event_name in (Events.LAYER_ADDED, Events.LAYER_SELECTED, Events.LAYER_CHANGED):
            self._event_tokens.append((event_name, subscribe(event_name, handler)))

    def _on_layer_event(self, _payload: Any = None) -> None:
        self.refresh()

    def _active_3d_layer(self) -> Any | None:
        layer = self._active_layer()
        if layer is None or str(getattr(layer, "layer_type", "")) != "3d":
            return None
        if not isinstance(getattr(layer, "scene_3d", None), Scene3DDocument):
            return None
        return layer

    def _active_layer(self) -> Any | None:
        active = getattr(self._canvas, "active_layer", None)
        if active is not None and not callable(active):
            return active
        if callable(active):
            with suppress(Exception):
                return active()
        getter = getattr(self._canvas, "_get_active_layer", None) or getattr(
            self._canvas,
            "get_active_layer",
            None,
        )
        if callable(getter):
            with suppress(Exception):
                return getter()
        layers = getattr(self._canvas, "_layers", None)
        index = int(getattr(self._canvas, "_active_index", -1))
        if isinstance(layers, list) and 0 <= index < len(layers):
            return layers[index]
        return None

    def _interaction_controller(self) -> Any | None:
        return getattr(self._canvas_widget, "_three_d_interaction", None)

    def _submit_camera(self, layer: Any, camera: OrbitCameraState) -> bool:
        layer_id = str(getattr(layer, "id", "") or "")
        if not layer_id:
            return False
        submit = getattr(self._canvas, "submit", None)
        command = SetLayer3DCameraCommand(
            layer_id,
            _camera_payload(camera),
            merge_key="3d-layer-camera",
        )
        try:
            submitted = (
                bool(submit(command)) if callable(submit) else bool(command.apply(self._canvas))
            )
        except Exception:
            logger.warning("3D camera update failed", exc_info=True)
            return False
        if submitted and self._widget is not None:
            self._widget.sync_from_layer(layer)
        return submitted

    def _anchor_rect(self, layer: Any) -> QRect:
        bounds_fn = getattr(self._canvas, "get_layer_content_bounds", None)
        layer_id = str(getattr(layer, "id", "") or "")
        if callable(bounds_fn):
            with suppress(Exception):
                bounds = bounds_fn(layer_id or None)
                if bounds is not None:
                    x, y, w, h = bounds
                    if int(w) > 0 and int(h) > 0:
                        return QRect(int(x), int(y), int(w), int(h))
        size_fn = getattr(self._canvas, "get_canvas_size", None)
        if callable(size_fn):
            with suppress(Exception):
                width, height = size_fn()
                return QRect(0, 0, max(1, int(width)), max(1, int(height)))
        image = getattr(layer, "image", None)
        if image is not None:
            with suppress(Exception):
                return QRect(0, 0, max(1, int(image.width())), max(1, int(image.height())))
        return QRect(0, 0, 1, 1)

    def _hide(self) -> None:
        hide_external = getattr(self._manager, "hide_external", None)
        if callable(hide_external):
            hide_external(self.provider_id)
            return
        hide = getattr(self._manager, "hide", None)
        if callable(hide):
            hide()


class ThreeDLayerToolbarWidget(QWidget):
    """Compact controls for the active 3D layer."""

    def __init__(
        self,
        controller: ThreeDLayerContextToolbarController,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._syncing = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        label = QLabel(_ts("3D"), self)
        label.setStyleSheet("font-size: 11px; font-weight: 600;")
        layout.addWidget(label)

        self._mode = QComboBox(self)
        for mode in PreviewDisplayMode:
            self._mode.addItem(_ts(_mode_label(mode)), mode.value)
        self._mode.currentIndexChanged.connect(self._on_mode_changed)
        layout.addWidget(self._mode)

        self._interaction_group = QButtonGroup(self)
        self._interaction_group.setExclusive(True)
        self._orbit_button = self._add_mode_button(
            layout,
            _ts("Orbit"),
            _ts("Left drag rotates the 3D layer"),
            0,
        )
        self._move_button = self._add_mode_button(
            layout,
            _ts("Move"),
            _ts("Left drag moves the 3D layer on the canvas"),
            1,
        )
        self._interaction_group.idClicked.connect(self._on_interaction_mode_changed)
        self.sync_interaction_mode(controller.interaction_mode())

        self._add_button(layout, _ts("Fit"), _ts("Fit 3D view"), controller.fit_to_view)
        self._add_button(
            layout, "Yaw -", _ts("Rotate left"), lambda: controller.rotate(yaw_delta=-18.0)
        )
        self._add_button(
            layout, "Yaw +", _ts("Rotate right"), lambda: controller.rotate(yaw_delta=18.0)
        )
        self._add_button(
            layout,
            "Pitch -",
            _ts("Tilt down"),
            lambda: controller.rotate(pitch_delta=-12.0),
        )
        self._add_button(
            layout,
            "Pitch +",
            _ts("Tilt up"),
            lambda: controller.rotate(pitch_delta=12.0),
        )
        self._add_button(layout, "-", _ts("Zoom out"), lambda: controller.zoom(1.0 / 1.2))
        self._add_button(layout, "+", _ts("Zoom in"), lambda: controller.zoom(1.2))

    def sync_from_layer(self, layer: Any) -> None:
        mode = preview_display_mode(getattr(layer, "display_mode_3d", "solid"))
        self._syncing = True
        try:
            for index in range(self._mode.count()):
                if str(self._mode.itemData(index)) == mode.value:
                    self._mode.setCurrentIndex(index)
                    break
            self._sync_interaction_mode_unchecked(self._controller.interaction_mode())
        finally:
            self._syncing = False

    def sync_interaction_mode(self, mode: str) -> None:
        self._syncing = True
        try:
            self._sync_interaction_mode_unchecked(mode)
        finally:
            self._syncing = False

    def set_value(self, key: str, value: Any) -> None:
        if key == "display_mode":
            mode = preview_display_mode(value)
            self._syncing = True
            try:
                for index in range(self._mode.count()):
                    if str(self._mode.itemData(index)) == mode.value:
                        self._mode.setCurrentIndex(index)
                        break
            finally:
                self._syncing = False

    def _on_mode_changed(self, index: int) -> None:
        if self._syncing:
            return
        mode = PreviewDisplayMode(str(self._mode.itemData(index)))
        self._controller.set_display_mode(mode)

    def _on_interaction_mode_changed(self, button_id: int) -> None:
        if self._syncing:
            return
        self._controller.set_interaction_mode("move" if button_id == 1 else "orbit")

    def _sync_interaction_mode_unchecked(self, mode: str) -> None:
        if str(mode).strip().lower() == "move":
            self._move_button.setChecked(True)
        else:
            self._orbit_button.setChecked(True)

    def _add_mode_button(
        self,
        layout: QHBoxLayout,
        label: str,
        tooltip: str,
        button_id: int,
    ) -> QPushButton:
        button = QPushButton(label, self)
        button.setCheckable(True)
        button.setMinimumHeight(20)
        button.setMaximumHeight(24)
        button.setToolTip(tooltip)
        self._interaction_group.addButton(button, button_id)
        layout.addWidget(button)
        return button

    def _add_button(
        self,
        layout: QHBoxLayout,
        label: str,
        tooltip: str,
        callback: Any,
    ) -> None:
        button = QPushButton(label, self)
        button.setMinimumHeight(20)
        button.setMaximumHeight(24)
        button.setToolTip(tooltip)
        button.clicked.connect(lambda _checked=False: callback())
        layout.addWidget(button)


def _camera_payload(camera: OrbitCameraState) -> dict[str, Any]:
    return {
        "yaw_degrees": float(camera.yaw_degrees),
        "pitch_degrees": float(camera.pitch_degrees),
        "zoom": float(camera.zoom),
        "target": list(camera.target),
        "distance": float(camera.distance),
    }


def _mode_label(mode: PreviewDisplayMode) -> str:
    return {
        PreviewDisplayMode.SOLID: "Solid",
        PreviewDisplayMode.WIREFRAME: "Wireframe",
        PreviewDisplayMode.TEXTURED: "Textured",
        PreviewDisplayMode.NORMAL_DEBUG: "Normal Map",
    }[mode]
