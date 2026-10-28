"""Layer menu action coordination for MainWindow."""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from typing import Any, cast

from PySide6.QtCore import Qt

logger = logging.getLogger(__name__)


class MainWindowLayerActionsCoordinator:
    """Bridge Layer menu actions to canvas layer operations."""

    def __init__(self, window: Any, *, message_box: Callable[[], Any]) -> None:
        self._window = window
        self._message_box = message_box

    def get_canvas_layers(self) -> list[Any]:
        canvas = self._canvas()
        if canvas is None:
            return []
        layers = getattr(canvas, "_layers", None)
        if isinstance(layers, list):
            return layers
        legacy_layers = getattr(canvas, "layers", None)
        if isinstance(legacy_layers, list):
            return legacy_layers
        return []

    def get_active_canvas_layer(self) -> tuple[Any | None, int | None]:
        canvas = self._canvas()
        if canvas is None:
            return None, None
        layers = self.get_canvas_layers()
        if not layers:
            return None, None

        active = getattr(canvas, "active_layer", None)
        active_id = getattr(active, "id", None)
        if isinstance(active_id, str):
            for index, layer in enumerate(layers):
                if getattr(layer, "id", None) == active_id:
                    return layer, index

        active_index = getattr(canvas, "_active_index", None)
        if isinstance(active_index, int) and 0 <= active_index < len(layers):
            return layers[active_index], active_index

        return layers[0], 0

    @staticmethod
    def find_layer_by_id(layers: list[Any], layer_id: str) -> Any | None:
        if not layer_id:
            return None
        for layer in layers:
            if getattr(layer, "id", None) == layer_id:
                return layer
        return None

    def refresh_layer_panel(self) -> None:
        panel = getattr(self._window, "_layer_panel", None)
        if panel is None:
            return
        if hasattr(panel, "_add_default_layers"):
            panel._add_default_layers()
        elif hasattr(panel, "refresh"):
            panel.refresh()

    @staticmethod
    def recompose_canvas(canvas: Any) -> None:
        for method_name in ("_recompose", "_invalidate_composite", "recompose"):
            method = getattr(canvas, method_name, None)
            if callable(method):
                try:
                    method()
                except Exception:
                    logger.debug("Canvas recomposition failed", exc_info=True)
                break

    def on_layer_new(self) -> None:
        self._window._event_bus.emit("layer.new", {})
        self._window._on_layer_added()

    def copy_layer_visual_state(self, source_layer: Any, dest_layer: Any) -> None:  # noqa: PLR0912
        canvas = self._canvas()
        if canvas is None:
            return
        canvas_any = cast(Any, canvas)
        dest_id = getattr(dest_layer, "id", None)
        if isinstance(dest_id, str):
            if hasattr(canvas_any, "set_layer_visibility"):
                canvas_any.set_layer_visibility(
                    dest_id,
                    bool(getattr(source_layer, "visible", True)),
                )
            if hasattr(canvas_any, "set_layer_opacity"):
                canvas_any.set_layer_opacity(
                    dest_id,
                    float(getattr(source_layer, "opacity", 1.0)),
                )
            if hasattr(canvas_any, "set_layer_locked"):
                canvas_any.set_layer_locked(dest_id, bool(getattr(source_layer, "locked", False)))
            if hasattr(canvas_any, "set_layer_blend_mode"):
                canvas_any.set_layer_blend_mode(
                    dest_id, str(getattr(source_layer, "blend_mode", "normal"))
                )
        if hasattr(dest_layer, "offset_x"):
            dest_layer.offset_x = int(getattr(source_layer, "offset_x", 0))
        if hasattr(dest_layer, "offset_y"):
            dest_layer.offset_y = int(getattr(source_layer, "offset_y", 0))
        if hasattr(dest_layer, "metadata"):
            source_metadata = getattr(source_layer, "metadata", None)
            if isinstance(source_metadata, dict):
                try:
                    dest_layer.metadata = copy.deepcopy(source_metadata)
                except Exception:
                    logger.debug("copy_layer_visual_state: suppressed exception", exc_info=True)
                    dest_layer.metadata = dict(source_metadata)

        source_image = getattr(source_layer, "image", None)
        if source_image is not None and hasattr(source_image, "copy"):
            try:
                dest_layer.image = source_image.copy()
            except Exception:
                logger.debug("Failed to copy source layer image", exc_info=True)

        source_vectors = getattr(source_layer, "vector_objects", None)
        if source_vectors:
            try:
                dest_layer.vector_objects = copy.deepcopy(source_vectors)
            except Exception:
                try:
                    dest_layer.vector_objects = list(source_vectors)
                except Exception:
                    logger.debug("Failed to copy vector objects", exc_info=True)
        self.recompose_canvas(canvas_any)

    def on_layer_dup(self) -> None:
        self._window._event_bus.emit("layer.duplicate", {})
        canvas = self._canvas()
        if canvas is None:
            return

        from .comic_content_selection import submit_selected_comic_content  # noqa: PLC0415

        if submit_selected_comic_content(self._window, canvas, "copy"):
            self.refresh_layer_panel()
            return

        source_layer, _ = self.get_active_canvas_layer()
        if source_layer is None:
            return
        canvas_any = cast(Any, canvas)
        source_id = getattr(source_layer, "id", None)
        if not source_id:
            return

        from loomstroke.core.commands import DuplicateLayerCommand  # noqa: PLC0415

        submit = getattr(canvas_any, "submit", None)
        if callable(submit):
            cmd = DuplicateLayerCommand(source_layer_id=source_id)
            submit(cmd)
        else:
            add_layer = getattr(canvas_any, "add_layer", None)
            if not callable(add_layer):
                return
            source_name = str(getattr(source_layer, "name", "Layer"))
            try:
                layer_ref = add_layer(
                    f"{source_name} Copy",
                    layer_type=getattr(source_layer, "layer_type", "raster"),
                )
            except TypeError:
                layer_ref = add_layer(f"{source_name} Copy")
            layers = self.get_canvas_layers()
            new_layer = self.find_layer_by_id(layers, getattr(layer_ref, "id", ""))
            if new_layer is not None:
                self.copy_layer_visual_state(source_layer, new_layer)
        self.refresh_layer_panel()

    def on_layer_del(self) -> None:
        self._window._event_bus.emit("layer.delete", {})
        if self._canvas() is None:
            return
        layer_id = self._selected_layer_id_from_panel()
        if not layer_id:
            _, active_index = self.get_active_canvas_layer()
            layers = self.get_canvas_layers()
            if active_index is None or not layers:
                return
            if not (0 <= active_index < len(layers)):
                return
            layer = layers[active_index]
            layer_id = getattr(layer, "id", None)
        if not isinstance(layer_id, str):
            return
        self._window._on_layer_deleted_by_id(layer_id)

    def on_layer_merge_down(self) -> None:  # noqa: PLR0911
        self._window._event_bus.emit("layer.merge_down", {})
        canvas = self._canvas()
        if canvas is None:
            return

        canvas_any = cast(Any, canvas)
        layers = self.get_canvas_layers()
        if len(layers) < 2:
            return

        source_layer, source_index = self.get_active_canvas_layer()
        if source_layer is None or source_index is None:
            return
        if source_index <= 0:
            return

        target_layer = layers[source_index - 1]
        source_id = getattr(source_layer, "id", None)
        target_id = getattr(target_layer, "id", None)
        if not isinstance(source_id, str) or not isinstance(target_id, str):
            return
        if bool(getattr(source_layer, "locked", False)):
            self._message_box().warning(self._window, "Layer Merge Down", "Source layer is locked.")
            return
        if bool(getattr(target_layer, "locked", False)):
            self._message_box().warning(self._window, "Layer Merge Down", "Target layer is locked.")
            return

        source_type = str(getattr(source_layer, "layer_type", "raster"))
        target_type = str(getattr(target_layer, "layer_type", "raster"))
        rasterize = getattr(canvas_any, "rasterize_layer", None)
        if source_type == "vector" and callable(rasterize) and not bool(rasterize(source_id)):
            return
        if target_type == "vector" and callable(rasterize) and not bool(rasterize(target_id)):
            return
        merger = getattr(canvas_any, "merge_layer_down", None)
        if callable(merger) and merger(source_id, target_id):
            if hasattr(canvas_any, "set_active_layer"):
                canvas_any.set_active_layer(target_id)
            self.refresh_layer_panel()

    def on_layer_merge_visible(self) -> None:
        """Merge all visible layers into one (Phase A Wave 2 #3)."""
        self._window._event_bus.emit("layer.merge_visible", {})
        canvas = self._canvas()
        if canvas is None:
            return

        canvas_any = cast(Any, canvas)
        layers = self.get_canvas_layers()
        if not layers:
            return

        rasterize = getattr(canvas_any, "rasterize_layer", None)
        for layer in layers:
            if (
                getattr(layer, "visible", False)
                and str(getattr(layer, "layer_type", "raster")) == "vector"
                and callable(rasterize)
            ):
                rasterize(getattr(layer, "id", ""))

        merger = getattr(canvas_any, "merge_visible_layers", None)
        if callable(merger):
            result_id = merger()
            if result_id:
                if hasattr(canvas_any, "set_active_layer"):
                    canvas_any.set_active_layer(result_id)
                self.refresh_layer_panel()

    def on_selection_to_mask(self) -> None:
        """Convert current selection to a layer mask on the active layer."""
        canvas = self._canvas()
        if canvas is None:
            return
        canvas_any = cast(Any, canvas)
        fn = getattr(canvas_any, "selection_to_layer_mask", None)
        if not callable(fn):
            return
        active = getattr(canvas_any, "active_layer", None)
        if active is None:
            return
        layer_id = getattr(active, "id", None)
        if layer_id:
            from loomstroke.core.layer_mask_selection_action import (  # noqa: PLC0415
                _selection_mask_action,
            )

            host = getattr(self._window, "_host_api", None)

            def current() -> bool:
                return (
                    getattr(self._window, "_host_api", None) is host
                    and self._canvas() is canvas
                    and getattr(self._window, "_host_api", None) is host
                )

            refresh = _selection_mask_action(canvas, layer_id, caller_current=current)
            if refresh is None:
                fn(layer_id)
                self.refresh_layer_panel()
            elif refresh and current():
                self.refresh_layer_panel()

    def on_layer_flatten_all(self) -> None:
        """Flatten all layers into one."""
        canvas = self._canvas()
        if canvas is None:
            return
        canvas_any = cast(Any, canvas)
        fn = getattr(canvas_any, "flatten_all_layers", None)
        if callable(fn):
            result_id = fn()
            if result_id:
                self.refresh_layer_panel()

    def move_active_canvas_layer_to(self, target_index: int) -> None:
        canvas = self._canvas()
        if canvas is None:
            return
        _, source_index = self.get_active_canvas_layer()
        if source_index is None or source_index == target_index:
            return
        layers = self.get_canvas_layers()
        if not (0 <= target_index < len(layers)):
            return
        mover = getattr(canvas, "move_layer", None)
        if not callable(mover):
            return
        if bool(mover(source_index, target_index)):
            self.refresh_layer_panel()

    def on_layer_arrange_to_front(self) -> None:
        layers = self.get_canvas_layers()
        if not layers:
            return
        self.move_active_canvas_layer_to(len(layers) - 1)

    def on_layer_arrange_forward(self) -> None:
        _, source_index = self.get_active_canvas_layer()
        if source_index is None:
            return
        self.move_active_canvas_layer_to(source_index + 1)

    def on_layer_arrange_backward(self) -> None:
        _, source_index = self.get_active_canvas_layer()
        if source_index is None:
            return
        self.move_active_canvas_layer_to(source_index - 1)

    def on_layer_arrange_to_back(self) -> None:
        self.move_active_canvas_layer_to(0)

    def on_layer_group(self) -> None:
        self._window._event_bus.emit("layer.group", {})
        self._message_box().information(
            self._window,
            "Layer Group",
            "Layer grouping is not implemented in this build.",
        )

    def on_layer_rasterize(self) -> None:
        self._window._event_bus.emit("layer.rasterize", {})
        canvas = self._canvas()
        if canvas is None:
            return
        source_layer, _ = self.get_active_canvas_layer()
        source_layer_id = getattr(source_layer, "id", None)
        if not isinstance(source_layer_id, str):
            return
        rasterize = getattr(canvas, "rasterize_layer", None)
        if not callable(rasterize):
            return
        if rasterize(source_layer_id):
            self.refresh_layer_panel()

    def _canvas(self) -> Any | None:
        host_api = getattr(self._window, "_host_api", None)
        if host_api and hasattr(host_api, "canvas"):
            return host_api.canvas
        return None

    def _selected_layer_id_from_panel(self) -> str | None:
        panel = getattr(self._window, "_layer_panel", None)
        if panel is None:
            return None
        current = getattr(panel, "_list", None)
        if current is None:
            return None
        item = current.currentItem()
        if item is None:
            return None
        layer_id = item.data(0, Qt.ItemDataRole.UserRole)
        return layer_id if isinstance(layer_id, str) else None
