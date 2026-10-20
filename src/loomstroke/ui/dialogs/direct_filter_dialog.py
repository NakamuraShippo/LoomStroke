"""Whole-layer filter parameters and detached result preview."""

from __future__ import annotations

import logging
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.direct_filter_session import DirectFilterSession
from loomstroke.core.filters import FilterRegistry
from loomstroke.plugins.filter_pen.param_widgets import FilterParameterForm
from loomstroke.plugins.filter_pen.translations import filter_text
from loomstroke.ui.translations import translated_literal_domain

logger = logging.getLogger(__name__)


def ts(source: str) -> str:
    return translated_literal_domain("DirectFilter", source, use_fallback=True)


class DirectFilterDialog(QDialog):
    def __init__(
        self,
        canvas: Any,
        filter_type: str,
        *,
        params: dict[str, Any] | None = None,
        parent: QWidget | None = None,
        processor_factory: Callable[[], Any] | None = None,
        auto_apply: bool = False,
        rasterize: bool = False,
    ) -> None:
        super().__init__(parent)
        filter_class = FilterRegistry.get(filter_type)
        if filter_class is None:
            raise ValueError("The selected filter is not available.")
        self._filter_name = filter_class().name()
        if processor_factory is None:
            from loomstroke.ui.canvas.wgpu_backend.filter_pen_processor import (  # noqa: PLC0415
                FilterPenGpuProcessor,
            )

            processor_factory = FilterPenGpuProcessor
        from loomstroke.ui.direct_filter_raster_source import prepare_filter_raster  # noqa: PLC0415

        target = prepare_filter_raster(canvas) if rasterize else None
        self.session = DirectFilterSession(
            canvas, processor_factory, self, rasterized_target=target
        )
        self.filter_type = filter_type
        self._auto_apply = auto_apply
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        self.preview = QLabel(self)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(160)
        layout.addWidget(self.preview)
        self.form = FilterParameterForm(self)
        self.form.set_filter(filter_type, deepcopy(params or {}))
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.form)
        layout.addWidget(scroll, 1)
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        layout.addWidget(self.buttons)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(150)
        self._timer.timeout.connect(self._request)
        self.form.changed.connect(self._changed)
        self.session.ready.connect(self._ready)
        self.session.failed.connect(self._failed)
        self.finished.connect(lambda _: self.session.close())
        self._status_source = ""
        self.retranslate()
        self._changed()
        self.resize(480, min(760, 380 + self.form.sizeHint().height()))

    def _changed(self, *_args: Any) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self._status_source = "Preparing filter preview…"
        self.status.setText(ts(self._status_source))
        self._timer.start()

    def _request(self) -> None:
        self.session.request(self.filter_type, self.form.values())

    def _ready(self, image: QImage) -> None:
        # A result for settings changed during debounce must never re-enable OK.
        if self._timer.isActive() or self.form.values() != self.session.params:
            return
        self.preview.setPixmap(QPixmap.fromImage(image))
        self._status_source = "Apply to the current layer. Undo restores the original pixels."
        self.status.setText(ts(self._status_source))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        if self._auto_apply:
            self.accept()

    def _failed(self, source: str) -> None:
        self._status_source = source
        self.status.setText(ts(source))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)

    def accept(self) -> None:
        if not self.buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled():
            return
        try:
            if not self.session.commit():
                raise ValueError(
                    "The filter could not be applied. The original pixels were preserved."
                )
        except Exception:
            logger.exception("Direct filter commit failed")
            self._failed("The filter could not be applied. The original pixels were preserved.")
            return
        super().accept()

    def done(self, result: int) -> None:
        self._timer.stop()
        self.session.close()
        super().done(result)

    def retranslate(self) -> None:
        self.setWindowTitle(filter_text(self._filter_name))
        self.status.setText(ts(self._status_source))

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "status"):
            self.retranslate()
