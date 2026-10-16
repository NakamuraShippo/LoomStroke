"""Status-bar busy indicator for SAM3 AI Select long-running operations.

Subscribes to the SAM3 AI Select tool events on the shared event bus and
shows a message plus an indeterminate progress bar in the main window's
status bar while a SAM3 operation (RUN / AI Refine / preview submit) is
running.
"""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtWidgets import QLabel, QProgressBar

from .translations import translated_literal_domain

_TRANSLATION_DOMAIN = "SAM3AISelect"
# started-event -> busy label. Every started event is paired with at least
# one terminal event below; unmatched terminals only hit the guarded
# decrement and never underflow the counter.
_STARTED_EVENT_LABELS = {
    "sam3_ai_select.run_started": "AI Select running...",
    "sam3_ai_select.refine_started": "AI Refine running...",
    "sam3_ai_select.preview_submit_started": "Preview generation running...",
}
_FINISHED_EVENTS = (
    "sam3_ai_select.run_completed",
    "sam3_ai_select.run_blocked",
    "sam3_ai_select.run_failed",
    "sam3_ai_select.refine_completed",
    "sam3_ai_select.refine_blocked",
    "sam3_ai_select.refine_failed",
    "sam3_ai_select.ai_effect_preview_submitted",
    "sam3_ai_select.manga_inpaint_preview_submitted",
)
# Emitted when a RUN is rejected before inference (no busy indicator shown);
# users need to hear WHY nothing happened, most commonly a non-raster layer.
_SOURCE_BLOCKED_EVENT = "sam3_ai_select.run_source_blocked"
_SOURCE_BLOCKED_MESSAGES = {
    "ai_select_active_layer_image_missing": (
        "SAM3: the active layer is not a raster layer — select a raster layer and run again"
    ),
    "ai_select_active_layer_missing": "SAM3: no active layer to run on",
}
_SOURCE_BLOCKED_DEFAULT_MESSAGE = "SAM3: run blocked"
_FINISHED_MESSAGE_MS = 4000
_WARNING_MESSAGE_MS = 6000
_PROGRESS_WIDTH_PX = 120

logger = logging.getLogger("loomstroke.ui.main_window")


class Sam3BusyStatusIndicator:
    """Own the SAM3 busy label / progress bar in the status bar."""

    def __init__(self, window: Any, event_bus: Any) -> None:
        self._window = window
        self._label: QLabel | None = None
        self._progress: QProgressBar | None = None
        self._active_count = 0
        # The event bus stores weak references: keep the bound handlers
        # alive for the window's lifetime by owning them here.
        self._handlers: list[Any] = []
        subscribe = getattr(event_bus, "on", None)
        if not callable(subscribe):
            return
        for event_name, label in _STARTED_EVENT_LABELS.items():
            handler = self._make_handler(self._on_started, label)
            self._handlers.append(handler)
            subscribe(event_name, handler)
        for event_name in _FINISHED_EVENTS:
            handler = self._make_handler(self._on_finished, None)
            self._handlers.append(handler)
            subscribe(event_name, handler)
        blocked_handler = self._make_handler(self._on_source_blocked, None)
        self._handlers.append(blocked_handler)
        subscribe(_SOURCE_BLOCKED_EVENT, blocked_handler)

    @staticmethod
    def _make_handler(target: Any, label: str | None) -> Any:
        def _handler(payload: Any) -> None:
            try:
                cleaned = payload if isinstance(payload, dict) else {}
                if label is None:
                    target(cleaned)
                else:
                    target(cleaned, label)
            except Exception:  # noqa: BLE001 - status UI must never break events
                logger.debug("SAM3 status indicator update failed", exc_info=True)

        return _handler

    def _ensure_widgets(self) -> bool:
        if self._label is not None and self._progress is not None:
            return True
        status_bar = getattr(self._window, "statusBar", None)
        if not callable(status_bar):
            return False
        bar = status_bar()
        if bar is None:
            return False
        self._label = QLabel(bar)
        self._label.setObjectName("sam3RefineStatusLabel")
        self._progress = QProgressBar(bar)
        self._progress.setObjectName("sam3RefineStatusProgress")
        self._progress.setRange(0, 0)  # indeterminate
        self._progress.setFixedWidth(_PROGRESS_WIDTH_PX)
        self._progress.setTextVisible(False)
        bar.addPermanentWidget(self._label)
        bar.addPermanentWidget(self._progress)
        self._label.hide()
        self._progress.hide()
        return True

    def _on_started(self, payload: dict[str, Any], label: str) -> None:
        del payload
        if not self._ensure_widgets():
            return
        self._active_count += 1
        assert self._label is not None and self._progress is not None
        self._label.setText(_ts(label))
        self._label.show()
        self._progress.show()

    def _on_finished(self, payload: dict[str, Any]) -> None:
        if not self._ensure_widgets():
            return
        if self._active_count == 0:
            # Terminal without a started counterpart (e.g. bound-only manga
            # inpaint terminals): nothing is showing, nothing to do.
            return
        self._active_count -= 1
        if self._active_count > 0:
            return
        assert self._label is not None and self._progress is not None
        self._label.hide()
        self._progress.hide()
        status = str(payload.get("status") or "").strip()
        message = (
            _ts("SAM3 operation blocked") if status == "blocked" else _ts("SAM3 operation finished")
        )
        status_bar = getattr(self._window, "statusBar", None)
        if callable(status_bar):
            bar = status_bar()
            if bar is not None:
                bar.showMessage(message, _FINISHED_MESSAGE_MS)

    def _on_source_blocked(self, payload: dict[str, Any]) -> None:
        codes = payload.get("blockedReasonCodes")
        code = str(codes[0]) if isinstance(codes, list) and codes else ""
        message = _SOURCE_BLOCKED_MESSAGES.get(code, _SOURCE_BLOCKED_DEFAULT_MESSAGE)
        status_bar = getattr(self._window, "statusBar", None)
        if callable(status_bar):
            bar = status_bar()
            if bar is not None:
                bar.showMessage(_ts(message), _WARNING_MESSAGE_MS)


def _ts(text: str) -> str:
    return translated_literal_domain(_TRANSLATION_DOMAIN, text, use_fallback=True)


__all__ = ["Sam3BusyStatusIndicator"]
