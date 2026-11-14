"""Nonmodal Brush Manager failures at the existing UI callback boundary."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QEvent, QObject

from loomstroke.plugins.common.ui_notification_hint import has_log_only_ui_hint
from loomstroke.ui.translations import translate_runtime_text, translated_literal_domain

from .pen_notice import _gui_application, _PenNotice

logger = logging.getLogger(__name__)
_SOURCE = "The brush library could not be saved."


class _BrushWriteNotice(_PenNotice):
    def __init__(self, plugin: Any) -> None:
        super().__init__(
            translate_runtime_text,
            translated_literal_domain("BasicPen", "Brush Manager", use_fallback=True),
            "",
        )
        self.message = ""
        self.render: Callable[[], str] | None = None
        self._panel = getattr(plugin, "_panel", None)
        if isinstance(self._panel, QObject):
            self._panel.installEventFilter(self)

    def _retranslate(self) -> None:
        self._dialog.setWindowTitle(translate_runtime_text(self._title_source))
        self._dialog.setText(self.render() if self.render else translate_runtime_text(self.message))

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self._panel and event.type() in (QEvent.Type.Hide, QEvent.Type.Close):
            self.close()
        return super().eventFilter(watched, event)


def show_brush_operation_notice(
    plugin: Any, message: str, *, render: Callable[[], str] | None = None
) -> bool:
    host = getattr(plugin, "host_api", None)
    if not has_log_only_ui_hint(getattr(host, "ui", None)):
        return False
    try:
        if _gui_application() is None:
            logger.warning("Brush library feedback requires the GUI thread")
            return True
        notice = getattr(plugin, "_brush_write_feedback", None)
        if notice is None:
            notice = _BrushWriteNotice(plugin)
            plugin._brush_write_feedback = notice
        notice.message, notice.render = message, render
        notice.show(host)
    except Exception:
        logger.warning("Brush library write notice could not be displayed")
    return True


def clear_brush_write_feedback(plugin: Any) -> None:
    notice = getattr(plugin, "_brush_write_feedback", None)
    if notice is not None:
        notice.close()
        plugin._brush_write_feedback = None


def run_brush_write(plugin: Any, callback: Callable[..., Any], *args: Any) -> Any:
    try:
        return callback(*args)
    except (OSError, ValueError) as exc:
        logger.warning("Brush library write failed (%s)", type(exc).__name__)
        message = translated_literal_domain("BasicPen", _SOURCE, use_fallback=True)
        if not show_brush_operation_notice(plugin, message):
            plugin._notify(message, level="error")
        return False


def show_png_import_failure_summary(plugin: Any, created: int, skipped: int) -> None:
    if created:
        source = "Imported {count} PNG tips ({skipped} skipped)"
    elif skipped:
        source = "No PNG tips imported ({skipped} skipped)"
    else:
        source = "No PNG tips imported"

    def render() -> str:
        return translated_literal_domain("BasicPen", source, use_fallback=True).format(
            count=created, skipped=skipped
        )

    if not show_brush_operation_notice(plugin, render(), render=render):
        plugin._notify(render(), level="info" if created else "warning")
