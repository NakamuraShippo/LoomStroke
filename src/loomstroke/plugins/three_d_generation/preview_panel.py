"""Dockable 3D generated asset preview panel."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from contextlib import suppress
from typing import Any

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.assets_3d.material import MaterialAsset
from loomstroke.core.assets_3d.mesh import create_unit_cube_mesh
from loomstroke.core.assets_3d.scene import Scene3DDocument, Scene3DNode
from loomstroke.core.commands import SetLayer3DCameraCommand, SetLayer3DDisplayModeCommand
from loomstroke.core.events import Events
from loomstroke.core.layer_3d_rendering import orbit_camera_state, preview_display_mode
from loomstroke.host_api import HostAPI
from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.ui import PanelDescriptor, UIPanelPlugin
from loomstroke.rendering_3d import (
    OrbitCameraState,
    PreviewDisplayMode,
    ProjectionImportRequest,
    ProjectionImportService,
    SoftwarePreviewRenderer,
)
from loomstroke.rendering_3d.projection import (
    _PROJECTION_FACE_LIMIT_MESSAGE,
    _ProjectionFaceLimitExceeded,
)

logger = logging.getLogger(__name__)


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("ThreeDGeneratedPreviewPanel", text, use_fallback=True)


class Generated3DPreviewWidget(QWidget):
    """Preview and import widget for provider-neutral generated 3D assets."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host: HostAPI | None = None
        self._scene = _fallback_scene()
        self._camera = OrbitCameraState.fit_to_scene(self._scene)
        self._renderer = SoftwarePreviewRenderer()
        self._projection = ProjectionImportService(self._renderer)
        self._display_mode = PreviewDisplayMode.TEXTURED
        self._layer_id: str | None = None
        self._event_tokens: list[tuple[str, Any]] = []
        self._event_handlers: list[Any] = []
        self._updating_mode = False
        self._turntable = QTimer(self)
        self._turntable.setInterval(33)
        self._turntable.timeout.connect(self._tick_turntable)

        self._preview = QLabel(self)
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumSize(240, 180)

        self._mode = QComboBox(self)
        for mode in PreviewDisplayMode:
            self._mode.addItem(_ts(_mode_label(mode)), mode.value)
        self._mode.currentIndexChanged.connect(self._on_mode_changed)

        fit = QPushButton(_ts("Fit"), self)
        fit.clicked.connect(self.fit_to_view)
        zoom_in = QPushButton("+", self)
        zoom_in.clicked.connect(lambda: self.zoom(1.2))
        zoom_out = QPushButton("-", self)
        zoom_out.clicked.connect(lambda: self.zoom(1.0 / 1.2))
        rotate = QPushButton(_ts("Rotate"), self)
        rotate.clicked.connect(lambda: self.rotate(18.0, 0.0))
        turn = QPushButton(_ts("Turntable"), self)
        turn.setCheckable(True)
        turn.toggled.connect(self.set_turntable_enabled)
        add_3d_button = QPushButton(_ts("Add 3D Layer"), self)
        add_3d_button.clicked.connect(self.add_3d_layer)
        import_button = QPushButton(_ts("Import Projection"), self)
        import_button.clicked.connect(self.import_to_canvas)

        row = QHBoxLayout()
        row.addWidget(self._mode, 1)
        row.addWidget(fit)
        row.addWidget(zoom_out)
        row.addWidget(zoom_in)
        row.addWidget(rotate)
        row.addWidget(turn)
        row.addWidget(add_3d_button)
        row.addWidget(import_button)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self._preview, 1)
        layout.addLayout(row)
        self.refresh()

    def set_host(self, host: HostAPI) -> None:
        if self._host is not None:
            self.detach_host()
        self._host = host
        self._subscribe_layer_events()
        self.sync_from_active_layer()

    def detach_host(self) -> None:
        self._unsubscribe_layer_events()
        self._host = None
        self._layer_id = None

    def set_scene(self, scene: Scene3DDocument) -> None:
        self._layer_id = None
        self._scene = scene
        self.fit_to_view()

    def rotate(self, yaw_delta: float, pitch_delta: float) -> None:
        self._camera = self._camera.rotated(yaw_delta=yaw_delta, pitch_delta=pitch_delta)
        if self._submit_camera_update():
            self.refresh()
            return
        self.refresh()

    def zoom(self, factor: float) -> None:
        self._camera = self._camera.zoomed(factor)
        if self._submit_camera_update():
            self.refresh()
            return
        self.refresh()

    def fit_to_view(self) -> None:
        self._camera = OrbitCameraState.fit_to_scene(self._scene)
        if self._submit_camera_update():
            self.refresh()
            return
        self.refresh()

    def set_turntable_enabled(self, enabled: bool) -> None:
        if enabled:
            self._turntable.start()
        else:
            self._turntable.stop()
            if self._submit_camera_update():
                self.refresh()

    def import_to_canvas(self) -> bool:
        if self._host is None:
            return False
        try:
            result = self._projection.import_to_canvas(
                self._scene,
                self._host.canvas,
                ProjectionImportRequest(
                    width=max(1, self._preview.width()),
                    height=max(1, self._preview.height()),
                    display_mode=self._display_mode,
                    camera=self._camera,
                    metadata={"viewer": "generated_3d_preview_panel"},
                ),
            )
            return result.submitted
        except _ProjectionFaceLimitExceeded:
            show = getattr(getattr(self._host, "ui", None), "show_message", None)
            if callable(show):
                try:
                    show(_ts(_PROJECTION_FACE_LIMIT_MESSAGE))
                except Exception:
                    logger.warning("3D projection refusal notice could not be displayed")
            return False
        except Exception:
            logger.warning("3D projection import failed", exc_info=True)
            return False

    def add_3d_layer(self) -> bool:
        if self._host is None:
            return False
        add_layer = getattr(self._host.canvas, "add_3d_layer", None)
        if not callable(add_layer):
            return False
        camera = {
            "yaw_degrees": float(self._camera.yaw_degrees),
            "pitch_degrees": float(self._camera.pitch_degrees),
            "zoom": float(self._camera.zoom),
            "target": list(self._camera.target),
            "distance": float(self._camera.distance),
        }
        try:
            ref = add_layer(
                _ts("3D Layer"),
                scene=self._scene,
                camera=camera,
                display_mode=self._display_mode.value,
            )
            layer_id = getattr(ref, "id", None)
            if isinstance(layer_id, str) and layer_id:
                self._layer_id = layer_id
            return bool(getattr(ref, "id", None))
        except Exception:
            logger.warning("3D layer import failed", exc_info=True)
            return False

    def refresh(self) -> None:
        result = self._renderer.render(
            self._scene,
            width=max(1, self._preview.width()),
            height=max(1, self._preview.height()),
            camera=self._camera,
            display_mode=self._display_mode,
        )
        self._preview.setPixmap(QPixmap.fromImage(result.image))
        self._preview.setToolTip(", ".join(result.fallbacks))

    def resizeEvent(self, event) -> None:  # noqa: N802, ANN001
        super().resizeEvent(event)
        self.refresh()

    def _tick_turntable(self) -> None:
        self._camera = self._camera.turntable(0.033)
        self.refresh()

    def _on_mode_changed(self, index: int) -> None:
        if self._updating_mode:
            return
        value = self._mode.itemData(index)
        self._display_mode = PreviewDisplayMode(str(value))
        if self._submit_display_mode_update():
            self.refresh()
            return
        self.refresh()

    def sync_from_active_layer(self) -> bool:
        layer = self._active_layer()
        return self._sync_from_layer(layer)

    def _subscribe_layer_events(self) -> None:
        if self._host is None:
            return
        bus = getattr(self._host, "events", None)
        subscribe = getattr(bus, "on", None) or getattr(bus, "subscribe", None)
        if not callable(subscribe):
            return
        handler = self._on_layer_event
        self._event_handlers.append(handler)
        for event_name in (Events.LAYER_ADDED, Events.LAYER_SELECTED, Events.LAYER_CHANGED):
            self._event_tokens.append((event_name, subscribe(event_name, handler)))

    def _unsubscribe_layer_events(self) -> None:
        if self._host is None:
            self._event_tokens.clear()
            self._event_handlers.clear()
            return
        bus = getattr(self._host, "events", None)
        off = getattr(bus, "off", None)
        unsubscribe = getattr(bus, "unsubscribe", None)
        for event_name, token in self._event_tokens:
            if callable(off):
                with suppress(Exception):
                    off(event_name, token)
                    continue
            if callable(unsubscribe):
                with suppress(Exception):
                    unsubscribe(event_name, token)
        self._event_tokens.clear()
        self._event_handlers.clear()

    def _on_layer_event(self, payload: Any = None) -> None:
        layer_id = self._payload_value(payload, "layer_id")
        layer = self._find_layer(str(layer_id)) if isinstance(layer_id, str) else None
        if layer is None and self._payload_value(payload, "change_type") == "selected":
            layer = self._active_layer()
        if layer is None:
            return
        if self._sync_from_layer(layer):
            return
        if self._payload_value(payload, "change_type") == "selected":
            self._layer_id = None

    def _sync_from_layer(self, layer: Any | None) -> bool:
        if layer is None or str(getattr(layer, "layer_type", "")) != "3d":
            return False
        scene = getattr(layer, "scene_3d", None)
        if not isinstance(scene, Scene3DDocument):
            return False
        self._layer_id = str(getattr(layer, "id", "") or "")
        self._scene = scene
        self._camera = orbit_camera_state(getattr(layer, "camera_3d", None), scene)
        self._display_mode = preview_display_mode(getattr(layer, "display_mode_3d", "solid"))
        self._sync_mode_combo()
        self.refresh()
        return True

    def _active_layer(self) -> Any | None:
        if self._host is None:
            return None
        active = getattr(self._host.canvas, "active_layer", None)
        if callable(active):
            return active()
        return active

    def _find_layer(self, layer_id: str) -> Any | None:
        if self._host is None or not layer_id:
            return None
        canvas = self._host.canvas
        finder = getattr(canvas, "_find_layer_by_id_tree", None)
        if callable(finder):
            return finder(layer_id)
        for layer in getattr(canvas, "_layers", []) or []:
            found = self._find_layer_in_tree(layer, layer_id)
            if found is not None:
                return found
        return None

    def _find_layer_in_tree(self, layer: Any, layer_id: str) -> Any | None:
        if getattr(layer, "id", None) == layer_id:
            return layer
        for child in getattr(layer, "children", []) or []:
            found = self._find_layer_in_tree(child, layer_id)
            if found is not None:
                return found
        return None

    def _sync_mode_combo(self) -> None:
        mode = getattr(self, "_mode", None)
        if mode is None:
            return
        for index in range(mode.count()):
            if str(mode.itemData(index)) == self._display_mode.value:
                self._updating_mode = True
                try:
                    old = mode.blockSignals(True)
                    mode.setCurrentIndex(index)
                    mode.blockSignals(old)
                finally:
                    self._updating_mode = False
                return

    def _submit_camera_update(self) -> bool:
        if self._host is None or not self._layer_id:
            return False
        submit = getattr(self._host.canvas, "submit", None)
        if not callable(submit):
            return False
        return bool(
            submit(
                SetLayer3DCameraCommand(
                    self._layer_id,
                    self._camera_payload(),
                    merge_key="3d-layer-camera",
                )
            )
        )

    def _submit_display_mode_update(self) -> bool:
        if self._host is None or not self._layer_id:
            return False
        submit = getattr(self._host.canvas, "submit", None)
        if not callable(submit):
            return False
        return bool(
            submit(
                SetLayer3DDisplayModeCommand(
                    self._layer_id,
                    self._display_mode.value,
                    merge_key="3d-layer-display-mode",
                )
            )
        )

    def _camera_payload(self) -> dict[str, Any]:
        return {
            "yaw_degrees": float(self._camera.yaw_degrees),
            "pitch_degrees": float(self._camera.pitch_degrees),
            "zoom": float(self._camera.zoom),
            "target": list(self._camera.target),
            "distance": float(self._camera.distance),
        }

    @staticmethod
    def _payload_value(payload: Any, key: str) -> Any:
        if isinstance(payload, Mapping):
            return payload.get(key)
        return getattr(payload, key, None)


class ThreeDGeneratedPreviewPlugin(UIPanelPlugin):
    metadata = PluginMetadata(
        id="loomstroke.generated_3d_preview",
        name=_ts("3D Preview"),
        version="0.1.0",
        description=_ts("Preview generated 3D assets and import projections."),
        author="LoomStroke Team",
        types=[PluginType.UI_PANEL],
    )

    def __init__(self) -> None:
        super().__init__(self.metadata)
        self._host: HostAPI | None = None
        self._panel: Generated3DPreviewWidget | None = None

    def descriptor(self) -> PanelDescriptor:
        return PanelDescriptor(
            id="loomstroke.generated_3d_preview.panel",
            display_name=_ts("3D Preview"),
            category="tools",
            default_position="right",
            default_size=(360, 420),
            resizable=True,
            closable=True,
            floating=False,
        )

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        if self._panel is None:
            self._panel = Generated3DPreviewWidget(parent=parent)
            if self._host is not None:
                self._panel.set_host(self._host)
        return self._panel

    def activate(self, host_api: HostAPI) -> None:  # type: ignore[override]
        super().activate(host_api)
        self._host = host_api
        if self._panel is not None:
            self._panel.set_host(host_api)

    def deactivate(self) -> None:
        if self._panel is not None:
            self._panel.detach_host()
            self._panel.deleteLater()
            self._panel = None
        self._host = None
        super().deactivate()


def create_plugin() -> ThreeDGeneratedPreviewPlugin:
    return ThreeDGeneratedPreviewPlugin()


def _mode_label(mode: PreviewDisplayMode) -> str:
    return {
        PreviewDisplayMode.SOLID: "Solid",
        PreviewDisplayMode.WIREFRAME: "Wireframe",
        PreviewDisplayMode.TEXTURED: "Textured",
        PreviewDisplayMode.NORMAL_DEBUG: "Normal Map",
    }[mode]


def _fallback_scene() -> Scene3DDocument:
    material = MaterialAsset(
        id="fallback_material",
        name="Fallback",
        base_color=(0.64, 0.72, 0.86, 1.0),
    )
    mesh = create_unit_cube_mesh(material_id=material.id)
    node = Scene3DNode(id="fallback_node", name="Fallback Cube", mesh_id=mesh.id)
    return Scene3DDocument(
        id="fallback_generated_3d_scene",
        nodes=(node,),
        meshes=(mesh,),
        materials=(material,),
        root_node_ids=(node.id,),
    )
