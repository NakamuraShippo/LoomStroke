"""Bridge one panel's native opacity gesture to its scalar preview owner."""

from __future__ import annotations

import logging
import weakref
from typing import Any, cast

from PySide6.QtCore import QObject, QSignalBlocker, Qt
from shiboken6 import isValid

from loomstroke.core.commands_layer_opacity import SetLayerOpacityCommand
from loomstroke.core.layer_opacity_preview_boundary import (
    _begin_opacity_preview,
    _supports_opacity_gesture,
)

logger = logging.getLogger(__name__)


class _PreviewSignalRequest:
    def __init__(self, owner: Any, value: float):
        self.owner = weakref.ref(owner)
        self.value = value

    def matches(self, canvas: Any, layer_id: str, value: float) -> bool:
        owner = self.owner()
        return (
            owner is not None
            and owner.canvas is canvas
            and owner.layer.id == layer_id
            and self.value == value
            and owner.scope.current()
        )


class _LayerOpacityController(QObject):
    def __init__(self, panel: Any):
        super().__init__(panel)
        self._panel = weakref.ref(panel)
        self.owner: Any = None
        self.kind: str | None = None
        self.legacy = False
        self.request: Any = None
        self.disposed = False

    def begin(self, kind: str) -> bool:
        if self.disposed or not self.cancel():
            return False
        panel = self._panel()
        if panel is None or not isValid(panel):
            return False
        self.kind = kind
        canvas = panel._get_canvas()
        self.legacy = not _supports_opacity_gesture(canvas)
        if self.legacy:
            return True
        current = panel._list.currentItem()
        layer_id = current.data(0, Qt.ItemDataRole.UserRole) if current is not None else None
        if not isinstance(layer_id, str):
            return False
        try:
            self.owner = _begin_opacity_preview(canvas, layer_id)
            if self.owner is not None:
                self.owner.closed_callback = weakref.WeakMethod(self._owner_closed)
        except Exception:
            logger.exception("Opacity gesture preparation failed")
            self.owner = None
        return self.owner is not None

    def changed(self, value: int) -> None:
        panel = self._panel()
        if panel is None or self.disposed:
            return
        if getattr(getattr(panel, "_opacity_slider", None), "_interrupted", False):
            self.sync()
            return
        if panel._list.currentItem() is None:
            panel._set_opacity_value(value)
            return
        discrete = self.kind is None
        if discrete:
            self.begin("discrete")
        panel._set_opacity_value(value)
        current = panel._list.currentItem()
        layer_id = current.data(0, Qt.ItemDataRole.UserRole) if current is not None else None
        opacity = max(0.0, min(1.0, value / 100.0))
        owner = self.owner
        if self.legacy:
            panel._handle_opacity_changed(layer_id, opacity)
        elif owner is not None and owner.preview(opacity):
            request = _PreviewSignalRequest(owner, opacity)
            self.request = weakref.ref(request)
            try:
                panel._handle_opacity_changed(layer_id, opacity)
            finally:
                self.request = None
            if not owner.scope.current():
                owner.cancel()
        else:
            self.sync()
        if discrete:
            self.end()

    def end(self) -> bool:
        self.kind = None
        owner, self.legacy = self.owner, False
        if owner is None:
            return True
        try:
            result = owner.commit()
        except Exception:
            logger.exception("Opacity gesture completion failed")
            result = False
            owner.cancel()
        if owner.closed:
            self.owner = None
        self.sync()
        return cast(bool, result)

    def cancel(self) -> bool:
        self.kind = None
        owner, self.legacy = self.owner, False
        if owner is not None and not owner.cancel():
            return False
        self.owner = None
        return True

    def sync(self) -> None:
        panel = self._panel()
        if panel is None or not isValid(panel):
            return
        current = panel._list.currentItem()
        layer_id = current.data(0, Qt.ItemDataRole.UserRole) if current is not None else None
        layer = panel._resolve_canvas_layer(layer_id) if isinstance(layer_id, str) else None
        if layer is not None:
            entry = panel._find_entry_by_id(layer_id)
            if entry is not None:
                entry.opacity = layer.opacity
            panel._set_opacity_value(round(layer.opacity * 100))

    def shutdown(self) -> None:
        self.disposed = True
        self.cancel()

    def before_selection_change(self) -> bool:
        if self.cancel():
            return True
        panel = self._panel()
        if panel is not None and isValid(panel):
            item = getattr(panel, "_item_map", {}).get(self.owner.layer.id)
            with QSignalBlocker(panel._list):
                panel._list.setCurrentItem(item)
            panel._selected_layer_id = self.owner.layer.id if item is not None else None
            self.sync()
        return False

    def _owner_closed(self, owner: Any) -> None:
        if self.owner is owner:
            self.owner = None
            if not self.disposed:
                self.sync()


def _opacity_controller_for(panel: Any) -> _LayerOpacityController:
    controller = getattr(panel, "_opacity_controller", None)
    if controller is None:
        controller = _LayerOpacityController(panel)
        panel._opacity_controller = controller
    return controller


def _apply_opacity_signal(window: Any, layer_id: str, opacity: float) -> None:
    canvas = window._host_api.canvas
    panel = getattr(window, "_layer_panel", None)
    source = QObject.sender(window) if isinstance(window, QObject) else None
    controller = vars(source).get("_opacity_controller") if source is not None else None
    if not isinstance(controller, _LayerOpacityController):
        controller = getattr(panel, "_opacity_controller", None)
    if isinstance(controller, _LayerOpacityController) and controller.request is not None:
        request = controller.request()
        if request is None or not request.matches(canvas, layer_id, opacity):
            return
        # The checked owner already applied this temporary scalar. The public
        # signal shape is preserved for observers without creating tick history.
        return
    if _supports_opacity_gesture(canvas):
        canvas.submit(SetLayerOpacityCommand(layer_id, opacity))
    else:
        canvas.set_layer_opacity(layer_id, opacity)
