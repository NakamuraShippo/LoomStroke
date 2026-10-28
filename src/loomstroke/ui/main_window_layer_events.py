"""Layer panel and EventBus routing for MainWindow."""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import Any, cast

logger = logging.getLogger(__name__)


class MainWindowLayerEventCoordinator:
    """Bridge LayerPanel/EventBus layer events to canvas operations."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def on_layer_visibility_changed(self, index: int, visible: bool) -> None:
        """Handle layer visibility change from panel."""
        canvas = self._canvas()
        if canvas is None:
            return
        layer = self._layer_from_ui_index(canvas, index)
        if layer is None:
            return
        canvas_any = cast(Any, canvas)
        canvas_any.set_layer_visibility(layer.id, visible)
        logger.debug("Set layer %s visibility to %s", layer.id, visible)

    def on_layer_locked_changed(self, index: int, locked: bool) -> None:
        """Handle layer lock change from panel."""
        canvas = self._canvas()
        if canvas is None:
            return
        layer = self._layer_from_ui_index(canvas, index)
        if layer is None:
            return
        canvas_any = cast(Any, canvas)
        if hasattr(canvas_any, "set_layer_locked"):
            canvas_any.set_layer_locked(layer.id, locked)
            logger.debug("Set layer %s locked to %s", layer.id, locked)
        else:
            logger.warning("Canvas service does not support set_layer_locked")

    def on_layer_opacity_changed(self, index: int, opacity: float) -> None:
        """Handle layer opacity change from panel."""
        from .panels.layer_opacity_controller import _apply_opacity_signal  # noqa: PLC0415

        canvas = self._canvas()
        if canvas is None:
            return
        layer = self._layer_from_ui_index(canvas, index)
        if layer is None:
            return
        _apply_opacity_signal(self._window, layer.id, opacity)
        logger.debug("Set layer %s opacity to %s", layer.id, opacity)

    def on_layer_selected(self, index: int) -> None:
        """Handle layer selection from panel."""
        canvas = self._canvas()
        if canvas is None:
            return
        layer = self._layer_from_ui_index(canvas, index)
        if layer is None:
            return
        if hasattr(canvas, "set_active_layer"):
            canvas.set_active_layer(layer.id)
            logger.debug("Set active layer to %s", layer.id)

    def on_layer_visibility_changed_by_id(self, layer_id: str, visible: bool) -> None:
        canvas = self._canvas()
        if canvas is None:
            return
        canvas_any = cast(Any, canvas)
        canvas_any.set_layer_visibility(layer_id, visible)
        logger.debug("Set layer %s visibility to %s", layer_id, visible)

    def on_layer_locked_changed_by_id(self, layer_id: str, locked: bool) -> None:
        canvas = self._canvas()
        if canvas is None:
            return
        canvas_any = cast(Any, canvas)
        if hasattr(canvas_any, "set_layer_locked"):
            canvas_any.set_layer_locked(layer_id, locked)
            logger.debug("Set layer %s locked to %s", layer_id, locked)

    def on_layer_alpha_lock_changed_by_id(self, layer_id: str, alpha_locked: bool) -> None:
        canvas = self._canvas()
        if canvas is None:
            return
        canvas_any = cast(Any, canvas)
        if hasattr(canvas_any, "set_layer_alpha_locked"):
            canvas_any.set_layer_alpha_locked(layer_id, alpha_locked)

    def on_layer_opacity_changed_by_id(self, layer_id: str, opacity: float) -> None:
        from .panels.layer_opacity_controller import _apply_opacity_signal  # noqa: PLC0415

        canvas = self._canvas()
        if canvas is None:
            return
        _apply_opacity_signal(self._window, layer_id, opacity)
        logger.debug("Set layer %s opacity to %s", layer_id, opacity)

    def on_layer_selected_by_id(self, layer_id: str) -> None:
        canvas = self._canvas()
        if canvas is not None and hasattr(canvas, "set_active_layer"):
            canvas.set_active_layer(layer_id)
            logger.debug("Set active layer to %s", layer_id)
        self._window._event_bus.emit("layer.selected", {"layer_id": layer_id})

    def on_layer_deleted_by_id(self, layer_id: str) -> None:
        canvas = self._canvas()
        if canvas is None:
            return
        from .comic_content_selection import submit_selected_comic_content  # noqa: PLC0415

        if submit_selected_comic_content(self._window, canvas, "delete"):
            return
        canvas_any = cast(Any, canvas)
        from loomstroke.core.commands import DeleteLayerCommand  # noqa: PLC0415

        submit = getattr(canvas_any, "submit", None)
        if callable(submit):
            cmd = DeleteLayerCommand(layer_id=layer_id)
            if submit(cmd):
                logger.info("Removed layer %s", layer_id)
            else:
                logger.warning("Failed to remove layer %s", layer_id)
        elif hasattr(canvas_any, "remove_layer"):
            canvas_any.remove_layer(layer_id)

    def on_layer_added(self) -> None:
        """Handle layer added signal from layer panel."""
        canvas = self._canvas()
        if canvas is None:
            return
        count = len(getattr(canvas, "_layers", []))
        canvas_any = cast(Any, canvas)
        from loomstroke.core.commands import AddLayerCommand  # noqa: PLC0415

        submit = getattr(canvas_any, "submit", None)
        if callable(submit):
            cmd = AddLayerCommand(f"Layer {count}")
            submit(cmd)
        else:
            canvas_any.add_layer(f"Layer {count}")
        logger.info("Added layer to canvas: Layer %d", count)

    def on_mask_layer_added(self) -> None:
        """Handle mask layer added signal by adding a mask to the selected layer."""
        canvas = self._canvas()
        if canvas is None:
            return
        canvas_any = cast(Any, canvas)

        panel = getattr(self._window, "_layer_panel", None)
        layer_id = getattr(panel, "_selected_layer_id", None) if panel else None
        if not layer_id:
            return

        from loomstroke.core.commands import AddLayerMaskCommand  # noqa: PLC0415

        submit = getattr(canvas_any, "submit", None)
        if callable(submit):
            cmd = AddLayerMaskCommand(layer_id=layer_id)
            submit(cmd)
        else:
            add_mask = getattr(canvas_any, "add_layer_mask", None)
            if callable(add_mask):
                add_mask(layer_id)
        logger.info("Added layer mask to layer: %s", layer_id)

    def on_layer_selected_from_bus(self, payload: Mapping[str, Any] | Any) -> None:
        """Sync panel selection without full rebuild (preserves scroll)."""
        panel = getattr(self._window, "_layer_panel", None)
        if panel is None:
            return
        try:
            layer_id = getattr(payload, "layer_id", None)
            if layer_id is None and isinstance(payload, Mapping):
                layer_id = payload.get("layer_id")
            if layer_id:
                if hasattr(panel, "select_layer_by_id"):
                    panel.select_layer_by_id(layer_id)
                elif hasattr(panel, "_row_for_layer"):
                    row = panel._row_for_layer(layer_id)
                    if row is not None:
                        panel._list.blockSignals(True)
                        panel._list.setCurrentRow(row)
                        panel._list.blockSignals(False)
                        panel._selected_layer_id = layer_id
        except Exception:
            logger.debug("Failed to sync layer selection", exc_info=True)

    def on_layer_event_from_bus(self, payload: Mapping[str, Any] | Any) -> None:
        """Refresh layer panel when any external layer event occurs."""
        payload_value = self._payload_value_reader(payload)
        if self._is_duplicate_layer_event(payload_value):
            return

        panel = getattr(self._window, "_layer_panel", None)
        if panel is None:
            return
        try:
            change_type = payload_value("change_type")
            if change_type == "content" and self._handle_content_layer_event(
                panel,
                payload_value,
            ):
                return
            if change_type == "effect_params_changed" and self._refresh_layer_event_thumbnail(
                panel,
                payload_value("layer_id"),
                defer_during_stroke=False,
            ):
                return

            self._refresh_layer_panel(panel)
        except Exception:
            logger.debug("Failed to refresh layer panel after layer event", exc_info=True)

    def on_layer_deleted(self, ui_index: int) -> None:
        """Handle layer deleted signal from layer panel."""
        canvas = self._canvas()
        if canvas is None:
            return
        layers = getattr(canvas, "_layers", [])

        if 0 <= ui_index < len(layers):
            canvas_index = len(layers) - 1 - ui_index

            if 0 <= canvas_index < len(layers):
                canvas_any = cast(Any, canvas)
                success = canvas_any.remove_layer_by_index(canvas_index)
                if success:
                    logger.info(
                        "Removed layer at canvas index %s (UI index %s)",
                        canvas_index,
                        ui_index,
                    )
                else:
                    logger.warning("Failed to remove layer at canvas index %s", canvas_index)
            else:
                logger.warning("Invalid canvas index %s for layer deletion", canvas_index)
        else:
            logger.warning("Invalid UI index %s for layer deletion", ui_index)

    def on_layer_reordered(self, ui_from: int, ui_to: int) -> None:
        """Handle layer reorder gestures from the layer panel."""
        if ui_from == ui_to:
            return
        canvas = self._canvas()
        if canvas is None:
            logger.debug("Host API unavailable; ignoring layer reorder")
            return

        layers = getattr(canvas, "_layers", [])
        total = len(layers)
        if total <= 1:
            return

        canvas_from = total - 1 - ui_from
        canvas_to = total - 1 - ui_to

        mover = getattr(canvas, "move_layer", None)
        if not callable(mover):
            logger.warning("Canvas service does not support move_layer; refreshing panel")
            self._refresh_default_layers()
            return

        if not (0 <= canvas_from < total and 0 <= canvas_to < total):
            logger.warning(
                "Layer reorder indices out of range (canvas %s -> %s)",
                canvas_from,
                canvas_to,
            )
            self._refresh_default_layers()
            return

        try:
            moved = bool(mover(canvas_from, canvas_to))
        except Exception as exc:
            logger.error(
                "Failed to reorder layer (canvas %s -> %s): %s",
                canvas_from,
                canvas_to,
                exc,
            )
            moved = False

        if not moved:
            self._refresh_default_layers()
            return

        logger.info(
            "Reordered layer: UI %s -> %s mapped to canvas %s -> %s",
            ui_from,
            ui_to,
            canvas_from,
            canvas_to,
        )

    def on_layer_blend_mode_changed(self, payload: Mapping[str, Any]) -> None:
        """Handle layer blend mode change from layer panel."""
        from .panels.layer_blend_request import _apply_panel_blend_request  # noqa: PLC0415

        canvas = self._canvas()
        if canvas is not None:
            _apply_panel_blend_request(canvas, payload)

    def _canvas(self) -> Any | None:
        host_api = getattr(self._window, "_host_api", None)
        if host_api and hasattr(host_api, "canvas"):
            return host_api.canvas
        return None

    @staticmethod
    def _layer_from_ui_index(canvas: Any, index: int) -> Any | None:
        layers = getattr(canvas, "_layers", [])
        if 0 <= index < len(layers):
            actual_index = len(layers) - 1 - index
            if 0 <= actual_index < len(layers):
                return layers[actual_index]
        return None

    def _refresh_default_layers(self) -> None:
        panel = getattr(self._window, "_layer_panel", None)
        if panel is not None and hasattr(panel, "_add_default_layers"):
            panel._add_default_layers()

    @staticmethod
    def _payload_value_reader(payload: Mapping[str, Any] | Any) -> Callable[..., Any]:
        def payload_value(key: str, default: Any = None) -> Any:
            if isinstance(payload, Mapping):
                return payload.get(key, default)
            return getattr(payload, key, default)

        return payload_value

    def _is_duplicate_layer_event(self, payload_value: Callable[..., Any]) -> bool:
        ts = payload_value("timestamp")
        if ts is None:
            return False
        last = getattr(self._window, "_last_layer_event_ts", None)
        if last == ts:
            return True
        self._window._last_layer_event_ts = ts
        return False

    def _handle_content_layer_event(
        self,
        panel: Any,
        payload_value: Callable[..., Any],
    ) -> bool:
        new_val = payload_value("new_value") or {}
        meta = new_val.get("metadata", {}) if isinstance(new_val, dict) else {}
        cmd = meta.get("command", "")
        if cmd not in ("paint", "stamp_brush", "erase"):
            return False
        return self._refresh_layer_event_thumbnail(panel, payload_value("layer_id"))

    def _refresh_layer_event_thumbnail(
        self,
        panel: Any,
        layer_id: Any,
        *,
        defer_during_stroke: bool = True,
    ) -> bool:
        if not layer_id or not hasattr(panel, "refresh_thumbnail"):
            return False
        if defer_during_stroke and getattr(self._window, "_is_canvas_stroke_in_progress", False):
            self._window._defer_layer_thumbnail_refresh(str(layer_id))
        else:
            self._window._refresh_layer_thumbnail(str(layer_id))
        return True

    @staticmethod
    def _refresh_layer_panel(panel: Any) -> None:
        if hasattr(panel, "refresh"):
            panel.refresh()
        else:
            panel._add_default_layers()
