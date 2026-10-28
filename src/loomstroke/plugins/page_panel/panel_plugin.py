from __future__ import annotations

import contextlib
import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, Signal
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from shiboken6 import isValid

from loomstroke.core.book_layout_service import BookLayoutService
from loomstroke.core.book_spread_layout import (
    KNOWN_PAGE_ROLES,
    BindingDirection,
    BookLayout,
    resolve_spreads,
)
from loomstroke.core.events import Events
from loomstroke.core.folio_resolver import resolve_folios
from loomstroke.host_api import HostAPI, PageSummary, PageTemplateSummary
from loomstroke.plugins.page_panel.empty_slot_insert_controller import (
    EmptySlotInsertController,
)
from loomstroke.plugins.page_panel.operation_failure_notice import (
    PageOperationFailureNotice,
    dismiss_page_failure_notice,
    install_page_failure_notice,
    page_failure_checkpoint,
    request_widget_page_failure,
)
from loomstroke.plugins.page_panel.page_activation_controller import (
    PageActivationController,
)
from loomstroke.plugins.page_panel.page_thumbnail_scheduler import (
    PageThumbnailScheduler,
    visible_page_ids,
)
from loomstroke.plugins.page_panel.spread_view import SpreadListView
from loomstroke.plugins.page_panel.template_dialogs import (
    TemplateCaptureDialog,
    TemplateManagerDialog,
)
from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.ui import PanelDescriptor, UIPanelPlugin
from loomstroke.ui.translations import translated_literal_domain

logger = logging.getLogger(__name__)

DEFAULT_THUMBNAIL_HEIGHT = 128
THUMBNAIL_ASPECT = 0.75
DEFAULT_PAGE_TEMPLATE_ID = "builtin.blank_like_current"

# CT-06 §6.2: the roles offered in the page context menu; shared with the
# Comic Book Settings dialog via core (moved there for F2).
PAGE_ROLES = KNOWN_PAGE_ROLES


def _plugin_ts(text: str) -> str:
    return translated_literal_domain("PagePanelPlugin", text, use_fallback=True)


@dataclass(slots=True)
class PageEntry:
    page_id: str
    index: int
    name: str
    dirty: bool
    thumbnail_available: bool


class PageItemWidget(QWidget):
    rename_requested = Signal(str, str)

    def __init__(
        self,
        entry: PageEntry,
        *,
        thumbnail_width: int,
        thumbnail_height: int,
        show_dirty: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.page_id = entry.page_id
        self._thumbnail_width = max(32, int(thumbnail_width))
        self._thumbnail_height = max(32, int(thumbnail_height))
        self._show_dirty = bool(show_dirty)
        self._name = entry.name
        self._editing = False

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 4, 6, 4)
        layout.setSpacing(4)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(4)

        self.number_label = QLabel(self)
        number_font = QFont()
        number_font.setPointSize(9)
        number_font.setBold(True)
        self.number_label.setFont(number_font)
        self.number_label.setStyleSheet("color: #b0b0b0;")
        self.number_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        self.number_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        header_layout.addWidget(self.number_label, 0, Qt.AlignmentFlag.AlignTop)

        self.name_label = QLabel(self)
        name_font = QFont()
        name_font.setPointSize(9)
        name_font.setBold(True)
        self.name_label.setFont(name_font)
        self.name_label.setStyleSheet("color: #e0e0e0;")
        self.name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.name_label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        header_layout.addWidget(self.name_label, 1)

        self.name_edit = QLineEdit(self)
        self.name_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.name_edit.setVisible(False)
        self.name_edit.returnPressed.connect(self._commit_edit)
        self.name_edit.editingFinished.connect(self._commit_edit)
        header_layout.addWidget(self.name_edit, 1)

        self.folio_label = self._build_folio_label()
        header_layout.addWidget(self.folio_label, 0, Qt.AlignmentFlag.AlignTop)

        self.dirty_label = QLabel(self)
        self.dirty_label.setText("*")
        self.dirty_label.setStyleSheet("color: #e53935; font-weight: bold;")
        header_layout.addWidget(self.dirty_label, 0, Qt.AlignmentFlag.AlignTop)

        layout.addLayout(header_layout)

        self.thumbnail_label = QLabel(self)
        self.thumbnail_label.setFixedSize(self._thumbnail_width, self._thumbnail_height)
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setStyleSheet("background-color: #3a3a3a; border: 1px solid #5a5a5a;")
        layout.addWidget(self.thumbnail_label, 0, Qt.AlignmentFlag.AlignLeft)

        self._apply_entry(entry)

    def start_edit(self) -> None:
        if self._editing:
            return
        self._editing = True
        self.name_edit.setText(self._name)
        self.name_label.setVisible(False)
        self.name_edit.setVisible(True)
        self.name_edit.setFocus(Qt.FocusReason.OtherFocusReason)
        self.name_edit.selectAll()

    def set_thumbnail(self, pixmap: QPixmap | None) -> None:
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

    def update_entry(self, entry: PageEntry) -> None:
        self.page_id = entry.page_id
        self._apply_entry(entry)

    def set_dirty(self, dirty: bool) -> None:
        self.dirty_label.setVisible(self._show_dirty and bool(dirty))

    def set_folio(self, folio: str | None) -> None:
        """Display folio; ``None`` means the page shows no folio."""
        self.folio_label.setText(folio or "")
        self.folio_label.setVisible(bool(folio))

    def _build_folio_label(self) -> QLabel:
        label = QLabel(self)
        folio_font = QFont()
        folio_font.setPointSize(8)
        label.setFont(folio_font)
        label.setStyleSheet("color: #909090;")
        label.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        label.setVisible(False)
        return label

    def _apply_entry(self, entry: PageEntry) -> None:
        self._name = entry.name
        self.number_label.setText(f"{entry.index + 1}.")
        self.name_label.setText(entry.name)
        self.set_dirty(entry.dirty)
        self._apply_placeholder()

    def _apply_placeholder(self) -> None:
        pixmap = QPixmap(self._thumbnail_width, self._thumbnail_height)
        pixmap.fill(QColor(58, 58, 58))
        self.thumbnail_label.setPixmap(pixmap)

    def _commit_edit(self) -> None:
        if not self._editing:
            return
        self._editing = False
        self.name_label.setVisible(True)
        self.name_edit.setVisible(False)
        new_name = self.name_edit.text()
        if new_name != self._name:
            self.rename_requested.emit(self.page_id, new_name)


class PageListWidget(QListWidget):
    delete_requested = Signal()
    rename_requested = Signal()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]  # PySide6 override stub limitation
        if event.key() == Qt.Key.Key_Delete:
            self.delete_requested.emit()
            return
        if event.key() == Qt.Key.Key_F2:
            self.rename_requested.emit()
            return
        super().keyPressEvent(event)


class PagePanelWidget(QWidget):
    _ICON_ROOT = Path(__file__).resolve().parents[2] / "ui"
    _ICON_MAP: ClassVar[dict[str, tuple[str | None, QStyle.StandardPixmap]]] = {
        "add": ("icons/layer_add.svg", QStyle.StandardPixmap.SP_FileDialogNewFolder),
        "duplicate": (
            "icons/layer_duplicate.svg",
            QStyle.StandardPixmap.SP_FileDialogDetailedView,
        ),
        "delete": ("icons/layer_delete.svg", QStyle.StandardPixmap.SP_TrashIcon),
        "move_up": (None, QStyle.StandardPixmap.SP_ArrowUp),
        "move_down": (None, QStyle.StandardPixmap.SP_ArrowDown),
    }

    def __init__(self, host: HostAPI, parent: QWidget | None = None) -> None:  # noqa: PLR0915
        super().__init__(parent)
        self._host = host
        self._page_service = host.pages
        self._settings = host.settings

        self._thumbnail_height = int(
            self._settings.get("ui/pages_panel/thumbnail_size", DEFAULT_THUMBNAIL_HEIGHT)
        )
        self._thumbnail_width = int(max(48, self._thumbnail_height * THUMBNAIL_ASPECT))
        self._show_dirty = bool(self._settings.get("ui/pages_panel/show_dirty_indicator", True))

        self._suppress_selection = False
        self._item_map: dict[str, QListWidgetItem] = {}
        self._horizontal_layout = False
        # CT-06 B2: runtime-only preview state (never persisted; a fresh
        # panel intentionally restarts at Single / Left).
        self._view_mode: str = "single"
        self._binding: BindingDirection = "left"
        self._entries: list[PageEntry] = []
        self._entries_by_id: dict[str, PageEntry] = {}
        self._template_name_map: dict[str, str] = {}
        # CT-08 V3: batched visibility-first thumbnails + activation bookkeeping.
        self._thumbnail_scheduler = PageThumbnailScheduler(self._request_thumbnail)
        self._activation = PageActivationController(
            self._page_service,
            on_failed=self._on_activation_failed,
            on_recovered=self._on_activation_recovered,
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        toolbar.setSpacing(4)

        self.add_button = self._tool_button(
            "+",
            self._ts("Add Page"),
            "add",
        )
        self.duplicate_button = self._tool_button(
            self._ts("Dup"),
            self._ts("Duplicate Page"),
            "duplicate",
        )
        self.delete_button = self._tool_button(
            self._ts("Del"),
            self._ts("Delete Page"),
            "delete",
        )
        self.up_button = self._tool_button(
            self._ts("Up"),
            self._ts("Move Up"),
            "move_up",
        )
        self.down_button = self._tool_button(
            self._ts("Down"),
            self._ts("Move Down"),
            "move_down",
        )
        self._template_menu = QMenu(self.add_button)
        self.add_button.setPopupMode(QToolButton.ToolButtonPopupMode.MenuButtonPopup)
        self.add_button.setMenu(self._template_menu)
        self._refresh_template_menu()

        self.add_button.clicked.connect(self._on_add_page)
        self.duplicate_button.clicked.connect(self._on_duplicate_page)
        self.delete_button.clicked.connect(self._on_delete_page)
        self.up_button.clicked.connect(self._on_move_up)
        self.down_button.clicked.connect(self._on_move_down)

        for button in (
            self.add_button,
            self.duplicate_button,
            self.delete_button,
            self.up_button,
            self.down_button,
        ):
            toolbar.addWidget(button)

        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        # CT-06: runtime-only view mode + the document's binding setting.
        self._build_view_controls(layout)

        self.list_widget = PageListWidget(self)
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list_widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        # Page reordering is button/command based (Move Up/Down -> move_page);
        # item dragging is NOT implemented.  Qt would silently enable free
        # item movement when the view switches to IconMode, letting icons be
        # dropped at arbitrary view-only positions that desync from the
        # document order until the next rebuild — so drag is pinned off here
        # and after every view-mode switch.
        self.list_widget.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.setAlternatingRowColors(True)
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.list_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Ignored)
        self.list_widget.setMinimumHeight(self._thumbnail_height + 24)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._on_page_context_menu)
        self.list_widget.itemSelectionChanged.connect(self._on_selection_changed)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        self.list_widget.delete_requested.connect(self._on_delete_page)
        self.list_widget.rename_requested.connect(self._on_rename_requested)
        layout.addWidget(self.list_widget, 1)

        # CT-06 B2: read-only spread preview, hidden while in Single mode.
        self.spread_view = SpreadListView(
            thumbnail_width=self._thumbnail_width,
            thumbnail_height=self._thumbnail_height,
            show_dirty=self._show_dirty,
            parent=self,
        )
        self.spread_view.setVisible(False)
        self.spread_view.page_activate_requested.connect(self._on_spread_slot_activated)
        self.spread_view.page_context_menu_requested.connect(self._show_page_role_menu)
        # CT-06 §6.2: an empty slot offers to insert a blank page (no drag path).
        self._empty_slot_insert = EmptySlotInsertController(
            self._page_service, confirm=self._confirm_blank_page_insert
        )
        self.spread_view.empty_slot_insert_requested.connect(self._on_empty_slot_insert)
        layout.addWidget(self.spread_view, 1)

        self._apply_list_layout(self.size())

        self.refresh_from_service()
        # Last: applying a stored Spread view rebuilds the spread cards,
        # which needs every widget above to exist.
        self.sync_from_document()

    def resizeEvent(self, event) -> None:  # type: ignore[override]  # PySide6 override stub limitation
        super().resizeEvent(event)
        self._apply_list_layout(event.size())

    def _apply_list_layout(self, size: QSize) -> None:
        width = size.width()
        height = size.height()
        if height <= 0:
            return
        horizontal = width >= height
        if horizontal == self._horizontal_layout:
            return
        self._horizontal_layout = horizontal
        if horizontal:
            self.list_widget.setViewMode(QListWidget.ViewMode.IconMode)
            self.list_widget.setFlow(QListWidget.Flow.LeftToRight)
            self.list_widget.setWrapping(False)
            self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.list_widget.setSpacing(8)
        else:
            self.list_widget.setViewMode(QListWidget.ViewMode.ListMode)
            self.list_widget.setFlow(QListWidget.Flow.TopToBottom)
            self.list_widget.setWrapping(False)
            self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.list_widget.setSpacing(4)
        # setViewMode() re-derives movement/drag from the view mode (IconMode
        # defaults to free movement); re-pin the static, drag-free behaviour.
        self.list_widget.setMovement(QListWidget.Movement.Static)
        self.list_widget.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)

    def refresh_from_service(self) -> None:
        self._refresh_template_menu()
        self.set_pages(self._page_service.list_pages())
        self.set_active_page(self._page_service.get_active_page_id())

    def set_pages(self, pages: Sequence[PageSummary]) -> None:
        entries = [self._coerce_entry(page) for page in pages]
        self._entries = list(entries)
        self._entries_by_id = {entry.page_id: entry for entry in entries}
        self._rebuild_list(entries)
        # Spread cards are re-derived only while visible; the stored
        # entries rebuild them lazily on the next mode switch.
        if self._view_mode == "spread":
            self._rebuild_spread_view()
        self._refresh_folios()
        self.set_active_page(self._page_service.get_active_page_id())

    def set_active_page(self, page_id: str) -> None:
        self.spread_view.set_active_page(page_id)
        item = self._item_map.get(page_id)
        if item is None:
            return
        self._suppress_selection = True
        self.list_widget.setCurrentItem(item)
        self._suppress_selection = False

    def update_dirty(self, page_id: str, dirty: bool) -> None:
        widget = self._widget_for(page_id)
        if widget:
            widget.set_dirty(dirty)
        self.spread_view.update_dirty(page_id, dirty)

    def update_thumbnail(self, page_id: str, pixmap: QPixmap | None) -> None:
        widget = self._widget_for(page_id)
        if widget:
            widget.set_thumbnail(pixmap)
        self.spread_view.update_thumbnail(page_id, pixmap)

    # ------------------------------------------------------------------
    # CT-06: View mode is runtime-only UI state (nothing persisted, the
    # document is never touched).  Binding is the opposite: it is a book
    # setting stored in the document metadata, undoable, and the canvas
    # reads the same value -- so this control writes it rather than
    # previewing it (spec §6.1 / §9).
    def _build_view_controls(self, layout: QVBoxLayout) -> None:
        controls = QHBoxLayout()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)

        self.view_label = QLabel(self._plugin_text("View"), self)
        controls.addWidget(self.view_label, 0)

        self.view_combo = QComboBox(self)
        self.view_combo.addItem(self._plugin_text("Single"), "single")
        self.view_combo.addItem(self._plugin_text("Spread"), "spread")
        self.view_combo.setAccessibleName(self._plugin_text("View"))
        controls.addWidget(self.view_combo, 1)

        self.binding_label = QLabel(self._plugin_text("Binding"), self)
        controls.addWidget(self.binding_label, 0)

        self.binding_combo = QComboBox(self)
        self.binding_combo.addItem(self._plugin_text("Left Binding"), "left")
        self.binding_combo.addItem(self._plugin_text("Right Binding"), "right")
        self.binding_combo.setAccessibleName(self._plugin_text("Binding"))
        controls.addWidget(self.binding_combo, 1)

        self._apply_binding_hint()
        # Signals are connected exactly once; retranslation only updates
        # item texts and never rebuilds the combos.
        self.view_combo.currentIndexChanged.connect(self._on_view_mode_changed)
        self.binding_combo.currentIndexChanged.connect(self._on_binding_changed)
        layout.addLayout(controls)

    def _plugin_text(self, text: str) -> str:
        return _plugin_ts(text)

    def _apply_binding_hint(self) -> None:
        hint = self._plugin_text("Changing the binding does not reorder pages")
        self.binding_combo.setToolTip(hint)
        self.binding_combo.setAccessibleDescription(hint)
        self.binding_label.setToolTip(hint)

    def _on_view_mode_changed(self, index: int) -> None:
        mode = self.view_combo.itemData(index)
        if not isinstance(mode, str):
            return
        self._apply_view_mode(mode, persist=True)

    def _apply_view_mode(self, mode: str, *, persist: bool) -> None:
        if mode == self._view_mode:
            return
        self._view_mode = mode
        spread = mode == "spread"
        if spread:
            self._rebuild_spread_view()
        self.list_widget.setVisible(not spread)
        self.spread_view.setVisible(spread)
        self._announce_view_mode(mode)
        if persist:
            self._store_view_mode(mode)

    def _store_view_mode(self, mode: str) -> bool:
        """Remember the view in the document (``BookLayout.default_view``).

        A book that is drawn as spreads should reopen as spreads, so this
        rides in the document rather than in the app's settings.  It is the
        same book-metadata path as the binding, which means switching the
        view is undoable and marks the document dirty (spec §9 originally
        called the toggle pure UI state; storing it was the user's call).
        """

        setter = getattr(self._page_service, "set_book_layout", None)
        if not callable(setter):
            return False
        try:
            return bool(setter({"default_view": mode}))
        except Exception:
            logger.warning("failed to store the page view mode", exc_info=True)
            return False

    def reset_view_mode_to_single(self) -> None:
        """Return to Single, announcing it if the panel was showing spreads.

        Does not touch the stored preference: the panel is being torn down,
        which is not the user deciding to stop working in spreads.
        """

        if self._view_mode == "single":
            return
        index = self.view_combo.findData("single")
        if index >= 0:
            previous = self.view_combo.blockSignals(True)
            try:
                self.view_combo.setCurrentIndex(index)
            finally:
                self.view_combo.blockSignals(previous)
        self._apply_view_mode("single", persist=False)

    def _announce_view_mode(self, mode: str) -> None:
        """Tell the canvas which view mode the Pages panel is in (CT-06 B4-c4).

        The canvas viewport follows this panel so the two never disagree
        about whether the book is being read as spreads.
        """
        bus = getattr(self._host, "events", None)
        emit = getattr(bus, "emit", None)
        if not callable(emit):
            return
        try:
            emit(Events.PAGE_VIEW_MODE_CHANGED, {"mode": mode})
        except Exception:
            logger.debug("page view mode event emit failed", exc_info=True)

    def _on_binding_changed(self, index: int) -> None:
        binding = self.binding_combo.itemData(index)
        if binding not in ("left", "right") or binding == self._binding:
            return
        if not self._write_binding(str(binding)):
            # Nothing was stored (no document, no metadata port): put the
            # control back rather than showing a book setting the document
            # does not have.
            self.sync_from_document()
            return
        self._binding = binding
        if self._view_mode == "spread":
            self._rebuild_spread_view()

    def _write_binding(self, binding: str) -> bool:
        setter = getattr(self._page_service, "set_book_layout", None)
        if not callable(setter):
            return False
        try:
            return bool(setter({"binding": binding}))
        except Exception:
            logger.warning("failed to store the book binding", exc_info=True)
            return False

    def _document_book_layout(self) -> BookLayout:
        """Book settings as the open document stores them.

        The canvas viewport resolves the same metadata, so both sides agree
        on binding and on how the book opens.
        """

        resolve = getattr(getattr(self._host, "document", None), "resolve_book_layout", None)
        if not callable(resolve):
            return BookLayout()
        try:
            return resolve()
        except Exception:
            logger.debug("book layout unavailable; using defaults", exc_info=True)
            return BookLayout()

    def _document_binding(self) -> BindingDirection:
        binding = str(self._document_book_layout().binding)
        return "right" if binding == "right" else "left"

    def sync_from_document(self) -> None:
        """Show the book settings the document stores (binding + view).

        Signals are blocked: setting an index here is not a user edit, and
        letting it through would push the value straight back into the
        document (and into the undo history) on every load.
        """

        self._sync_binding_from_document()
        self._sync_view_mode_from_document()
        # Any book setting can reshape the spreads now (body_start moves the
        # pairings without touching binding or view), so an external change
        # rebuilds the cards regardless of which key moved.
        if self._view_mode == "spread":
            self._rebuild_spread_view()
        # Folios ride on the same events (folio_start/prefix/hidden roles
        # via book.layout_changed, folio_mode via book_attributes_changed).
        self._refresh_folios()

    def _sync_view_mode_from_document(self) -> None:
        mode = self._document_view_mode()
        if mode == self._view_mode:
            return
        index = self.view_combo.findData(mode)
        if index >= 0 and index != self.view_combo.currentIndex():
            previous = self.view_combo.blockSignals(True)
            try:
                self.view_combo.setCurrentIndex(index)
            finally:
                self.view_combo.blockSignals(previous)
        self._apply_view_mode(mode, persist=False)

    def _document_view_mode(self) -> str:
        layout = self._document_book_layout()
        return "spread" if str(getattr(layout, "default_view", "single")) == "spread" else "single"

    def _sync_binding_from_document(self) -> None:
        binding = self._document_binding()
        changed = binding != self._binding
        self._binding = binding
        index = self.binding_combo.findData(binding)
        if index >= 0 and index != self.binding_combo.currentIndex():
            previous = self.binding_combo.blockSignals(True)
            try:
                self.binding_combo.setCurrentIndex(index)
            finally:
                self.binding_combo.blockSignals(previous)
        if changed and self._view_mode == "spread":
            # An external change (load, undo/redo) has to reach the cards
            # too, not just the control that happens to display it.
            self._rebuild_spread_view()

    def _rebuild_spread_view(self) -> None:
        """Re-derive spread cards with the same inputs the canvas uses.

        Runs only when the page list or a book setting changes — never per
        paint/resize/active change.  The full document layout (binding,
        ``body_start``) and the per-page attributes (roles) come from the
        metadata, exactly like the canvas viewport's resolution: two views
        of the same book must never pair its pages differently.
        """
        page_ids = [entry.page_id for entry in self._entries]
        spreads = resolve_spreads(
            page_ids,
            self._document_book_layout(),
            self._page_attributes(page_ids),
        )
        self.spread_view.set_spreads(spreads, self._entries_by_id)
        self.spread_view.set_active_page(self._page_service.get_active_page_id())
        self._refresh_folios()
        self._replay_cached_thumbnails(page_ids)

    def _refresh_folios(self) -> None:
        """Recompute display folios and push them to both views.

        Event-driven only (page list changes and book-metadata changes),
        never per paint.  The resolver reads the same metadata the spread
        derivation uses, so the two can never disagree.
        """
        page_ids = [entry.page_id for entry in self._entries]
        folios = resolve_folios(
            page_ids,
            self._document_book_layout(),
            self._page_attributes(page_ids),
        )
        for page_id in page_ids:
            widget = self._widget_for(page_id)
            if widget is not None:
                widget.set_folio(folios.get(page_id))
            self.spread_view.update_folio(page_id, folios.get(page_id))

    def _page_attributes(self, page_ids: Sequence[str]) -> dict[str, Any]:
        """Per-page comic attributes, resolved from each page's metadata."""

        metadata_by_id: dict[str, Any] = {}
        for page_id in page_ids:
            metadata_by_id[page_id] = self._page_metadata(page_id)
        return BookLayoutService().resolve_page_attributes_by_id(metadata_by_id)

    def _page_metadata(self, page_id: str) -> Any:
        # CT-08 V2: prefer the non-hydrating accessor so reading roles or
        # folio settings never forces a full layer decode of every page.
        reader = getattr(self._page_service, "get_page_metadata", None)
        if callable(reader):
            return reader(page_id)
        snapshot = getattr(self._page_service, "get_page_snapshot", None)
        if not callable(snapshot):
            return None
        page = snapshot(page_id)
        return None if page is None else getattr(page, "metadata", None)

    def _replay_cached_thumbnails(self, page_ids: Sequence[str]) -> None:
        """Ask the service to re-emit its cached thumbnails (existing
        ``request_page_thumbnail`` path — no new cache is introduced).
        CT-08 V3: visible cards first, the rest in small batches."""
        self._thumbnail_scheduler.schedule(
            page_ids, visible=visible_page_ids(self.list_widget, self._item_map)
        )

    def _request_thumbnail(self, page_id: str) -> None:
        request = getattr(self._page_service, "request_page_thumbnail", None)
        if callable(request):
            request(page_id)

    def _on_activation_failed(self, page_id: str) -> None:
        item = self._item_map.get(page_id)
        if item is not None:
            item.setToolTip(self._ts("The page could not be loaded. Select it again to retry."))

    def _on_activation_recovered(self, page_id: str) -> None:
        item = self._item_map.get(page_id)
        if item is not None:
            item.setToolTip("")

    def _confirm_blank_page_insert(self, position: str) -> bool:
        del position
        answer = QMessageBox.question(
            self, self._ts("Insert Blank Page"), self._ts("Insert a blank page here?")
        )
        return answer == QMessageBox.StandardButton.Yes

    def _on_empty_slot_insert(self, neighbor_page_id: str, empty_side: str) -> None:
        _new_id, reason = self._empty_slot_insert.insert(
            neighbor_page_id, empty_side, self._binding
        )
        if reason == "before_first_unsupported":
            QMessageBox.information(
                self,
                self._ts("Insert Blank Page"),
                self._ts(
                    "A blank page cannot be inserted before the first page. Use Add Page instead."
                ),
            )

    def _on_spread_slot_activated(self, page_id: str) -> None:
        # Real slots only (empty slots never emit); the existing
        # PageService path owns the actual switch and its events.
        self._activation.activate(page_id)

    def _rebuild_list(self, entries: Sequence[PageEntry]) -> None:
        self._suppress_selection = True
        self.list_widget.clear()
        self._item_map.clear()
        for entry in entries:
            item = QListWidgetItem(self.list_widget)
            item.setData(Qt.ItemDataRole.UserRole, entry.page_id)
            widget = PageItemWidget(
                entry,
                thumbnail_width=self._thumbnail_width,
                thumbnail_height=self._thumbnail_height,
                show_dirty=self._show_dirty,
            )
            widget.rename_requested.connect(self._on_rename_commit)
            item.setSizeHint(widget.sizeHint())
            self.list_widget.addItem(item)
            self.list_widget.setItemWidget(item, widget)
            self._item_map[entry.page_id] = item
        self._suppress_selection = False

    def _widget_for(self, page_id: str) -> PageItemWidget | None:
        item = self._item_map.get(page_id)
        if item is None:
            return None
        widget = self.list_widget.itemWidget(item)
        if isinstance(widget, PageItemWidget):
            return widget
        return None

    def _coerce_entry(self, summary: PageSummary | Mapping[str, Any]) -> PageEntry:
        if isinstance(summary, Mapping):
            page_id = str(summary.get("id", ""))
            index = int(summary.get("index", 0))
            name = str(summary.get("name", f"Page {index + 1}"))
            dirty = bool(summary.get("dirty", False))
            thumb = bool(summary.get("thumbnail_available", False))
            return PageEntry(page_id, index, name, dirty, thumb)
        return PageEntry(
            summary.id,
            summary.index,
            summary.name,
            summary.dirty,
            summary.thumbnail_available,
        )

    def _on_selection_changed(self) -> None:
        if self._suppress_selection:
            return
        item = self.list_widget.currentItem()
        if not item:
            return
        page_id = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(page_id, str) and page_id:
            self._activation.activate(page_id)

    def _on_item_double_clicked(self, item: QListWidgetItem) -> None:
        widget = self.list_widget.itemWidget(item)
        if isinstance(widget, PageItemWidget):
            widget.start_edit()

    def _on_rename_requested(self) -> None:
        item = self.list_widget.currentItem()
        if not item:
            return
        widget = self.list_widget.itemWidget(item)
        if isinstance(widget, PageItemWidget):
            widget.start_edit()

    def _on_rename_commit(self, page_id: str, name: str) -> None:
        self._page_service.rename_page(page_id, name)

    def _on_add_page(self) -> None:
        # Always the current page's canvas.  Choosing a template from the
        # dropdown used to make it the button's default for every later
        # click, which silently kept producing pages at that template's
        # fixed size long after the user had forgotten about it -- a
        # template picked once (or by accident) is not a preference.
        self._add_page_with_template(DEFAULT_PAGE_TEMPLATE_ID)

    def _on_add_page_template(self, template_id: str) -> None:
        self._add_page_with_template(template_id)

    def _add_page_with_template(self, template_id: str) -> None:
        add_with_template = getattr(self._page_service, "add_page_from_template", None)
        if callable(add_with_template):
            add_with_template(str(template_id))
            return
        self._page_service.add_page()

    def _on_duplicate_page(self) -> None:
        page_id = self._current_page_id()
        if page_id:
            self._page_service.duplicate_page(page_id)

    def _on_delete_page(self) -> None:
        page_id = self._current_page_id()
        if not page_id:
            return
        name = self._current_page_name() or self._ts("this page")
        result = QMessageBox.question(
            self,
            self._ts("Delete Page"),
            f"Delete {name}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if result == QMessageBox.StandardButton.Yes:
            self._page_service.delete_page(page_id)

    # Move Up/Down step by one page in both view modes. Moving by spread was
    # tried in Spread mode (CT-06 B4-b) and reverted: a spread is one or two
    # pages depending on where it sits, so "one spread" was one page here and
    # two pages there, and with only a few spreads a single click landed the
    # pages at the end of the book. Stepping by page keeps the buttons
    # predictable; spreads re-form from the new positions on their own,
    # because the layout derives them rather than storing them.
    def _on_move_up(self) -> None:
        page_id, row = self._current_page_with_row()
        if page_id is None or row <= 0:
            return
        self._page_service.move_page(page_id, row - 1)

    def _on_move_down(self) -> None:
        page_id, row = self._current_page_with_row()
        if page_id is None:
            return
        if row >= self.list_widget.count() - 1:
            return
        self._page_service.move_page(page_id, row + 1)

    def _on_page_context_menu(self, pos: Any) -> None:
        item = self.list_widget.itemAt(pos)
        if item is None:
            return
        page_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(page_id, str):
            return
        self._show_page_role_menu(page_id, self.list_widget.viewport().mapToGlobal(pos))

    def _show_page_role_menu(self, page_id: str, global_pos: QPoint) -> None:
        # Shared by both views: the single list's customContextMenuRequested
        # and the spread slots' context_menu_requested land here.
        self._build_page_role_menu(page_id).exec(global_pos)

    def _build_page_role_menu(self, page_id: str) -> QMenu:
        """The page context menu: role first (F3 added the folio submenu
        after it — keep the order, callers find the role menu first)."""
        menu = QMenu(self)
        role_menu = menu.addMenu(self._plugin_text("Page Role"))
        current = self._page_role(page_id)
        for value, label in PAGE_ROLES:
            action = role_menu.addAction(self._plugin_text(label))
            action.setCheckable(True)
            action.setChecked(value == current)
            action.triggered.connect(
                lambda _checked=False, p=page_id, r=value: self._set_page_role(p, r)
            )
        self._add_folio_submenu(menu, page_id)
        return menu

    def _add_folio_submenu(self, menu: QMenu, page_id: str) -> None:
        folio_menu = menu.addMenu(self._plugin_text("Folio"))
        current = self._page_attributes([page_id])[page_id]
        for value, label in (("auto", "Automatic"), ("hidden", "Hidden")):
            action = folio_menu.addAction(self._plugin_text(label))
            action.setCheckable(True)
            action.setChecked(current.folio_mode == value)
            action.triggered.connect(
                lambda _checked=False, p=page_id, m=value: self._set_page_folio_mode(p, m)
            )
        custom_action = folio_menu.addAction(self._plugin_text("Custom..."))
        custom_action.setCheckable(True)
        custom_action.setChecked(current.folio_mode == "custom")
        custom_action.triggered.connect(
            lambda _checked=False, p=page_id: self._prompt_custom_folio(p)
        )

    def _page_role(self, page_id: str) -> str:
        return BookLayoutService().resolve_page_attributes(self._page_metadata(page_id)).role

    def _set_page_role(self, page_id: str, role: str) -> None:
        if role == self._page_role(page_id):
            return
        self._write_page_book_attributes(page_id, {"role": role})

    def _set_page_folio_mode(self, page_id: str, mode: str) -> None:
        if mode == self._page_attributes([page_id])[page_id].folio_mode:
            return
        self._write_page_book_attributes(page_id, {"folio_mode": mode})

    def _prompt_custom_folio(self, page_id: str) -> None:
        current = self._page_attributes([page_id])[page_id]
        text, accepted = QInputDialog.getText(
            self,
            self._plugin_text("Custom Folio"),
            self._plugin_text("Folio text:"),
            text=current.custom_folio or "",
        )
        if not accepted:
            return
        if current.folio_mode == "custom" and text == (current.custom_folio or ""):
            return
        self._write_page_book_attributes(page_id, {"folio_mode": "custom", "custom_folio": text})

    def _write_page_book_attributes(self, page_id: str, updates: Mapping[str, Any]) -> None:
        setter = getattr(self._page_service, "set_page_book_attributes", None)
        if not callable(setter):
            return
        try:
            setter(page_id, updates)
        except Exception:
            logger.warning("failed to set page book attributes", exc_info=True)

    def _current_page_id(self) -> str | None:
        item = self.list_widget.currentItem()
        if not item:
            return None
        page_id = item.data(Qt.ItemDataRole.UserRole)
        return page_id if isinstance(page_id, str) else None

    def _current_page_name(self) -> str | None:
        item = self.list_widget.currentItem()
        if not item:
            return None
        widget = self.list_widget.itemWidget(item)
        if isinstance(widget, PageItemWidget):
            return widget._name
        return None

    def _current_page_with_row(self) -> tuple[str | None, int]:
        item = self.list_widget.currentItem()
        if not item:
            return None, -1
        page_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(page_id, str):
            return None, -1
        return page_id, self.list_widget.row(item)

    def _tool_button(self, text: str, tooltip: str, icon_key: str) -> QToolButton:
        button = QToolButton(self)
        button.setAutoRaise(True)
        button.setText(text)
        button.setToolTip(tooltip)
        button.setProperty("loomstroke_page_icon_key", icon_key)
        self._apply_tool_button_icon(button, icon_key)
        return button

    def _apply_tool_button_icon(self, button: QToolButton, icon_key: str) -> None:
        from loomstroke.ui.icon_loader import load_svg_icon  # noqa: PLC0415

        svg_name, fallback = self._ICON_MAP.get(
            icon_key,
            (None, QStyle.StandardPixmap.SP_FileIcon),
        )
        icon = QIcon()
        if svg_name is not None:
            svg_icon = load_svg_icon(self._ICON_ROOT / svg_name)
            if svg_icon is not None:
                icon = svg_icon
        if icon.isNull():
            style = self.style()
            if style is not None:
                icon = style.standardIcon(fallback)
        button.setIcon(icon)

    def _refresh_theme_icons(self) -> None:
        for attr_name in (
            "add_button",
            "duplicate_button",
            "delete_button",
            "up_button",
            "down_button",
        ):
            button = getattr(self, attr_name, None)
            if not isinstance(button, QToolButton):
                continue
            icon_key = button.property("loomstroke_page_icon_key")
            if isinstance(icon_key, str):
                self._apply_tool_button_icon(button, icon_key)

    def _refresh_template_menu(self) -> None:
        self._template_menu.clear()
        templates = self._list_page_templates()
        self._template_name_map = {template.id: template.name for template in templates}

        builtin = [
            template
            for template in templates
            if template.source == "builtin" and template.category != "comic"
        ]
        comic = [
            template
            for template in templates
            if template.source == "builtin" and template.category == "comic"
        ]
        custom = [template for template in templates if template.source != "builtin"]

        self._append_template_actions(self._ts("Basic"), builtin)
        self._append_template_actions(self._ts("Comic"), comic)
        self._append_template_actions(self._ts("Custom"), custom)
        self._append_template_save_action()

    def _append_template_actions(
        self,
        section_name: str,
        templates: Sequence[PageTemplateSummary],
    ) -> None:
        if not templates:
            return
        if self._template_menu.actions():
            self._template_menu.addSeparator()
        section_action = QAction(section_name, self._template_menu)
        section_action.setEnabled(False)
        self._template_menu.addAction(section_action)
        for template in templates:
            action = QAction(template.name, self._template_menu)
            action.setData(template.id)
            if isinstance(template.description, str) and template.description.strip():
                action.setToolTip(template.description.strip())
            action.triggered.connect(
                lambda _checked=False, template_id=template.id: self._on_add_page_template(
                    template_id
                )
            )
            self._template_menu.addAction(action)

    def _append_template_save_action(self) -> None:
        if self._template_menu.actions():
            self._template_menu.addSeparator()
        action = QAction(self._ts("Save Current Page as Template..."), self._template_menu)
        action.triggered.connect(self._on_save_current_page_template)
        self._template_menu.addAction(action)
        manage = QAction(self._ts("Manage Templates..."), self._template_menu)
        manage.triggered.connect(self._on_manage_templates)
        self._template_menu.addAction(manage)
        masters = QAction(self._ts("Manage Masters..."), self._template_menu)
        masters.triggered.connect(self._on_manage_masters)
        self._template_menu.addAction(masters)

    def _on_manage_templates(self) -> None:
        dialog = TemplateManagerDialog(page_service=self._page_service, parent=self)
        dialog.exec()
        # Renames/deletes must reach the add-page menu immediately.
        self._refresh_template_menu()

    def _on_manage_masters(self) -> None:
        from loomstroke.plugins.page_panel.master_dialogs import (  # noqa: PLC0415
            ManageMastersDialog,
        )

        dialog = ManageMastersDialog(
            page_service=self._page_service,
            document_service=getattr(self._host, "document", None),
            parent=self,
        )
        dialog.exec()

    def _on_save_current_page_template(self) -> None:
        page_id = self._current_page_id()
        if not page_id:
            return

        save_template = getattr(self._page_service, "save_page_as_template", None)
        if not callable(save_template):
            request_widget_page_failure(
                self,
                self._ts("Page Operation Failed"),
                self._ts("save_template failed: feature not supported"),
                self._ts,
            )
            return

        estimate = getattr(self._page_service, "estimate_page_template_raster_bytes", None)
        dialog = TemplateCaptureDialog(
            suggested_name=self._suggest_template_name(),
            estimated_raster_bytes=int(estimate(page_id)) if callable(estimate) else 0,
            parent=self,
        )
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        normalized_name = dialog.template_name()
        if not normalized_name:
            return

        failure_before = page_failure_checkpoint(self, self._ts)
        template_id = save_template(page_id, normalized_name, options=dialog.capture_options())
        if not isinstance(template_id, str) or not template_id:
            # A silent no-op here hid a real save crash (vector pages,
            # 2026-08-03) — failures must be visible.
            if failure_before is not None:
                request_widget_page_failure(
                    self,
                    self._ts("Save Page Template"),
                    self._ts("The template could not be saved. See the log for details."),
                    self._ts,
                    checkpoint=failure_before,
                )
            return

        # Saving a template does not select it: it only adds it to the menu.
        self._refresh_template_menu()
        show_message = getattr(self._host.ui, "show_message", None)
        if callable(show_message):
            show_message(f"Saved template: {normalized_name}", duration=3000)

    def _suggest_template_name(self) -> str:
        current_name = (self._current_page_name() or "").strip()
        if current_name:
            return current_name
        page_id, row = self._current_page_with_row()
        if page_id and row >= 0:
            return f"Page {row + 1}"
        return self._ts("Page Template")

    def _list_page_templates(self) -> Sequence[PageTemplateSummary]:
        list_templates = getattr(self._page_service, "list_page_templates", None)
        if callable(list_templates):
            templates = list_templates()
            if templates:
                return templates
        return (
            PageTemplateSummary(
                id=DEFAULT_PAGE_TEMPLATE_ID,
                name=self._ts("Blank (Like Current Page)"),
                category="basic",
                source="builtin",
                description=None,
            ),
        )

    def _ts(self, text: str) -> str:
        from loomstroke.ui.translations import translated_literal  # noqa: PLC0415

        return translated_literal(self.tr, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        event_type = event.type()
        if event_type == QEvent.Type.LanguageChange:
            self._refresh_translations()
        elif event_type in (
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.PaletteChange,
        ):
            self._refresh_theme_icons()

    def _refresh_translations(self) -> None:
        self.add_button.setToolTip(self._build_add_button_tooltip())
        self.duplicate_button.setToolTip(self._ts("Duplicate Page"))
        self.delete_button.setToolTip(self._ts("Delete Page"))
        self.up_button.setToolTip(self._ts("Move Up"))
        self.down_button.setToolTip(self._ts("Move Down"))
        self.up_button.setText(self._ts("Up"))
        self.down_button.setText(self._ts("Down"))
        self.duplicate_button.setText(self._ts("Dup"))
        # CT-06 B2: retranslate the view controls in place — stable
        # values (itemData) and the current selection are untouched.
        self.view_label.setText(self._plugin_text("View"))
        self.view_combo.setItemText(0, self._plugin_text("Single"))
        self.view_combo.setItemText(1, self._plugin_text("Spread"))
        self.view_combo.setAccessibleName(self._plugin_text("View"))
        self.binding_label.setText(self._plugin_text("Binding"))
        self.binding_combo.setItemText(0, self._plugin_text("Left Binding"))
        self.binding_combo.setItemText(1, self._plugin_text("Right Binding"))
        self.binding_combo.setAccessibleName(self._plugin_text("Binding"))
        self._apply_binding_hint()
        if self._view_mode == "spread":
            # Card/slot labels carry translated templates: rebuild from
            # the same runtime state (mode/binding/active preserved).
            self._rebuild_spread_view()

    def _build_add_button_tooltip(self) -> str:
        return self._ts("Add Page")


class PagePanelPlugin(UIPanelPlugin):
    metadata = PluginMetadata(
        id="page_panel",
        name=_plugin_ts("Pages Panel"),
        version="0.1.0",
        description=_plugin_ts("Page management panel"),
        author="LoomStroke Team",
        types=[PluginType.UI_PANEL],
    )

    def __init__(self) -> None:
        super().__init__(self.metadata)
        self._host: HostAPI | None = None
        self._panel: PagePanelWidget | None = None
        self._subscriptions: list[tuple[str, Any]] = []
        self._event_handlers: list[Any] = []
        self._failure_notice: PageOperationFailureNotice | None = None

    def descriptor(self) -> PanelDescriptor:
        return PanelDescriptor(
            id="pages_panel",
            display_name=_plugin_ts("Pages"),
            category="tools",
            default_position="left",
            default_size=(260, 520),
            resizable=True,
        )

    def activate(self, host_api: HostAPI) -> None:  # type: ignore[override]  # plugin API signature divergence
        super().activate(host_api)
        self._host = host_api
        self._subscribe_events()

    def deactivate(self) -> None:
        self._unsubscribe_events()
        dismiss_page_failure_notice(self)
        if self._panel is not None and isValid(self._panel):
            # The canvas follows this panel's view mode; leaving it in Spread
            # after the panel is gone would strand the viewport with no way
            # back (CT-06 B4-c4).
            self._panel.reset_view_mode_to_single()
        self._panel = None
        self._host = None
        super().deactivate()

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        if self._host is None:
            raise RuntimeError("Host API not set")
        if self._panel is None:
            self._panel = PagePanelWidget(self._host, parent=parent)
        install_page_failure_notice(self, _plugin_ts)
        return self._panel

    def _subscribe_events(self) -> None:
        if self._host is None:
            return
        bus = self._host.events
        subscribe = getattr(bus, "on", None) or getattr(bus, "subscribe", None)
        if not callable(subscribe):
            return
        handlers = [
            (Events.PAGES_CHANGED, self._on_pages_changed),
            (Events.ACTIVE_PAGE_CHANGED, self._on_active_page_changed),
            (Events.PAGE_DIRTY_CHANGED, self._on_page_dirty_changed),
            (Events.PAGE_THUMBNAIL_UPDATED, self._on_page_thumbnail_updated),
            (Events.PAGE_OP_FAILED, self._on_page_op_failed),
            # Binding is document metadata now, so it also changes from
            # outside this panel: a load, and undo/redo of the very command
            # this panel issues (CT-06).
            (Events.DOC_CHANGED, self._on_document_changed),
            (Events.DOC_LOADED, self._on_document_reloaded),
        ]
        for event, handler in handlers:
            self._event_handlers.append(handler)
            self._subscriptions.append((event, subscribe(event, handler)))

    def _unsubscribe_events(self) -> None:
        if self._host is None:
            self._subscriptions.clear()
            self._event_handlers.clear()
            return
        bus = self._host.events
        off = getattr(bus, "off", None)
        unsubscribe = getattr(bus, "unsubscribe", None)
        for event, token in self._subscriptions:
            if callable(off):
                try:
                    off(event, token)
                    continue
                except Exception:
                    logger.debug("event bus off failed", exc_info=True)
            if callable(unsubscribe):
                try:
                    unsubscribe(event, token)
                except TypeError:
                    with contextlib.suppress(Exception):
                        unsubscribe(token)
        self._subscriptions.clear()
        self._event_handlers.clear()

    def _on_document_changed(self, payload: Any) -> None:
        # DOC_CHANGED carries every content edit; only the book settings
        # concern this panel.  Page attributes count: a role change can move
        # the first body page and re-pair the spreads.
        change_type = str(getattr(payload, "change_type", ""))
        if change_type not in ("book.layout_changed", "pages.book_attributes_changed"):
            return
        self._on_document_reloaded(payload)

    def _on_document_reloaded(self, payload: Any) -> None:
        del payload
        if self._panel is None:
            return
        self._panel.sync_from_document()

    def _on_pages_changed(self, payload: Any) -> None:
        if self._panel is None:
            return
        pages = None
        if isinstance(payload, Mapping):
            pages = payload.get("pages")
        if pages is None and self._host is not None:
            pages = self._host.pages.list_pages()
        if pages is None:
            return
        self._panel.set_pages(pages)

    def _on_active_page_changed(self, payload: Any) -> None:
        if self._panel is None:
            return
        page_id = None
        if isinstance(payload, Mapping):
            page_id = payload.get("page_id")
        elif hasattr(payload, "page_id"):
            page_id = getattr(payload, "page_id", None)
        if isinstance(page_id, str):
            self._panel.set_active_page(page_id)

    def _on_page_dirty_changed(self, payload: Any) -> None:
        if self._panel is None:
            return
        page_id = None
        dirty = None
        if isinstance(payload, Mapping):
            page_id = payload.get("page_id")
            dirty = payload.get("dirty")
        if isinstance(page_id, str):
            self._panel.update_dirty(page_id, bool(dirty))

    def _on_page_thumbnail_updated(self, payload: Any) -> None:
        if self._panel is None:
            return
        if not isinstance(payload, Mapping):
            return
        page_id = payload.get("page_id")
        png_data = payload.get("thumbnail_png")
        if not isinstance(page_id, str):
            return
        if isinstance(png_data, (bytes, bytearray)):
            pixmap = QPixmap()
            if pixmap.loadFromData(png_data, "PNG"):
                self._panel.update_thumbnail(page_id, pixmap)

    def _on_page_op_failed(self, payload: Any) -> None:
        if self._panel is None:
            return
        if not isinstance(payload, Mapping):
            return
        op = payload.get("op", "operation")
        error = payload.get("error", "Unknown error")
        notice = install_page_failure_notice(self, _plugin_ts)
        if notice is not None:
            notice.request(f"{op} failed: {error}")


def create_plugin() -> PagePanelPlugin:
    """Factory for plugin registry."""
    return PagePanelPlugin()


__all__ = ["PagePanelPlugin", "create_plugin"]
