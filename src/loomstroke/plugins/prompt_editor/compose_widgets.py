"""Interactive Live Prompt widgets for the Compose tab.

Provides a chip-based flow layout where each prompt component is displayed
as inline text.  Hovering highlights the chip; clicking opens an edit popup.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QMimeData, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import (
    QDrag,
    QDragEnterEvent,
    QDragMoveEvent,
    QDropEvent,
    QMouseEvent,
    QPalette,
)
from PySide6.QtWidgets import (  # noqa: F401
    QApplication,
    QComboBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QMenu,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
    QWidgetItem,
)

from loomstroke.plugins.common.constants import CK_LIST, CK_PROMPT, CK_STRING, CK_TEXT
from loomstroke.ui.translations import translated_literal_domain

from .selection_controls import PromptSelectionControls
from .wrapped_chip_label import WrappedChipLabel

if TYPE_CHECKING:
    from loomstroke.plugins.prompt_editor.panel_plugin import (
        PromptComponent,
        SnippetCatalog,
    )

logger = logging.getLogger(__name__)

COMPONENT_MIME_TYPE = "application/x-loomstroke-component-kind"
CHIP_MIME_TYPE = "application/x-loomstroke-chip-index"


def _chip_ts(text: str) -> str:
    return translated_literal_domain("PromptChipWidget", text, use_fallback=True)


def _configure_compact_combo(combo: QComboBox, *, min_chars: int = 6) -> None:
    combo.setMinimumWidth(0)
    combo.setMinimumContentsLength(min_chars)
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)


# ---------------------------------------------------------------------------
# FlowLayout
# ---------------------------------------------------------------------------


class FlowLayout(QLayout):
    """Layout that arranges widgets horizontally, wrapping to the next line."""

    def __init__(self, parent: QWidget | None = None, spacing: int = 4) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._spacing = spacing

    def addItem(self, item: QLayoutItem) -> None:  # noqa: N802
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items[index]
        return None

    def takeAt(self, index: int) -> QLayoutItem | None:  # noqa: N802
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def spacing(self) -> int:
        return self._spacing

    def setSpacing(self, spacing: int) -> None:  # noqa: N802
        self._spacing = spacing

    def hasHeightForWidth(self) -> bool:  # noqa: N802
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802
        return self._do_layout(QRect(0, 0, width, 0), test_only=True)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802
        super().setGeometry(rect)
        self._do_layout(rect, test_only=False)

    def sizeHint(self) -> QSize:  # noqa: N802
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802
        # Return a small minimum so the parent scroll area can shrink freely.
        margins = self.contentsMargins()
        return QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _do_layout(self, rect: QRect, *, test_only: bool) -> int:
        margins = self.contentsMargins()
        effective = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x = effective.x()
        y = effective.y()
        line_height = 0
        spacing = self._spacing

        for item in self._items:
            hint = item.sizeHint()
            width = min(hint.width(), max(1, effective.width()))
            height = item.heightForWidth(width) if item.hasHeightForWidth() else hint.height()
            hint = QSize(width, max(1, height if height >= 0 else hint.height()))
            next_x = x + hint.width() + spacing
            if x > effective.x() and next_x - spacing > effective.right():
                x = effective.x()
                y = y + line_height + spacing
                next_x = x + hint.width() + spacing
                line_height = 0
            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x = next_x
            line_height = max(line_height, hint.height())

        return y + line_height - rect.y() + margins.bottom()


# ---------------------------------------------------------------------------
# PromptChipWidget
# ---------------------------------------------------------------------------


class PromptChipWidget(QFrame):
    """Displays a single PromptComponent as inline text with hover highlight."""

    removed = Signal(object)
    changed = Signal(object)
    expand_requested = Signal(object)

    def __init__(
        self,
        component: PromptComponent,
        catalog: SnippetCatalog,
        render_func: object,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._component = component
        self._catalog = catalog
        self._render_func = render_func  # callable(PromptComponent) -> str
        self._popup: ChipEditPopup | None = None
        self._drag_start: QPoint | None = None

        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMouseTracking(True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(2, 1, 2, 1)
        layout.setSpacing(0)

        self._label = WrappedChipLabel(self)
        self._label.setTextFormat(Qt.TextFormat.PlainText)
        self._label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        layout.addWidget(self._label)

        self._update_display_text()

    @property
    def component(self) -> PromptComponent:
        return self._component

    def refresh_catalog(self, catalog: SnippetCatalog) -> None:
        self._catalog = catalog
        self._update_display_text()

    def _update_display_text(self) -> None:
        text = self._render_func(self._component)
        self._label.setText(text if text else "\u2026")
        self.updateGeometry()

    # --- hover highlight ---

    def enterEvent(self, event: object) -> None:  # type: ignore[override]  # PySide6 override stub limitation
        pal = self.palette()
        highlight = pal.color(QPalette.ColorRole.Highlight)
        highlight.setAlpha(40)
        r, g, b, a = highlight.red(), highlight.green(), highlight.blue(), highlight.alpha()
        self.setStyleSheet(
            f"PromptChipWidget {{ background: rgba({r},{g},{b},{a}); border-radius: 3px; }}"
        )
        super().enterEvent(event)

    def leaveEvent(self, event: object) -> None:  # type: ignore[override]  # PySide6 override stub limitation
        self.setStyleSheet("")
        super().leaveEvent(event)

    # --- click → popup ---

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]  # PySide6 override stub limitation
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.pos()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]  # PySide6 override stub limitation
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self._drag_start is not None
            and (event.pos() - self._drag_start).manhattanLength()
            < QApplication.startDragDistance()
        ):
            self._show_popup()
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]  # PySide6 override stub limitation
        if (
            self._drag_start is not None
            and (event.pos() - self._drag_start).manhattanLength()
            >= QApplication.startDragDistance()
        ):
            self._start_drag()
            self._drag_start = None
            return
        super().mouseMoveEvent(event)

    # --- drag ---

    def _start_drag(self) -> None:
        flow = self.parent()
        if flow is None:
            return
        # Find our index among chip siblings
        chip_index = -1
        idx = 0
        for child in flow.children():
            if isinstance(child, PromptChipWidget):
                if child is self:
                    chip_index = idx
                    break
                idx += 1
        if chip_index < 0:
            return
        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(CHIP_MIME_TYPE, str(chip_index).encode("utf-8"))
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.MoveAction)

    # --- popup ---

    def _show_popup(self) -> None:
        if self._popup is not None:
            self._popup.close()
        popup = ChipEditPopup(self._component, self._catalog, parent=self)
        popup.changed.connect(self._on_popup_changed)
        popup.delete_requested.connect(self._on_delete)
        popup.expand_requested.connect(self._on_expand)

        # Position below this chip
        global_pos = self.mapToGlobal(QPoint(0, self.height()))
        # Clamp to screen
        screen = self.screen()
        if screen is not None:
            screen_rect = screen.availableGeometry()
            popup_size = popup.sizeHint()
            if global_pos.x() + popup_size.width() > screen_rect.right():
                global_pos.setX(screen_rect.right() - popup_size.width())
            if global_pos.y() + popup_size.height() > screen_rect.bottom():
                global_pos = self.mapToGlobal(QPoint(0, -popup_size.height()))

        popup.move(global_pos)
        self._popup = popup
        popup.show()

    def _on_popup_changed(self, component: PromptComponent) -> None:
        self._update_display_text()
        self.changed.emit(component)

    def _on_delete(self) -> None:
        if self._popup is not None:
            self._popup.close()
            self._popup = None
        self.removed.emit(self)

    def _on_expand(self) -> None:
        if self._popup is not None:
            self._popup.close()
            self._popup = None
        self.expand_requested.emit(self)


# ---------------------------------------------------------------------------
# ChipEditPopup
# ---------------------------------------------------------------------------


class ChipEditPopup(QFrame):
    """Popup for editing a chip's component settings."""

    changed = Signal(object)
    delete_requested = Signal()
    expand_requested = Signal()

    def __init__(
        self,
        component: PromptComponent,
        catalog: SnippetCatalog,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self._component = component
        self._catalog = catalog
        self._tag_combo: QComboBox | None = None
        self._snippet_combo: QComboBox | None = None
        self._item_combo: QComboBox | None = None
        self._text_edit: QPlainTextEdit | None = None

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumWidth(220)
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        if self._component.kind in {CK_STRING, CK_LIST, CK_PROMPT}:
            form = QFormLayout()
            form.setContentsMargins(0, 0, 0, 0)
            form.setSpacing(4)

            self._tag_combo = QComboBox(self)
            _configure_compact_combo(self._tag_combo, min_chars=6)
            all_tags_text = _chip_ts("All tags")
            self._tag_combo.addItem(all_tags_text, None)
            tags = self._catalog.tags(self._component.kind)
            for tag in tags:
                self._tag_combo.addItem(tag, tag)
            if self._component.tag_filter:
                idx = self._tag_combo.findData(self._component.tag_filter)
                if idx >= 0:
                    self._tag_combo.setCurrentIndex(idx)
            self._tag_combo.currentIndexChanged.connect(self._on_tag_changed)
            form.addRow(_chip_ts("Tag"), self._tag_combo)

            self._snippet_combo = QComboBox(self)
            _configure_compact_combo(self._snippet_combo, min_chars=8)
            self._populate_snippet_combo()
            self._snippet_combo.currentIndexChanged.connect(self._on_snippet_changed)
            form.addRow(_chip_ts("Snippet"), self._snippet_combo)

            if self._component.kind == CK_LIST:
                self._item_combo = QComboBox(self)
                _configure_compact_combo(self._item_combo, min_chars=6)
                self._populate_item_combo()
                self._item_combo.currentIndexChanged.connect(self._on_item_changed)
                form.addRow(_chip_ts("Item"), self._item_combo)

            layout.addLayout(form)

        elif self._component.kind == CK_TEXT:
            self._text_edit = QPlainTextEdit(self)
            self._text_edit.setPlaceholderText(_chip_ts("Enter text..."))
            self._text_edit.setPlainText(self._component.text_value)
            self._text_edit.setMaximumHeight(100)
            self._text_edit.textChanged.connect(self._on_text_changed)
            layout.addWidget(self._text_edit)

        self._selection_controls = PromptSelectionControls(self._component.selection_mode, self)
        self._selection_controls.modeChanged.connect(self._on_selection_mode_changed)
        layout.addWidget(self._selection_controls)
        layout.addLayout(self._build_action_row())

    def _on_selection_mode_changed(self, mode: str) -> None:
        self._component.selection_mode = mode
        self.changed.emit(self._component)

    def _build_action_row(self) -> QHBoxLayout:
        action_row = QHBoxLayout()
        if self._component.kind == CK_PROMPT:
            expand_btn = QPushButton(self)
            expand_btn.setIcon(
                self.style().standardIcon(QStyle.SP_ToolBarHorizontalExtensionButton)
            )
            expand_btn.setText(_chip_ts("Expand"))
            expand_btn.setFlat(True)
            expand_btn.clicked.connect(self.expand_requested.emit)
            action_row.addWidget(expand_btn)
        action_row.addStretch(1)
        delete_btn = QPushButton(self)
        delete_btn.setIcon(self.style().standardIcon(QStyle.SP_TrashIcon))
        delete_btn.setText(_chip_ts("Delete"))
        delete_btn.setFlat(True)
        delete_btn.clicked.connect(self.delete_requested.emit)
        action_row.addWidget(delete_btn)
        return action_row

    def _populate_snippet_combo(self) -> None:
        if self._snippet_combo is None:
            return
        tag = self._tag_combo.currentData() if self._tag_combo else None
        snippets = self._catalog.iter(self._component.kind, tag=tag)
        current = self._component.snippet_id

        self._snippet_combo.blockSignals(True)
        self._snippet_combo.clear()
        for snippet in snippets:
            title = snippet.title or snippet.id
            self._snippet_combo.addItem(title, snippet.id)
        if current:
            idx = self._snippet_combo.findData(current)
            if idx >= 0:
                self._snippet_combo.setCurrentIndex(idx)
            elif self._snippet_combo.count():
                self._snippet_combo.setCurrentIndex(0)
        elif self._snippet_combo.count():
            self._snippet_combo.setCurrentIndex(0)
        self._snippet_combo.blockSignals(False)

        # Sync component
        if self._snippet_combo.count():
            self._component.snippet_id = self._snippet_combo.currentData()
        else:
            self._component.snippet_id = None

    def _populate_item_combo(self) -> None:
        if self._item_combo is None:
            return

        from loomstroke.plugins.prompt_editor.panel_plugin import (  # noqa: PLC0415
            _resolve_list_items,
        )

        snippet = self._catalog.get(self._component.snippet_id)
        items = _resolve_list_items(snippet)

        self._item_combo.blockSignals(True)
        self._item_combo.clear()
        for i, item_text in enumerate(items):
            self._item_combo.addItem(item_text, i)
        current = self._component.list_index or 0
        if 0 <= current < self._item_combo.count():
            self._item_combo.setCurrentIndex(current)
        self._item_combo.blockSignals(False)

    def _on_tag_changed(self, _index: int) -> None:
        if self._tag_combo is not None:
            self._component.tag_filter = self._tag_combo.currentData()
        self._populate_snippet_combo()
        if self._component.kind == CK_LIST:
            self._populate_item_combo()
        self.changed.emit(self._component)

    def _on_snippet_changed(self, _index: int) -> None:
        if self._snippet_combo is not None:
            self._component.snippet_id = self._snippet_combo.currentData()
        if self._component.kind == CK_LIST:
            self._component.list_index = 0
            self._populate_item_combo()
        self.changed.emit(self._component)

    def _on_item_changed(self, _index: int) -> None:
        if self._item_combo is not None:
            self._component.list_index = self._item_combo.currentIndex()
        self.changed.emit(self._component)

    def _on_text_changed(self) -> None:
        if self._text_edit is not None:
            self._component.text_value = self._text_edit.toPlainText()
        self.changed.emit(self._component)


# ---------------------------------------------------------------------------
# PromptChipFlowWidget
# ---------------------------------------------------------------------------


class PromptChipFlowWidget(QScrollArea):
    """Scrollable flow area that displays chips and accepts drops."""

    chipInserted = Signal(str, int)  # (component_kind, insert_index)
    chipMoved = Signal(int, int)  # (from_index, to_index)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setAcceptDrops(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setMinimumHeight(32)

        self._inner = QWidget(self)
        self._flow = FlowLayout(self._inner, spacing=2)
        self._flow.setContentsMargins(6, 6, 6, 6)
        self._inner.setLayout(self._flow)
        self.setWidget(self._inner)

        self._chips: list[PromptChipWidget] = []
        self._separator_labels: list[QLabel] = []
        self._add_button = QToolButton(self._inner)
        self._add_button.setText("+")
        self._add_button.setToolTip(_chip_ts("Add component"))
        self._add_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_button.clicked.connect(self._on_add_clicked)
        self._flow.addWidget(self._add_button)

        self._separator_text = ", "

    @property
    def chips(self) -> list[PromptChipWidget]:
        return list(self._chips)

    def set_separator_text(self, text: str) -> None:
        """Update the visual separator between chips."""
        self._separator_text = text
        for label in self._separator_labels:
            label.setText(self._separator_text)

    def rebuild(
        self,
        components: list[PromptComponent],
        catalog: SnippetCatalog,
        render_func: object,
    ) -> list[PromptChipWidget]:
        """Clear and rebuild all chips from component list."""
        # Remove existing
        self._clear_all()

        chips: list[PromptChipWidget] = []
        for i, comp in enumerate(components):
            if i > 0:
                sep = self._make_separator()
                self._flow.addWidget(sep)
                self._separator_labels.append(sep)

            chip = PromptChipWidget(comp, catalog, render_func, self._inner)
            self._flow.addWidget(chip)
            chips.append(chip)

        self._chips = chips

        # Re-add the + button at the end
        self._flow.removeWidget(self._add_button)
        self._flow.addWidget(self._add_button)

        self._inner.updateGeometry()
        return chips

    def insert_chip(
        self,
        index: int,
        component: PromptComponent,
        catalog: SnippetCatalog,
        render_func: object,
    ) -> PromptChipWidget:
        """Insert a single chip at the given index without full rebuild."""
        chip = PromptChipWidget(component, catalog, render_func, self._inner)

        # chips[0] at layout 0, sep at 1, chips[1] at 2, sep at 3, ...
        layout_pos = 0 if index <= 0 else index * 2

        # Insert separator if not the first chip
        if self._chips:
            sep = self._make_separator()
            if index <= 0:
                # Insert chip at 0, separator at 1
                self._insert_layout_widget(0, chip)
                self._insert_layout_widget(1, sep)
            else:
                # Insert separator then chip
                self._insert_layout_widget(layout_pos - 1, sep)
                self._insert_layout_widget(layout_pos, chip)
            self._separator_labels.insert(max(0, index - 1), sep)
        else:
            self._insert_layout_widget(0, chip)

        self._chips.insert(index, chip)
        self._inner.updateGeometry()
        return chip

    def remove_chip(self, chip: PromptChipWidget) -> int:
        """Remove a chip and its adjacent separator. Returns removed index."""
        if chip not in self._chips:
            return -1
        index = self._chips.index(chip)
        self._chips.remove(chip)

        # Remove the chip from layout
        self._flow.removeWidget(chip)
        chip.setParent(None)
        chip.deleteLater()

        # Remove adjacent separator
        if self._separator_labels:
            sep_idx = max(0, index - 1) if index > 0 else 0
            if sep_idx < len(self._separator_labels):
                sep = self._separator_labels.pop(sep_idx)
                self._flow.removeWidget(sep)
                sep.setParent(None)
                sep.deleteLater()

        self._inner.updateGeometry()
        return index

    def _clear_all(self) -> None:
        for chip in self._chips:
            self._flow.removeWidget(chip)
            chip.setParent(None)
            chip.deleteLater()
        for sep in self._separator_labels:
            self._flow.removeWidget(sep)
            sep.setParent(None)
            sep.deleteLater()
        self._chips.clear()
        self._separator_labels.clear()

    def _make_separator(self) -> QLabel:
        sep = QLabel(self._separator_text, self._inner)
        sep.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        pal = sep.palette()
        sep.setStyleSheet(
            f"color: rgba({pal.color(QPalette.ColorRole.Text).red()},"
            f"{pal.color(QPalette.ColorRole.Text).green()},"
            f"{pal.color(QPalette.ColorRole.Text).blue()},120);"
        )
        return sep

    def _insert_layout_widget(self, position: int, widget: QWidget) -> None:
        """Insert a widget into the FlowLayout at a specific position."""
        # FlowLayout doesn't support insert, so rebuild order
        items: list[QLayoutItem] = []
        while self._flow.count():
            item = self._flow.takeAt(0)
            if item is not None:
                items.append(item)
        items.insert(position, QWidgetItem(widget))
        for item in items:
            self._flow.addItem(item)

    # --- drag & drop ---

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # type: ignore[override]  # PySide6 override stub limitation
        mime = event.mimeData()
        if mime.hasFormat(COMPONENT_MIME_TYPE) or mime.hasFormat(CHIP_MIME_TYPE):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:  # type: ignore[override]  # PySide6 override stub limitation
        mime = event.mimeData()
        if mime.hasFormat(COMPONENT_MIME_TYPE) or mime.hasFormat(CHIP_MIME_TYPE):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:  # type: ignore[override]  # PySide6 override stub limitation
        mime = event.mimeData()
        drop_index = self._index_at_pos(event.position().toPoint())

        if mime.hasFormat(CHIP_MIME_TYPE):
            # Reorder
            raw = bytes(mime.data(CHIP_MIME_TYPE)).decode("utf-8")
            from_index = int(raw)
            if from_index != drop_index:
                self.chipMoved.emit(from_index, drop_index)
            event.acceptProposedAction()
            return

        if mime.hasFormat(COMPONENT_MIME_TYPE):
            raw = bytes(mime.data(COMPONENT_MIME_TYPE)).decode("utf-8")
            kinds = [token for token in raw.split(",") if token]
            for offset, kind in enumerate(kinds):
                self.chipInserted.emit(kind, drop_index + offset)
            event.acceptProposedAction()
            return

        event.ignore()

    def _index_at_pos(self, pos: QPoint) -> int:
        """Determine chip insertion index from drop position."""
        mapped = self._inner.mapFrom(self, pos)
        for i, chip in enumerate(self._chips):
            geom = chip.geometry()
            if mapped.x() < geom.center().x() and mapped.y() <= geom.bottom():
                return i
        return len(self._chips)

    # --- add button ---

    def refresh_translations(self) -> None:
        """Update translatable text on language change."""
        self._add_button.setToolTip(_chip_ts("Add component"))

    def _on_add_clicked(self) -> None:
        menu = QMenu(self)
        for kind, label in [
            (CK_STRING, _chip_ts("String")),
            (CK_LIST, _chip_ts("List")),
            (CK_TEXT, _chip_ts("Text")),
            (CK_PROMPT, _chip_ts("Prompt")),
        ]:
            action = menu.addAction(label)
            action.setData(kind)
        chosen = menu.exec(self._add_button.mapToGlobal(QPoint(0, self._add_button.height())))
        if chosen is not None:
            self.chipInserted.emit(chosen.data(), len(self._chips))
