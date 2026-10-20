"""Filter action coordination for MainWindow."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class MainWindowFilterActionsCoordinator:
    """Coordinate Filter menu action dispatch and repeat state."""

    def __init__(self, window: Any, *, message_box: Callable[[], Any]) -> None:
        self._window = window
        self._message_box = message_box
        self._last_direct_filter: tuple[str, dict[str, Any]] | None = None

    def initialize_state(self) -> None:
        self._window._last_filter_plugin = None
        self._last_direct_filter = None

    def on_last_filter(self) -> None:
        if self._last_direct_filter is not None:
            filter_type, params = self._last_direct_filter
            self._apply_direct_filter(filter_type, params=params, repeat=True)
            return
        plugin = self._window._last_filter_plugin
        if plugin:
            self.apply_filter(plugin)

    def apply_filter(self, plugin: Any) -> None:
        if isinstance(plugin, str):
            self._apply_direct_filter(plugin)
            return
        try:
            self._last_direct_filter = None
            self._window._last_filter_plugin = plugin
            self._window._event_bus.emit("filter.apply", {"plugin": plugin})
        except Exception as exc:
            logger.debug("_apply_filter: %s", exc)
            self._message_box().warning(self._window, "Filter Error", str(exc))

    def _apply_direct_filter(
        self, filter_type: str, *, params: dict[str, Any] | None = None, repeat: bool = False
    ) -> None:
        from PySide6.QtWidgets import QDialog  # noqa: PLC0415

        from .dialogs.direct_filter_dialog import DirectFilterDialog, ts  # noqa: PLC0415

        canvas = getattr(getattr(self._window, "_host_api", None), "canvas", None)
        dialog = None
        try:
            if canvas is None:
                raise ValueError("Canvas service is not available.")
            layer = canvas._get_active_layer()
            rasterize = layer is not None and layer.layer_type in {"vector", "text"}
            if rasterize:
                version = layer.texture_version
                box = self._message_box()
                answer = box.question(
                    self._window,
                    ts("Rasterize layer?"),
                    ts(
                        'Applying this filter requires rasterizing "{name}". The vector or text '
                        "will no longer be editable after applying. Continue?"
                    ).format(name=layer.name),
                    box.StandardButton.Yes | box.StandardButton.Cancel,
                    box.StandardButton.Cancel,
                )
                if answer != box.StandardButton.Yes:
                    return
                if canvas._get_active_layer() is not layer or layer.texture_version != version:
                    raise ValueError("The editing target changed. Reopen the filter to continue.")
            dialog = DirectFilterDialog(
                canvas,
                filter_type,
                params=params,
                parent=self._window,
                auto_apply=repeat,
                rasterize=rasterize,
            )
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._last_direct_filter = (filter_type, dialog.form.values())
                self._window._last_filter_plugin = None
        except Exception as exc:
            logger.exception("Direct filter failed")
            self._message_box().warning(self._window, ts("Filter Error"), ts(str(exc)))
        finally:
            if dialog is not None:
                dialog.session.close()
                dialog.deleteLater()
