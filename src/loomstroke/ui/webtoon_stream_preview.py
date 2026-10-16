"""Virtual vertical-scroll preview of the Webtoon stream (CT-12 §6.1 / §13 / §14).

A scroll area whose content is the :class:`VerticalStreamPlan` scaled to
the viewport width.  Only the spans that intersect the viewport are
painted, from thumbnails supplied by a provider callback (medium
resolution, bounded cache) — no stream-sized image ever exists.  Slice
boundaries are drawn as numbered dashed lines whose style (not only
colour) tells safe from unverified.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPaintEvent, QPen, QResizeEvent
from PySide6.QtWidgets import QAbstractScrollArea, QWidget

from ..core.webtoon.slice_plan import SlicePlan
from ..core.webtoon.vertical_stream_plan import VerticalStreamPlan

__all__ = ["WebtoonStreamPreview"]

ThumbnailProvider = Callable[[str], QImage | None]


class WebtoonStreamPreview(QAbstractScrollArea):
    """Paint visible spans + slice boundaries; scroll through the whole stream."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._stream: VerticalStreamPlan | None = None
        self._slices: tuple[SlicePlan, ...] = ()
        self._provider: ThumbnailProvider | None = None
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.verticalScrollBar().valueChanged.connect(lambda _v: self.viewport().update())
        self.setMinimumSize(160, 200)

    # -- API ---------------------------------------------------------------------

    def set_plan(self, stream: VerticalStreamPlan | None, slices: Sequence[SlicePlan] = ()) -> None:
        self._stream = stream
        self._slices = tuple(slices)
        self._update_scrollbar()
        self.viewport().update()

    def set_thumbnail_provider(self, provider: ThumbnailProvider | None) -> None:
        self._provider = provider
        self.viewport().update()

    def stream(self) -> VerticalStreamPlan | None:
        return self._stream

    def scale(self) -> float:
        if self._stream is None or self._stream.width <= 0:
            return 1.0
        return max(1, self.viewport().width()) / self._stream.width

    def visible_stream_range(self) -> tuple[int, int]:
        """Stream rows currently inside the viewport."""
        if self._stream is None:
            return (0, 0)
        scale = self.scale()
        top = self.verticalScrollBar().value() / scale
        bottom = top + self.viewport().height() / scale
        return (int(top), min(self._stream.height, int(bottom) + 1))

    def visible_page_ids(self) -> tuple[str, ...]:
        if self._stream is None:
            return ()
        y0, y1 = self.visible_stream_range()
        return tuple(span.page_id for span in self._stream.spans_between(y0, y1))

    def scroll_to_stream_y(self, stream_y: int) -> None:
        self.verticalScrollBar().setValue(int(stream_y * self.scale()))

    def sizeHint(self) -> QSize:  # noqa: N802
        return QSize(320, 480)

    # -- internals ---------------------------------------------------------------

    def _update_scrollbar(self) -> None:
        bar = self.verticalScrollBar()
        if self._stream is None:
            bar.setRange(0, 0)
            return
        content_height = int(self._stream.height * self.scale())
        bar.setRange(0, max(0, content_height - self.viewport().height()))
        bar.setPageStep(max(1, self.viewport().height()))
        bar.setSingleStep(24)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._update_scrollbar()

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        del event
        painter = QPainter(self.viewport())
        try:
            painter.fillRect(self.viewport().rect(), self.palette().window())
            if self._stream is None:
                return
            scale = self.scale()
            offset = self.verticalScrollBar().value()
            width = self.viewport().width()
            y0, y1 = self.visible_stream_range()
            for span in self._stream.spans_between(y0, y1):
                top = span.stream_y0 * scale - offset
                height = max(1.0, span.height * scale)
                rect = QRectF(0.0, top, float(width), height)
                image = self._provider(span.page_id) if self._provider is not None else None
                if image is not None and not image.isNull():
                    painter.drawImage(rect, image)
                else:
                    painter.fillRect(rect, QColor(210, 210, 210))
                    painter.setPen(QPen(QColor(90, 90, 90)))
                    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, str(span.page_id))
                painter.setPen(QPen(QColor(150, 150, 150), 1.0))
                painter.drawRect(rect)
            self._paint_boundaries(painter, scale, offset, width)
        finally:
            painter.end()

    def _paint_boundaries(self, painter: QPainter, scale: float, offset: int, width: int) -> None:
        assert self._stream is not None
        for plan in self._slices:
            if plan.stream_y1 >= self._stream.height:
                continue
            y = plan.stream_y1 * scale - offset
            if y < -2 or y > self.viewport().height() + 2:
                continue
            pen = QPen(QColor(40, 140, 60) if plan.confidence >= 1.0 else QColor(200, 40, 40))
            pen.setWidthF(1.5)
            pen.setStyle(Qt.PenStyle.SolidLine if plan.confidence >= 1.0 else Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(0, int(y), width, int(y))
            label = f"{plan.index + 1}"
            if plan.warnings:
                label += " !"
            painter.drawText(4, int(y) - 3, label)
