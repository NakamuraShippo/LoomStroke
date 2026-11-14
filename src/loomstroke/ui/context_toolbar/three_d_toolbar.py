"""Context toolbar controls for editable 3D layers."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

from PySide6.QtCore import QEvent, QPointF, QRect, Qt
from PySide6.QtGui import QIcon, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QDoubleSpinBox,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.assets_3d.scene import Scene3DDocument
from loomstroke.core.commands import (
    SetLayer3DCameraCommand,
    SetLayer3DDisplayModeCommand,
    SetLayer3DLightPositionCommand,
)
from loomstroke.core.drawing_figures.model import pose_from_layer
from loomstroke.core.events import Events
from loomstroke.core.layer_3d_light import light_position
from loomstroke.core.layer_3d_rendering import orbit_camera_state, preview_display_mode
from loomstroke.plugins.sdk.tool import ContextToolbarSpec
from loomstroke.rendering_3d import OrbitCameraState, PreviewDisplayMode
from loomstroke.ui.drawing_figure_add import figure_text
from loomstroke.ui.translations import translated_literal_domain

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
        editor = getattr(self, "_figure_hand_editor", None)
        if editor is not None:
            editor.sync()
        figure = getattr(self._interaction_controller(), "figure", None)
        if figure is not None:
            figure.cancel_if_invalid()
        interaction = self._interaction_controller()
        if interaction is not None:
            cancel_if_invalid = getattr(interaction, "cancel_if_invalid", None)
            if callable(cancel_if_invalid):
                cancel_if_invalid()
        layer = self._active_3d_layer()
        if layer is None:
            self._hide()
            return False
        if pose_from_layer(layer) is None and self.interaction_mode() == "pose":
            self.set_interaction_mode("orbit")
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

    def set_light_position(self, position: tuple[float, float, float]) -> bool:
        layer = self._active_3d_layer()
        if layer is None:
            return False
        command = SetLayer3DLightPositionCommand(
            str(layer.id), position, merge_key="3d-layer-light-position"
        )
        submit = getattr(self._canvas, "submit", None)
        try:
            submitted = (
                bool(submit(command)) if callable(submit) else bool(command.apply(self._canvas))
            )
        except Exception:
            logger.warning("3D light position update failed", exc_info=True)
            return False
        if submitted and self._widget is not None:
            self._widget.sync_from_layer(layer)
        return submitted

    def interaction_mode(self) -> str:
        controller = self._interaction_controller()
        getter = getattr(controller, "drag_mode", None)
        if callable(getter):
            mode = str(getter())
            if mode in {"orbit", "move", "pose", "light"}:
                self._interaction_mode = mode
        return self._interaction_mode

    def set_interaction_mode(self, mode: str) -> bool:
        token = str(mode or "").strip().lower()
        if token not in {"orbit", "move", "pose", "light"}:
            return False
        if token == "pose" and pose_from_layer(self._active_3d_layer()) is None:
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
        widget.destroyed.connect(lambda: self._forget_widget(widget))
        layer = self._active_3d_layer()
        if layer is not None:
            widget.sync_from_layer(layer)
        return widget

    def _forget_widget(self, widget: ThreeDLayerToolbarWidget) -> None:
        # Another provider rebuilds the shared toolbar with deleteLater().
        # An older pending deletion must not clear its newer replacement.
        if self._widget is widget:
            self._widget = None

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

    def __init__(  # noqa: PLR0915 - compact widget wiring
        self,
        controller: ThreeDLayerContextToolbarController,
        *,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._controller = controller
        self._syncing = False

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        layout = QHBoxLayout()
        outer.addLayout(layout)
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

        mode_container = QWidget(self)
        mode_layout = QHBoxLayout(mode_container)
        mode_layout.setContentsMargins(0, 0, 0, 0)
        mode_layout.setSpacing(0)
        layout.addWidget(mode_container)
        self._interaction_group = QButtonGroup(self)
        self._interaction_group.setExclusive(True)
        self._orbit_button = self._add_mode_button(
            mode_layout,
            _ts("Orbit"),
            _ts("Left drag rotates the 3D layer"),
            0,
        )
        self._move_button = self._add_mode_button(
            mode_layout,
            _ts("Move"),
            _ts("Move"),
            1,
        )
        self._light_button = self._add_mode_button(
            mode_layout,
            _ts("Light position"),
            _ts("Light position in 3D scene coordinates"),
            2,
        )
        self._pose_button = self._add_mode_button(
            mode_layout,
            figure_text("Edit pose"),
            figure_text("Edit pose"),
            3,
        )
        self._interaction_group.idClicked.connect(self._on_interaction_mode_changed)
        self.sync_interaction_mode(controller.interaction_mode())

        self._fit_button = self._add_button(
            layout, _ts("Fit"), _ts("Fit 3D view"), controller.fit_to_view
        )
        self._view_buttons: list[tuple[QToolButton, str]] = []
        for icon, tooltip, callback in (
            ("left", "Rotate left", lambda: controller.rotate(yaw_delta=-18.0)),
            ("right", "Rotate right", lambda: controller.rotate(yaw_delta=18.0)),
            ("down", "Tilt down", lambda: controller.rotate(pitch_delta=-12.0)),
            ("up", "Tilt up", lambda: controller.rotate(pitch_delta=12.0)),
            ("zoom_out", "Zoom out", lambda: controller.zoom(1.0 / 1.2)),
            ("zoom_in", "Zoom in", lambda: controller.zoom(1.2)),
        ):
            button = QToolButton(self)
            button.setAutoRaise(True)
            button.setFixedSize(28, 24)
            button.setIcon(_view_icon(self, icon))
            button.setToolTip(_ts(tooltip))
            button.clicked.connect(callback)
            layout.addWidget(button)
            self._view_buttons.append((button, tooltip))
        layout.addStretch(1)

        self._light_controls = QWidget(self)
        light_layout = QHBoxLayout(self._light_controls)
        light_layout.setContentsMargins(0, 0, 0, 0)
        light_layout.setSpacing(6)
        outer.addWidget(self._light_controls)
        self._light_label = QLabel(_ts("Light position"), self._light_controls)
        light_layout.addWidget(self._light_label)
        self._light_position: list[QDoubleSpinBox] = []
        for axis in "XYZ":
            light_layout.addWidget(QLabel(axis, self._light_controls))
            spin = QDoubleSpinBox(self._light_controls)
            spin.setRange(-10000.0, 10000.0)
            spin.setDecimals(2)
            spin.setSingleStep(0.1)
            spin.setKeyboardTracking(False)
            spin.setToolTip(_ts("Light position in 3D scene coordinates"))
            spin.valueChanged.connect(self._on_light_position_changed)
            light_layout.addWidget(spin)
            self._light_position.append(spin)
        light_layout.addStretch()
        from loomstroke.ui.drawing_figure_toolbar import DrawingFigureToolbarWidget  # noqa: PLC0415

        self._figure_controls = DrawingFigureToolbarWidget(controller, self)
        self._figure_controls.hide()
        outer.addWidget(self._figure_controls)
        self._openpose_button = QToolButton(self)
        self._openpose_button.setText("OpenPose")
        self._openpose_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._openpose_menu = QMenu(self._openpose_button)
        self._openpose_layer_action = self._openpose_menu.addAction(figure_text("OpenPose layer"))
        self._openpose_layer_action.triggered.connect(self._figure_controls.add_openpose_layer)
        self._openpose_json_action = self._openpose_menu.addAction(
            translated_literal_domain("DrawingFigure", "OpenPose JSON", use_fallback=True)
        )
        self._openpose_json_action.triggered.connect(
            lambda: self._figure_controls.export_pose("json")
        )
        self._openpose_button.setMenu(self._openpose_menu)
        layout.addWidget(self._openpose_button)
        self._sync_mode_rows(controller.interaction_mode())

    def sync_from_layer(self, layer: Any) -> None:
        has_pose = pose_from_layer(layer) is not None
        self._pose_button.setVisible(has_pose)
        self._pose_button.setEnabled(has_pose and not bool(getattr(layer, "locked", False)))
        self._openpose_button.setVisible(has_pose)
        self._openpose_button.setEnabled(has_pose and not bool(getattr(layer, "locked", False)))
        mode = preview_display_mode(getattr(layer, "display_mode_3d", "solid"))
        self._syncing = True
        try:
            position = light_position(layer)
            for spin, value in zip(self._light_position, position, strict=True):
                spin.setValue(value)
            for index in range(self._mode.count()):
                if str(self._mode.itemData(index)) == mode.value:
                    self._mode.setCurrentIndex(index)
                    break
            self._sync_interaction_mode_unchecked(self._controller.interaction_mode())
        finally:
            self._syncing = False
        self._sync_mode_rows(self._controller.interaction_mode(), layer)

    def sync_interaction_mode(self, mode: str) -> None:
        self._syncing = True
        try:
            self._sync_interaction_mode_unchecked(mode)
        finally:
            self._syncing = False
        if hasattr(self, "_figure_controls"):
            self._sync_mode_rows(mode)

    def _sync_mode_rows(self, mode: str, layer: Any | None = None) -> None:
        self._light_controls.setVisible(mode == "light")
        self._figure_controls.sync_from_layer(
            layer if layer is not None else self._controller._active_3d_layer()
        )

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

    def _on_light_position_changed(self, _value: float) -> None:
        if self._syncing:
            return
        self._controller.set_light_position(tuple(spin.value() for spin in self._light_position))

    def _on_interaction_mode_changed(self, button_id: int) -> None:
        if self._syncing:
            return
        self._controller.set_interaction_mode(
            {0: "orbit", 1: "move", 2: "light", 3: "pose"}[button_id]
        )

    def _sync_interaction_mode_unchecked(self, mode: str) -> None:
        token = str(mode).strip().lower()
        self._interaction_group.setExclusive(False)
        self._move_button.setChecked(token == "move")
        self._light_button.setChecked(token == "light")
        self._pose_button.setChecked(token == "pose")
        self._orbit_button.setChecked(token == "orbit")
        self._interaction_group.setExclusive(True)

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
        button.setStyleSheet(
            "QPushButton { border: 1px solid palette(mid); border-radius: 0; padding: 2px 6px; }"
            "QPushButton:checked { background: palette(highlight); "
            "color: palette(highlighted-text); }"
        )
        self._interaction_group.addButton(button, button_id)
        layout.addWidget(button)
        return button

    def _add_button(
        self,
        layout: QHBoxLayout,
        label: str,
        tooltip: str,
        callback: Any,
    ) -> QPushButton:
        button = QPushButton(label, self)
        button.setMinimumHeight(20)
        button.setMaximumHeight(24)
        button.setToolTip(tooltip)
        button.clicked.connect(lambda _checked=False: callback())
        layout.addWidget(button)
        return button

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "_view_buttons"):
            for button, source in self._view_buttons:
                if source in {"Zoom in", "Zoom out"}:
                    button.setIcon(
                        _view_icon(self, "zoom_in" if source == "Zoom in" else "zoom_out")
                    )
        if event.type() != QEvent.Type.LanguageChange or not hasattr(self, "_view_buttons"):
            return
        self._orbit_button.setText(_ts("Orbit"))
        self._move_button.setText(_ts("Move"))
        self._light_button.setText(_ts("Light position"))
        self._pose_button.setText(figure_text("Edit pose"))
        self._fit_button.setText(_ts("Fit"))
        self._light_label.setText(_ts("Light position"))
        for button, source in self._view_buttons:
            button.setToolTip(_ts(source))
        for spin in self._light_position:
            spin.setToolTip(_ts("Light position in 3D scene coordinates"))
        self._openpose_layer_action.setText(figure_text("OpenPose layer"))
        self._openpose_json_action.setText(
            translated_literal_domain("DrawingFigure", "OpenPose JSON", use_fallback=True)
        )
        for index, mode in enumerate(PreviewDisplayMode):
            self._mode.setItemText(index, _ts(_mode_label(mode)))


def _view_icon(widget: QWidget, kind: str) -> QIcon:
    arrows = {
        "left": QStyle.StandardPixmap.SP_ArrowLeft,
        "right": QStyle.StandardPixmap.SP_ArrowRight,
        "up": QStyle.StandardPixmap.SP_ArrowUp,
        "down": QStyle.StandardPixmap.SP_ArrowDown,
    }
    if kind in arrows:
        return widget.style().standardIcon(arrows[kind])
    pixmap = QPixmap(20, 20)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(widget.palette().windowText().color(), 1.8))
    painter.drawEllipse(QPointF(8, 8), 5, 5)
    painter.drawLine(QPointF(12, 12), QPointF(18, 18))
    painter.drawLine(QPointF(5, 8), QPointF(11, 8))
    if kind == "zoom_in":
        painter.drawLine(QPointF(8, 5), QPointF(8, 11))
    painter.end()
    return QIcon(pixmap)


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
