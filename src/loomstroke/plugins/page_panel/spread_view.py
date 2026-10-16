"""Read-only spread preview widgets for the Pages panel (CT-06 Phase B2).

Thin display adapters over the B1 ``book_spread_layout`` resolver: these
widgets render ``SpreadDescriptor`` slots exactly as resolved — no page
order math lives here.  Everything is runtime-only preview state: no
metadata, settings, or ``.lstroke`` payload is read or written, empty
slots never become pages, and activating a real slot goes through the
existing ``PageService.set_active_page`` path owned by the panel.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.book_spread_layout import SpreadDescriptor
from loomstroke.ui.translations import translated_literal_domain

if TYPE_CHECKING:
    from loomstroke.plugins.page_panel.panel_plugin import PageEntry


def _ts(text: str) -> str:
    return translated_literal_domain("PagePanelPlugin", text, use_fallback=True)


def _side_label(physical_side: str) -> str:
    """Physical side display text (kept distinct from binding labels)."""
    return _ts("Left Side") if physical_side == "left" else _ts("Right Side")


class SpreadSlotWidget(QFrame):
    """One physical slot of a spread card.

    A *real* slot shows the page number, name, thumbnail/placeholder,
    dirty marker, and an active indicator, and can be activated by mouse
    click or Enter/Space.  An *empty* slot is a clearly marked blank: it
    has no page id, accepts no activation, and never calls services.
    """

    activate_requested = Signal(str)
    context_menu_requested = Signal(str, QPoint)
    #: Empty slot activated (click / Enter / Space): the panel asks whether
    #: to insert a blank page here (CT-06 §6.2, no drag path).
    insert_requested = Signal()

    def __init__(
        self,
        *,
        spread_number: int,
        physical_side: str,
        entry: PageEntry | None,
        thumbnail_width: int,
        thumbnail_height: int,
        show_dirty: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.page_id: str | None = entry.page_id if entry is not None else None
        self._spread_number = int(spread_number)
        self._physical_side = physical_side
        self.physical_side = physical_side
        self._page_label_text = ""
        self._active = False
        self._show_dirty = bool(show_dirty)
        self._thumbnail_width = max(32, int(thumbnail_width))
        self._thumbnail_height = max(32, int(thumbnail_height))

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setLineWidth(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(3)
        layout.addLayout(self._build_header())

        self.thumbnail_label = QLabel(self)
        self.thumbnail_label.setFixedSize(self._thumbnail_width, self._thumbnail_height)
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.thumbnail_label, 0, Qt.AlignmentFlag.AlignHCenter)

        self.side_label = QLabel(self)
        side_font = QFont()
        side_font.setPointSize(8)
        self.side_label.setFont(side_font)
        self.side_label.setStyleSheet("color: #909090;")
        self.side_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        layout.addWidget(self.side_label, 0, Qt.AlignmentFlag.AlignHCenter)

        if entry is not None:
            self._populate_real_slot(entry)
        else:
            self._populate_empty_slot()
        self.side_label.setText(_side_label(physical_side))
        self._refresh_accessibility()

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(4)

        self.active_marker = QLabel(self)
        self.active_marker.setText("▶")  # non-color active indicator
        marker_font = QFont()
        marker_font.setBold(True)
        self.active_marker.setFont(marker_font)
        self.active_marker.setVisible(False)
        header.addWidget(self.active_marker, 0, Qt.AlignmentFlag.AlignTop)

        self.title_label = QLabel(self)
        title_font = QFont()
        title_font.setPointSize(9)
        title_font.setBold(True)
        self.title_label.setFont(title_font)
        self.title_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        header.addWidget(self.title_label, 1)

        self.dirty_label = QLabel(self)
        self.dirty_label.setText("*")
        self.dirty_label.setStyleSheet("color: #e53935; font-weight: bold;")
        self.dirty_label.setVisible(False)
        header.addWidget(self.dirty_label, 0, Qt.AlignmentFlag.AlignTop)
        return header

    def _populate_real_slot(self, entry: PageEntry) -> None:
        self._page_label_text = entry.name if entry.name else f"Page {entry.index + 1}"
        self.title_label.setText(f"{entry.index + 1}. {entry.name}")
        self.set_dirty(entry.dirty)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.thumbnail_label.setStyleSheet("background-color: #3a3a3a; border: 1px solid #5a5a5a;")
        self._apply_placeholder()

    def _populate_empty_slot(self) -> None:
        # Empty slot: a clearly marked blank.  Activating it offers to
        # insert a blank page (confirmation lives in the panel).
        self.title_label.setText(_ts("Empty"))
        self.title_label.setStyleSheet("color: #808080; font-style: italic;")
        self.setStyleSheet("SpreadSlotWidget { border: 1px dashed #5a5a5a; }")
        self.thumbnail_label.setStyleSheet("background-color: transparent;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip(_ts("Insert a blank page here"))

    # ------------------------------------------------------------------
    def is_empty(self) -> bool:
        return self.page_id is None

    def set_active(self, active: bool) -> None:
        if self.page_id is None:
            return
        self._active = bool(active)
        self.active_marker.setVisible(self._active)
        self.setLineWidth(3 if self._active else 1)
        self.setProperty("spread_slot_active", self._active)
        style = self.style()
        if style is not None:
            style.unpolish(self)
            style.polish(self)
        self._refresh_accessibility()

    def is_active(self) -> bool:
        return self._active

    def set_dirty(self, dirty: bool) -> None:
        if self.page_id is None:
            return
        self.dirty_label.setVisible(self._show_dirty and bool(dirty))

    def set_folio(self, folio: str | None) -> None:
        """Show the display folio on the side line; ``None`` hides it."""
        if self.page_id is None:
            return
        base = _side_label(self._physical_side)
        self.side_label.setText(f"{base} · {folio}" if folio else base)

    def set_thumbnail(self, pixmap: QPixmap | None) -> None:
        if self.page_id is None:
            return
        if pixmap is None or pixmap.isNull():
            self._apply_placeholder()
            return
        scaled = pixmap.scaled(
            self._thumbnail_width,
            self._thumbnail_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.thumbnail_label.setPixmap(scaled)

    # ------------------------------------------------------------------
    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            if self.page_id is not None:
                self.activate_requested.emit(self.page_id)
            else:
                self.insert_requested.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def contextMenuEvent(self, event) -> None:  # type: ignore[override]
        # Without this the right click bubbles up to the QDockWidget and
        # the user gets Qt's dock menu ("Float") instead of the page menu.
        if self.page_id is not None:
            event.accept()
            self.context_menu_requested.emit(self.page_id, event.globalPos())
            return
        super().contextMenuEvent(event)

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Space,
        ):
            if self.page_id is not None:
                self.activate_requested.emit(self.page_id)
            else:
                self.insert_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    def _apply_placeholder(self) -> None:
        pixmap = QPixmap(self._thumbnail_width, self._thumbnail_height)
        pixmap.fill(QColor(58, 58, 58))
        self.thumbnail_label.setPixmap(pixmap)

    def _refresh_accessibility(self) -> None:
        side = _side_label(self._physical_side)
        if self.page_id is None:
            name = _ts("Spread {number}, {side}, {page}").format(
                number=self._spread_number,
                side=side,
                page=_ts("Empty"),
            )
            self.setAccessibleName(name)
            self.setAccessibleDescription(_ts("Empty"))
            return
        # Custom page names are user strings and are never translated.
        name = _ts("Spread {number}, {side}, {page}").format(
            number=self._spread_number,
            side=side,
            page=self._page_label_text,
        )
        self.setAccessibleName(name)
        self.setAccessibleDescription(_ts("Active Page") if self._active else "")


class SpreadCardWidget(QWidget):
    """One spread: a header plus the physical (left, right) slots."""

    #: (neighbour page id, empty physical side) — the panel decides what
    #: "insert here" means for the current binding (CT-06 §6.2).
    empty_slot_insert_requested = Signal(str, str)

    def __init__(
        self,
        descriptor: SpreadDescriptor,
        entries_by_id: Mapping[str, PageEntry],
        *,
        thumbnail_width: int,
        thumbnail_height: int,
        show_dirty: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        spread_number = descriptor.index + 1
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 6)
        layout.setSpacing(3)

        self.header_label = QLabel(self)
        header_font = QFont()
        header_font.setPointSize(8)
        header_font.setBold(True)
        self.header_label.setFont(header_font)
        self.header_label.setStyleSheet("color: #b0b0b0;")
        self.header_label.setText(_ts("Spread {number}").format(number=spread_number))
        layout.addWidget(self.header_label)

        slots_layout = QHBoxLayout()
        slots_layout.setContentsMargins(0, 0, 0, 0)
        slots_layout.setSpacing(6)
        self.slot_widgets: list[SpreadSlotWidget] = []
        for slot in descriptor.slots:  # always physical (left, right)
            entry = entries_by_id.get(slot.page_id) if slot.page_id is not None else None
            widget = SpreadSlotWidget(
                spread_number=spread_number,
                physical_side=slot.physical_side,
                entry=entry,
                thumbnail_width=thumbnail_width,
                thumbnail_height=thumbnail_height,
                show_dirty=show_dirty,
                parent=self,
            )
            self.slot_widgets.append(widget)
            slots_layout.addWidget(widget, 1)
        layout.addLayout(slots_layout)
        for widget in self.slot_widgets:
            if widget.page_id is None:
                widget.insert_requested.connect(
                    lambda side=widget.physical_side: self._on_empty_slot_insert(side)
                )

    def _on_empty_slot_insert(self, empty_side: str) -> None:
        neighbours = [w.page_id for w in self.slot_widgets if w.page_id is not None]
        if len(neighbours) == 1:
            self.empty_slot_insert_requested.emit(neighbours[0], str(empty_side))


class SpreadListView(QWidget):
    """Vertical, read-only list of spread cards in logical order.

    Card order always follows the logical page order top-to-bottom —
    right binding mirrors slot sides only, never the card order.  The
    list never allows selection, dragging, or item movement.
    """

    page_activate_requested = Signal(str)
    page_context_menu_requested = Signal(str, QPoint)
    empty_slot_insert_requested = Signal(str, str)

    def __init__(
        self,
        *,
        thumbnail_width: int,
        thumbnail_height: int,
        show_dirty: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._thumbnail_width = int(thumbnail_width)
        self._thumbnail_height = int(thumbnail_height)
        self._show_dirty = bool(show_dirty)
        self._slots_by_page: dict[str, SpreadSlotWidget] = {}
        self._empty_slot_count = 0
        self._active_page_id: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.list_widget = QListWidget(self)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.list_widget.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.setViewMode(QListWidget.ViewMode.ListMode)
        self.list_widget.setFlow(QListWidget.Flow.TopToBottom)
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.list_widget, 1)

    # ------------------------------------------------------------------
    def set_spreads(
        self,
        spreads: Sequence[SpreadDescriptor],
        entries_by_id: Mapping[str, PageEntry],
    ) -> None:
        """Rebuild the cards from resolved descriptors (display only)."""
        self.list_widget.clear()
        self._slots_by_page.clear()
        self._empty_slot_count = 0
        for descriptor in spreads:
            card = SpreadCardWidget(
                descriptor,
                entries_by_id,
                thumbnail_width=self._thumbnail_width,
                thumbnail_height=self._thumbnail_height,
                show_dirty=self._show_dirty,
            )
            for slot_widget in card.slot_widgets:
                if slot_widget.page_id is not None:
                    self._slots_by_page[slot_widget.page_id] = slot_widget
                    slot_widget.activate_requested.connect(self.page_activate_requested)
                    slot_widget.context_menu_requested.connect(self.page_context_menu_requested)
                else:
                    self._empty_slot_count += 1
            card.empty_slot_insert_requested.connect(self.empty_slot_insert_requested)
            item = QListWidgetItem(self.list_widget)
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            item.setSizeHint(card.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, card)
        if self._active_page_id is not None:
            self.set_active_page(self._active_page_id)

    def set_active_page(self, page_id: str) -> None:
        self._active_page_id = page_id
        for slot_page_id, widget in self._slots_by_page.items():
            widget.set_active(slot_page_id == page_id)

    def update_dirty(self, page_id: str, dirty: bool) -> None:
        widget = self._slots_by_page.get(page_id)
        if widget is not None:
            widget.set_dirty(dirty)

    def update_thumbnail(self, page_id: str, pixmap: QPixmap | None) -> None:
        widget = self._slots_by_page.get(page_id)
        if widget is not None:
            widget.set_thumbnail(pixmap)

    def update_folio(self, page_id: str, folio: str | None) -> None:
        widget = self._slots_by_page.get(page_id)
        if widget is not None:
            widget.set_folio(folio)

    # ------------------------------------------------------------------
    # Introspection helpers (used by tests and the panel)
    def slot_for_page(self, page_id: str) -> SpreadSlotWidget | None:
        return self._slots_by_page.get(page_id)

    def real_page_ids(self) -> tuple[str, ...]:
        return tuple(self._slots_by_page)

    def empty_slot_count(self) -> int:
        return self._empty_slot_count

    def card_at(self, row: int) -> SpreadCardWidget | None:
        item = self.list_widget.item(row)
        if item is None:
            return None
        widget = self.list_widget.itemWidget(item)
        return widget if isinstance(widget, SpreadCardWidget) else None

    def card_count(self) -> int:
        return self.list_widget.count()


__all__ = ["SpreadCardWidget", "SpreadListView", "SpreadSlotWidget"]
