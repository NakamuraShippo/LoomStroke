from __future__ import annotations

import contextlib
import itertools
import json
import logging
import shutil
import uuid
from collections.abc import Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import (
    QByteArray,
    QEvent,
    QPoint,
    QSize,
    Qt,
)
from PySide6.QtGui import QDrag, QIcon, QPixmap
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.event_bus_extensions import get_extended_event_bus
from loomstroke.core.events import Events
from loomstroke.host_api import HostAPI
from loomstroke.platform.user_dirs import plugin_data_dir
from loomstroke.plugins.vector_tools.common import (
    Anchor,
    VectorCreateCommand,
    VectorObject,
    VectorPath,
    VectorReplaceCommand,
    VectorStyle,
    make_vector_object,
)
from loomstroke.plugins.vector_tools.parametric_shapes import (
    convert_parametric_to_vector,
    create_basic_shape_object,
    get_basic_shape_definition,
    is_editable_parametric_shape,
    list_basic_shape_definitions,
)
from loomstroke.ui._dock_safe import is_valid

from ..model.shape_index import ShapeIndex, _require_no_load_failure
from ..model.shape_record import ShapeRecord, ShapeTemplate
from ..ops.bbox import compute_object_bbox, merge_bboxes
from ..ops.delete_shape_assets import delete_shape_record
from ..ops.persist_shape_assets import persist_shape_assets
from ..ops.selection_ops import scale_object, translate_object
from ..svg.parse import parse_svg
from ..svg.render_thumb import render_svg_thumbnail
from ..svg.sanitize import sanitize_svg
from .dnd import MIME_TYPE, build_payload, parse_payload
from .effect_shape_dialog import configure_effect_shape
from .library_load_feedback import (
    capture_library_load_issue,
    refresh_library_load_feedback,
    request_library_load_feedback,
    retire_library_load_feedback,
)

logger = logging.getLogger(__name__)

PLUGIN_ID = "loomstroke.shape_manager"
INDEX_FILENAME = "index.json"

DEFAULT_CATEGORY = "custom"


class ShapeListWidget(QListWidget):
    def __init__(
        self,
        payload_provider: Callable[[str], tuple[str | None, QPixmap | None]],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._payload_provider = payload_provider

    def startDrag(self, supportedActions: Qt.DropActions) -> None:  # type: ignore[override]  # PySide6 override stub limitation
        item = self.currentItem()
        if item is None:
            return
        shape_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(shape_id, str):
            return
        payload, pixmap = self._payload_provider(shape_id)
        if not payload:
            return
        mime = self.mimeData([item])
        mime.setData(MIME_TYPE, QByteArray(payload.encode("utf-8")))
        drag = QDrag(self)
        drag.setMimeData(mime)
        if pixmap is not None and not pixmap.isNull():
            drag.setPixmap(pixmap)
            drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.exec(Qt.DropAction.CopyAction)


class ShapeManagerPanel(QWidget):
    _ACTION_ICON_DIR = Path(__file__).resolve().parent.parent / "icons"
    _ACTION_ICON_MAP: ClassVar[dict[str, str]] = {
        "fit_contain": "fit_contain.svg",
        "fit_stretch": "fit_stretch.svg",
        "center": "center.svg",
        "align_top": "align_top.svg",
        "align_middle": "align_middle.svg",
        "align_bottom": "align_bottom.svg",
        "align_left": "align_left.svg",
        "align_center": "align_center.svg",
        "align_right": "align_right.svg",
        "distribute_h": "distribute_h.svg",
        "distribute_v": "distribute_v.svg",
        "import": "import.svg",
        "add_shape": "add_shape.svg",
        "delete": "delete.svg",
    }

    def __init__(self, parent: QWidget | None = None) -> None:  # noqa: PLR0915
        super().__init__(parent)
        self._host: HostAPI | None = None
        self._settings = None
        self._event_token = None
        self._drop_handler = None
        self._language_event_bus = get_extended_event_bus()
        self._language_event_handler = self._on_language_changed
        self._language_event_token = None
        self._selected_shape_id: str | None = None
        self._updating_edit_fields = False

        self._data_root = plugin_data_dir(PLUGIN_ID)
        self._index = ShapeIndex(self._data_root / INDEX_FILENAME)
        self._index.load()
        self._initial_library_load_issue = capture_library_load_issue(self)
        self._library_load_feedback = None

        self._category_combo = QComboBox(self)
        self._tag_combo = QComboBox(self)
        self._search_edit = QLineEdit(self)
        self._import_button = self._make_icon_button("import", self._ts("Import"))
        self._register_button = self._make_icon_button("add_shape", self._ts("Add Shape"))
        self._delete_button = self._make_icon_button("delete", self._ts("Delete"))
        self._list_widget = ShapeListWidget(self._build_drag_payload, self)
        self._tabs = QTabWidget(self)
        self._basic_list_widget = ShapeListWidget(self._build_basic_drag_payload, self)
        self._name_edit = QLineEdit(self)
        self._category_edit = QLineEdit(self)
        self._tags_edit = QLineEdit(self)
        self._align_reference_combo = QComboBox(self)
        self._basic_shape_defs = list_basic_shape_definitions()

        self._fit_contain_button = QPushButton(self)
        self._fit_stretch_button = QPushButton(self)
        self._center_button = QPushButton(self)
        self._align_left_button = QPushButton(self)
        self._align_center_button = QPushButton(self)
        self._align_right_button = QPushButton(self)
        self._align_top_button = QPushButton(self)
        self._align_middle_button = QPushButton(self)
        self._align_bottom_button = QPushButton(self)
        self._distribute_h_button = QPushButton(self)
        self._distribute_v_button = QPushButton(self)

        self._label_search_hint = QLabel(self)
        self._label_category = QLabel(self._ts("Category:"), self)
        self._label_tag = QLabel(self._ts("Tag:"), self)
        self._label_name = QLabel(self._ts("Name:"), self)
        self._label_category_edit = QLabel(self._ts("Category:"), self)
        self._label_tags = QLabel(self._ts("Tags:"), self)
        self._label_align_reference = QLabel(self._ts("Align Reference:"), self)
        self._basic_tab_hint = QLabel(self)
        # These labels are not added to any layout; hide them so they don't
        # sit at (0,0) and block mouse events on the tab bar.
        self._label_category_row = QLabel(self)
        self._label_category_row.hide()
        self._label_action_header = QLabel(self)
        self._label_action_header.hide()
        self._label_search_hint.hide()

        self._build_ui()
        self._retranslate_ui()
        self._refresh_categories()
        self._refresh_list()
        self._subscribe_language_event()
        self.destroyed.connect(self._on_destroyed)

    # --- Host lifecycle -------------------------------------------------
    def set_host(self, host_api: HostAPI) -> None:
        self._host = host_api
        self._settings = host_api.settings
        self._apply_settings()
        self._subscribe_drop_event()
        request_library_load_feedback(self)

    def detach_host(self) -> None:
        retire_library_load_feedback(self)
        self._unsubscribe_drop_event()
        self._host = None
        self._settings = None

    # --- UI -------------------------------------------------------------
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        layout.addWidget(self._tabs, 1)

        library_tab = QWidget(self._tabs)
        self._build_library_tab(library_tab)
        self._tabs.addTab(library_tab, self._ts("Shape Manager"))

        basic_tab = QWidget(self._tabs)
        self._build_basic_tab(basic_tab)
        self._tabs.addTab(basic_tab, self._ts("Basic Shapes"))
        self._tabs.setCurrentIndex(0)

    def _build_library_tab(self, container: QWidget) -> None:  # noqa: PLR0915
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._import_button.clicked.connect(self._on_import_svg)
        self._register_button.clicked.connect(self._on_register_selection)
        self._delete_button.clicked.connect(self._on_delete_selected_shape)
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(6)
        header_row.addWidget(self._import_button)
        header_row.addWidget(self._register_button)
        header_row.addWidget(self._delete_button)
        header_row.addStretch(1)
        layout.addLayout(header_row)

        search_row = QHBoxLayout()
        search_row.setContentsMargins(0, 0, 0, 0)
        search_row.setSpacing(6)
        self._search_edit.setPlaceholderText(self._ts("Search..."))
        self._search_edit.textChanged.connect(self._on_filter_changed)
        search_row.addWidget(self._search_edit, 1)
        layout.addLayout(search_row)

        category_row = QHBoxLayout()
        category_row.setContentsMargins(0, 0, 0, 0)
        category_row.setSpacing(6)
        category_row.addWidget(self._label_category)
        self._category_combo.currentIndexChanged.connect(self._on_filter_changed)
        self._category_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        category_row.addWidget(self._category_combo, 1)
        layout.addLayout(category_row)

        tag_row = QHBoxLayout()
        tag_row.setContentsMargins(0, 0, 0, 0)
        tag_row.setSpacing(6)
        tag_row.addWidget(self._label_tag)
        self._tag_combo.currentIndexChanged.connect(self._on_filter_changed)
        self._tag_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        tag_row.addWidget(self._tag_combo, 1)
        layout.addLayout(tag_row)

        self._list_widget.setViewMode(QListWidget.ViewMode.IconMode)
        self._list_widget.setIconSize(QSize(96, 96))
        self._list_widget.setResizeMode(QListWidget.ResizeMode.Adjust)
        self._list_widget.setSpacing(8)
        self._list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list_widget.customContextMenuRequested.connect(self._show_context_menu)
        self._list_widget.itemSelectionChanged.connect(self._on_shape_selection_changed)
        self._list_widget.setDragEnabled(True)
        self._list_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self._list_widget, 1)

        editor = QVBoxLayout()
        editor.setContentsMargins(0, 0, 0, 0)
        editor.setSpacing(4)

        name_row = QHBoxLayout()
        name_row.setContentsMargins(0, 0, 0, 0)
        name_row.setSpacing(4)
        name_row.addWidget(self._label_name)
        self._name_edit.setPlaceholderText(self._ts("Select a shape"))
        self._name_edit.editingFinished.connect(self._apply_shape_edits)
        name_row.addWidget(self._name_edit, 1)
        editor.addLayout(name_row)

        category_edit_row = QHBoxLayout()
        category_edit_row.setContentsMargins(0, 0, 0, 0)
        category_edit_row.setSpacing(4)
        category_edit_row.addWidget(self._label_category_edit)
        self._category_edit.setPlaceholderText(self._ts("custom"))
        self._category_edit.editingFinished.connect(self._apply_shape_edits)
        category_edit_row.addWidget(self._category_edit, 1)
        editor.addLayout(category_edit_row)

        tags_row = QHBoxLayout()
        tags_row.setContentsMargins(0, 0, 0, 0)
        tags_row.setSpacing(4)
        tags_row.addWidget(self._label_tags)
        self._tags_edit.setPlaceholderText(self._ts("comma-separated"))
        self._tags_edit.editingFinished.connect(self._apply_shape_edits)
        tags_row.addWidget(self._tags_edit, 1)
        editor.addLayout(tags_row)

        layout.addLayout(editor)

        action_header = QHBoxLayout()
        action_header.setContentsMargins(0, 0, 0, 0)
        action_header.setSpacing(4)
        action_header.addWidget(self._label_align_reference)
        self._align_reference_combo.addItems([self._ts("Canvas"), self._ts("Selection")])
        action_header.addWidget(self._align_reference_combo)
        action_header.addStretch(1)
        layout.addLayout(action_header)

        actions = QVBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(4)

        fit_row = QHBoxLayout()
        fit_row.setContentsMargins(0, 0, 0, 0)
        fit_row.setSpacing(4)
        self._configure_action(
            self._fit_contain_button,
            self._ts("Fit Contain"),
            self._on_fit_contain,
            icon_key="fit_contain",
        )
        self._configure_action(
            self._fit_stretch_button,
            self._ts("Fit Stretch"),
            self._on_fit_stretch,
            icon_key="fit_stretch",
        )
        self._configure_action(
            self._center_button,
            self._ts("Center"),
            self._on_center_canvas,
            icon_key="center",
        )
        fit_row.addWidget(self._fit_contain_button)
        fit_row.addWidget(self._fit_stretch_button)
        fit_row.addWidget(self._center_button)
        fit_row.addStretch(1)
        actions.addLayout(fit_row)

        align_v_row = QHBoxLayout()
        align_v_row.setContentsMargins(0, 0, 0, 0)
        align_v_row.setSpacing(4)
        self._configure_action(
            self._align_top_button,
            self._ts("Top"),
            lambda: self._on_align("top"),
            icon_key="align_top",
        )
        self._configure_action(
            self._align_middle_button,
            self._ts("V-Center"),
            lambda: self._on_align("center_y"),
            icon_key="align_middle",
        )
        self._configure_action(
            self._align_bottom_button,
            self._ts("Bottom"),
            lambda: self._on_align("bottom"),
            icon_key="align_bottom",
        )
        align_v_row.addWidget(self._align_top_button)
        align_v_row.addWidget(self._align_middle_button)
        align_v_row.addWidget(self._align_bottom_button)
        align_v_row.addStretch(1)
        actions.addLayout(align_v_row)

        align_h_row = QHBoxLayout()
        align_h_row.setContentsMargins(0, 0, 0, 0)
        align_h_row.setSpacing(4)
        self._configure_action(
            self._align_left_button,
            self._ts("Left"),
            lambda: self._on_align("left"),
            icon_key="align_left",
        )
        self._configure_action(
            self._align_center_button,
            self._ts("H-Center"),
            lambda: self._on_align("center_x"),
            icon_key="align_center",
        )
        self._configure_action(
            self._align_right_button,
            self._ts("Right"),
            lambda: self._on_align("right"),
            icon_key="align_right",
        )
        align_h_row.addWidget(self._align_left_button)
        align_h_row.addWidget(self._align_center_button)
        align_h_row.addWidget(self._align_right_button)
        align_h_row.addStretch(1)
        actions.addLayout(align_h_row)

        dist_row = QHBoxLayout()
        dist_row.setContentsMargins(0, 0, 0, 0)
        dist_row.setSpacing(4)
        self._configure_action(
            self._distribute_h_button,
            self._ts("H-Distribute"),
            self._on_distribute_h,
            icon_key="distribute_h",
        )
        self._configure_action(
            self._distribute_v_button,
            self._ts("V-Distribute"),
            self._on_distribute_v,
            icon_key="distribute_v",
        )
        dist_row.addWidget(self._distribute_h_button)
        dist_row.addWidget(self._distribute_v_button)
        dist_row.addStretch(1)
        actions.addLayout(dist_row)

        layout.addLayout(actions)

    def _build_basic_tab(self, container: QWidget) -> None:
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self._basic_tab_hint.setWordWrap(True)
        layout.addWidget(self._basic_tab_hint)

        self._basic_list_widget.setViewMode(QListWidget.ViewMode.ListMode)
        self._basic_list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self._basic_list_widget.setDragEnabled(True)
        self._basic_list_widget.setUniformItemSizes(True)
        self._basic_list_widget.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self._basic_list_widget, 1)
        self._refresh_basic_shape_list()

    def _refresh_basic_shape_list(self) -> None:
        self._basic_list_widget.clear()
        ordered = sorted(
            self._basic_shape_defs,
            key=lambda item: (
                self._translate_basic_shape_category(item.category),
                self._translate_basic_shape_name(item.display_name),
            ),
        )
        for definition in ordered:
            category_label = self._translate_basic_shape_category(definition.category)
            name_label = self._translate_basic_shape_name(definition.display_name)
            label = f"[{category_label}] {name_label}"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, definition.shape_id)
            item.setToolTip(name_label)
            self._basic_list_widget.addItem(item)

    def _make_icon_button(self, icon_key: str, label: str) -> QPushButton:
        """Create a QPushButton with an SVG icon, falling back to text label."""
        from loomstroke.ui.icon_loader import load_svg_icon  # noqa: PLC0415

        btn = QPushButton(self)
        svg_name = self._ACTION_ICON_MAP.get(icon_key)
        if svg_name:
            icon = load_svg_icon(self._ACTION_ICON_DIR / svg_name)
            if icon is not None:
                btn.setIcon(icon)
                btn.setToolTip(label)
                btn.setProperty("_shape_manager_icon_set", True)
                return btn
        btn.setText(label)
        btn.setToolTip(label)
        return btn

    def _configure_action(
        self,
        button: QPushButton,
        label: str,
        handler: Callable[[], None],
        *,
        icon_key: str | None = None,
    ) -> None:
        if icon_key is not None and not button.property("_shape_manager_icon_set"):
            from loomstroke.ui.icon_loader import load_svg_icon  # noqa: PLC0415

            svg_name = self._ACTION_ICON_MAP.get(icon_key)
            if svg_name:
                icon = load_svg_icon(self._ACTION_ICON_DIR / svg_name)
                if icon is not None:
                    button.setIcon(icon)
                    button.setToolTip(label)
                    button.setText("")
                    button.setProperty("_shape_manager_icon_set", True)
        button.setToolTip(label)
        if not button.property("_shape_manager_icon_set"):
            button.setText(label)
        if not button.property("_shape_manager_handler_connected"):
            button.clicked.connect(handler)
            button.setProperty("_shape_manager_handler_connected", True)

    def changeEvent(self, event: QEvent) -> None:
        if not is_valid(self):
            return
        with contextlib.suppress(RuntimeError):
            super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def refresh_translations(self) -> None:
        if not is_valid(self):
            return
        self._retranslate_ui()

        refresh_library_load_feedback(self)

    def _subscribe_language_event(self) -> None:
        if self._language_event_token is not None:
            return
        subscribe = getattr(self._language_event_bus, "on", None) or getattr(
            self._language_event_bus,
            "subscribe",
            None,
        )
        if callable(subscribe):
            self._language_event_token = subscribe(
                Events.LANGUAGE_CHANGED,
                self._language_event_handler,
            )

    def _unsubscribe_language_event(self) -> None:
        if self._language_event_token is None:
            return
        unsubscribe = getattr(self._language_event_bus, "off", None) or getattr(
            self._language_event_bus,
            "unsubscribe",
            None,
        )
        if callable(unsubscribe):
            with contextlib.suppress(Exception):
                unsubscribe(Events.LANGUAGE_CHANGED, self._language_event_token)
        self._language_event_token = None

    def _on_language_changed(self, _payload: object) -> None:
        if not is_valid(self):
            return
        with contextlib.suppress(RuntimeError):
            self.refresh_translations()

    def _on_destroyed(self, _obj: object | None = None) -> None:
        retire_library_load_feedback(self, final=True)
        self._unsubscribe_language_event()
        self._unsubscribe_drop_event()

    def _retranslate_ui(self) -> None:
        self._tabs.setTabText(0, self._ts("Shape Manager"))
        self._tabs.setTabText(1, self._ts("Basic Shapes"))

        for btn, label in [
            (self._import_button, self._ts("Import")),
            (self._register_button, self._ts("Add Shape")),
            (self._delete_button, self._ts("Delete")),
        ]:
            btn.setToolTip(label)
            if not btn.property("_shape_manager_icon_set"):
                btn.setText(label)

        self._label_search_hint.setText("")
        self._search_edit.setPlaceholderText(self._ts("Search..."))

        self._label_category.setText(self._ts("Category:"))
        self._label_tag.setText(self._ts("Tag:"))
        self._label_name.setText(self._ts("Name:"))
        self._label_category_edit.setText(self._ts("Category:"))
        self._label_tags.setText(self._ts("Tags:"))
        self._label_align_reference.setText(self._ts("Align Reference:"))

        self._name_edit.setPlaceholderText(self._ts("Select a shape"))
        self._category_edit.setPlaceholderText(self._ts("custom"))
        self._tags_edit.setPlaceholderText(self._ts("comma-separated"))

        if self._align_reference_combo.count() >= 2:
            self._align_reference_combo.setItemText(0, self._ts("Canvas"))
            self._align_reference_combo.setItemText(1, self._ts("Selection"))

        self._basic_tab_hint.setText(
            self._ts("Drag onto the canvas to add shapes. Right-click for conversion handles.")
        )

        self._configure_action(
            self._fit_contain_button,
            self._ts("Fit Contain"),
            self._on_fit_contain,
        )
        self._configure_action(
            self._fit_stretch_button,
            self._ts("Fit Stretch"),
            self._on_fit_stretch,
        )
        self._configure_action(self._center_button, self._ts("Center"), self._on_center_canvas)
        self._configure_action(
            self._align_top_button,
            self._ts("Top"),
            lambda: self._on_align("top"),
        )
        self._configure_action(
            self._align_middle_button, self._ts("V-Center"), lambda: self._on_align("center_y")
        )
        self._configure_action(
            self._align_bottom_button, self._ts("Bottom"), lambda: self._on_align("bottom")
        )
        self._configure_action(
            self._align_left_button,
            self._ts("Left"),
            lambda: self._on_align("left"),
        )
        self._configure_action(
            self._align_center_button, self._ts("H-Center"), lambda: self._on_align("center_x")
        )
        self._configure_action(
            self._align_right_button,
            self._ts("Right"),
            lambda: self._on_align("right"),
        )
        self._configure_action(
            self._distribute_h_button, self._ts("H-Distribute"), self._on_distribute_h
        )
        self._configure_action(
            self._distribute_v_button, self._ts("V-Distribute"), self._on_distribute_v
        )
        self._refresh_categories()
        self._refresh_basic_shape_list()

    # --- Data -----------------------------------------------------------
    def _apply_settings(self) -> None:
        if not self._settings:
            return
        last_category = self._settings.get("plugins/shape_manager/ui/last_category", "All")
        last_tag = self._settings.get("plugins/shape_manager/ui/last_tag", "All")
        search_text = self._settings.get("plugins/shape_manager/ui/search_text", "")
        self._search_edit.blockSignals(True)
        self._search_edit.setText(str(search_text or ""))
        self._search_edit.blockSignals(False)
        self._set_category(last_category)
        self._set_tag(last_tag)

    def _refresh_categories(self) -> None:
        categories = {record.category for record in self._index.all() if record.category}
        ordered_categories = sorted(categories, key=str.lower)
        if DEFAULT_CATEGORY not in categories:
            ordered_categories.append(DEFAULT_CATEGORY)
        current = self._category_combo.currentData()
        self._category_combo.blockSignals(True)
        self._category_combo.clear()
        self._category_combo.addItem(self._ts("All"), None)
        for category in ordered_categories:
            self._category_combo.addItem(
                self._translate_basic_shape_category(category),
                category,
            )
        self._category_combo.blockSignals(False)
        self._set_category(current)
        self._refresh_tags()

    def _refresh_tags(self) -> None:
        tag_map: dict[str, str] = {}
        for record in self._index.all():
            for tag in record.tags:
                key = tag.lower()
                if key not in tag_map:
                    tag_map[key] = tag
        current = self._tag_combo.currentData()
        self._tag_combo.blockSignals(True)
        self._tag_combo.clear()
        self._tag_combo.addItem(self._ts("All"), None)
        for tag in sorted(tag_map.values(), key=str.lower):
            self._tag_combo.addItem(tag, tag)
        self._tag_combo.blockSignals(False)
        self._set_tag(current)

    def _set_category(self, category: object) -> None:
        normalized = self._normalize_filter_value(category)
        index = self._category_combo.findData(normalized)
        self._category_combo.setCurrentIndex(index if index >= 0 else 0)

    def _set_tag(self, tag: object) -> None:
        normalized = self._normalize_filter_value(tag)
        index = self._tag_combo.findData(normalized)
        self._tag_combo.setCurrentIndex(index if index >= 0 else 0)

    def _filtered_records(self) -> list[ShapeRecord]:
        records = self._index.all()
        category = self._category_combo.currentData()
        if isinstance(category, str) and category:
            records = [record for record in records if record.category == category]
        tag = self._tag_combo.currentData()
        if isinstance(tag, str) and tag:
            tag_key = tag.lower()
            records = [
                record
                for record in records
                if any(tag_key == entry.lower() for entry in record.tags)
            ]
        text = self._search_edit.text().strip().lower()
        if text:
            records = [
                record
                for record in records
                if text in record.name.lower() or any(text in tag.lower() for tag in record.tags)
            ]
        return records

    def _refresh_list(self) -> None:
        selected = self._selected_shape_id if hasattr(self, "_selected_shape_id") else None
        self._list_widget.clear()
        for record in self._filtered_records():
            item = QListWidgetItem(record.name)
            item.setData(Qt.ItemDataRole.UserRole, record.shape_id)
            icon = self._load_thumbnail(record)
            if icon is not None:
                item.setIcon(QIcon(icon))
            item.setToolTip(record.name)
            self._list_widget.addItem(item)
        if selected:
            self._select_shape_id(selected)
        else:
            self._clear_shape_editor()

    def _load_thumbnail(self, record: ShapeRecord) -> QPixmap | None:
        thumb_path = self._data_root / record.thumb_path
        if not thumb_path.exists():
            return None
        pixmap = QPixmap(str(thumb_path))
        return pixmap if not pixmap.isNull() else None

    def _on_shape_selection_changed(self) -> None:
        item = self._list_widget.currentItem()
        if item is None:
            self._selected_shape_id = None
            self._clear_shape_editor()
            return
        shape_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(shape_id, str):
            self._selected_shape_id = None
            self._clear_shape_editor()
            return
        self._selected_shape_id = shape_id
        record = self._index.get(shape_id)
        if record is None:
            self._clear_shape_editor()
            return
        self._populate_shape_editor(record)

    def _populate_shape_editor(self, record: ShapeRecord) -> None:
        self._updating_edit_fields = True
        try:
            self._name_edit.setEnabled(True)
            self._category_edit.setEnabled(True)
            self._tags_edit.setEnabled(True)
            self._name_edit.blockSignals(True)
            self._category_edit.blockSignals(True)
            self._tags_edit.blockSignals(True)
            self._name_edit.setText(record.name)
            self._category_edit.setText(record.category)
            self._tags_edit.setText(_format_tags(record.tags))
        finally:
            self._name_edit.blockSignals(False)
            self._category_edit.blockSignals(False)
            self._tags_edit.blockSignals(False)
            self._updating_edit_fields = False

    def _clear_shape_editor(self) -> None:
        self._updating_edit_fields = True
        try:
            self._name_edit.blockSignals(True)
            self._category_edit.blockSignals(True)
            self._tags_edit.blockSignals(True)
            self._name_edit.setText("")
            self._category_edit.setText("")
            self._tags_edit.setText("")
            self._name_edit.setEnabled(False)
            self._category_edit.setEnabled(False)
            self._tags_edit.setEnabled(False)
        finally:
            self._name_edit.blockSignals(False)
            self._category_edit.blockSignals(False)
            self._tags_edit.blockSignals(False)
            self._updating_edit_fields = False

    def _apply_shape_edits(self) -> None:
        if self._updating_edit_fields:
            return
        shape_id = self._selected_shape_id
        if not shape_id:
            return
        record = self._index.get(shape_id)
        if record is None:
            return
        name = self._name_edit.text().strip()
        if not name:
            self._populate_shape_editor(record)
            return
        category = self._category_edit.text().strip() or record.category or DEFAULT_CATEGORY
        tags = _parse_tags(self._tags_edit.text())
        if name == record.name and category == record.category and tags == record.tags:
            return
        updated = replace(
            record,
            name=name,
            category=category,
            tags=tags,
            updated_at_iso=datetime.now(UTC).isoformat(),
        )
        self._index.update(updated)
        self._refresh_categories()
        self._refresh_list()
        self._select_shape_id(shape_id)

    def _select_shape_id(self, shape_id: str) -> None:
        for i in range(self._list_widget.count()):
            item = self._list_widget.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == shape_id:
                self._list_widget.setCurrentItem(item)
                return
        self._selected_shape_id = None
        self._clear_shape_editor()

    # --- Drag & drop ----------------------------------------------------
    def _build_drag_payload(self, shape_id: str) -> tuple[str | None, QPixmap | None]:
        record = self._index.get(shape_id)
        if record is None:
            return None, None
        keep_aspect = self._get_bool_setting("plugins/shape_manager/transform/keep_aspect", True)
        payload = build_payload(
            plugin_id=PLUGIN_ID,
            insert_policy=record.default_insert_policy,
            default_size_px=record.default_size_px,
            keep_aspect=keep_aspect,
            shape_id=record.shape_id,
        )
        return payload, self._load_thumbnail(record)

    def _build_basic_drag_payload(self, basic_shape_id: str) -> tuple[str | None, QPixmap | None]:
        definition = get_basic_shape_definition(basic_shape_id)
        if definition is None:
            return None, None
        payload = build_payload(
            plugin_id=PLUGIN_ID,
            kind="basic",
            basic_shape_id=definition.shape_id,
            insert_policy="default_size",
            default_size_px=definition.default_size_px,
            keep_aspect=True,
        )
        return payload, None

    def _subscribe_drop_event(self) -> None:
        if not self._host:
            return
        bus = self._host.events
        subscribe = getattr(bus, "on", None) or getattr(bus, "subscribe", None)
        if callable(subscribe):
            # Keep a strong reference to the bound method for weakref-based buses.
            self._drop_handler = self._on_canvas_drop
            self._event_token = subscribe("ui.canvas.drop", self._drop_handler)

    def _unsubscribe_drop_event(self) -> None:
        if not self._host or self._event_token is None:
            return
        bus = self._host.events
        unsubscribe = getattr(bus, "off", None) or getattr(bus, "unsubscribe", None)
        if callable(unsubscribe):
            with contextlib.suppress(Exception):
                unsubscribe("ui.canvas.drop", self._event_token)
        self._event_token = None
        self._drop_handler = None

    def _on_canvas_drop(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        if payload.get("mime") != MIME_TYPE:
            return
        payload_json = payload.get("payload_json")
        if not isinstance(payload_json, str):
            return
        data = parse_payload(payload_json)
        if not data or data.get("plugin_id") != PLUGIN_ID:
            return
        pos = payload.get("pos_canvas")
        if not isinstance(pos, (tuple, list)) or len(pos) < 2:
            return
        self._insert_shape_from_payload(data, (float(pos[0]), float(pos[1])))

    # --- Import / context menu -----------------------------------------
    def _show_context_menu(self, pos: QPoint) -> None:
        item = self._list_widget.itemAt(pos)
        if item is None:
            return
        self._list_widget.setCurrentItem(item)
        shape_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(shape_id, str):
            return
        menu = QMenu(self)
        rename_action = menu.addAction(self._ts("Rename"))
        tags_action = menu.addAction(self._ts("Edit Tags"))
        duplicate_action = menu.addAction(self._ts("Duplicate"))
        delete_action = menu.addAction(self._ts("Delete"))
        action = menu.exec(self._list_widget.mapToGlobal(pos))
        if action == rename_action:
            self._rename_shape(shape_id)
        elif action == tags_action:
            self._edit_tags(shape_id)
        elif action == duplicate_action:
            self._duplicate_shape(shape_id)
        elif action == delete_action:
            self._delete_shape(shape_id)

    def _on_import_svg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            self._ts("Import SVG"),
            "",
            self._ts("SVG Files (*.svg)"),
        )
        if not path:
            return
        try:
            raw = Path(path).read_text(encoding="utf-8")
        except Exception as exc:
            self._notify(f"{self._ts('Failed to read SVG')}: {exc}")
            return
        try:
            sanitized = sanitize_svg(raw)
            template = parse_svg(sanitized)
        except Exception as exc:
            self._notify(f"{self._ts('SVG import failed')}: {exc}")
            return
        try:
            tags = self._prompt_tags(title=self._ts("Import Tags"), initial=())
            if tags is None:
                return
            record = self._persist_shape(Path(path).stem, sanitized, template, tags=tags)
        except Exception as exc:
            self._notify(f"{self._ts('Saving shape failed')}: {exc}")
            return
        self._index.add(record)
        self._refresh_categories()
        self._refresh_list()
        self._select_shape_id(record.shape_id)
        self._insert_imported_svg_record(record)

    def _on_register_selection(self) -> None:  # noqa: PLR0912
        selection = self._resolve_selection()
        if not selection:
            self._notify(self._ts("Select vector shapes to register."))
            return

        objects: list[VectorObject] = []
        for _layer_id, obj, _bbox in selection:
            if isinstance(obj, VectorObject) and not obj.preview:
                objects.append(obj)

        if not objects:
            self._notify(self._ts("No valid vector shapes selected."))
            return

        count = len(objects)
        if count == 1:
            name, ok = QInputDialog.getText(
                self,
                self._ts("Register Shape"),
                self._ts("Name:"),
                text=self._ts("Shape"),
            )
            if not ok or not name.strip():
                return
            names = [name.strip()]
        else:
            base_name, ok = QInputDialog.getText(
                self,
                self._ts("Register Shapes"),
                self._ts("Base Name:"),
                text=self._ts("Shape"),
            )
            if not ok or not base_name.strip():
                return
            base = base_name.strip()
            names = [f"{base} {idx + 1}" for idx in range(count)]

        category = DEFAULT_CATEGORY
        tags = self._prompt_tags(title=self._ts("Register Tags"), initial=())
        if tags is None:
            return

        success = 0
        failures = 0
        for obj, name in zip(objects, names, strict=False):
            try:
                record = self._persist_shape_from_vector(name, obj, category=category, tags=tags)
                self._index.add(record)
                success += 1
            except Exception:
                logger.debug("Failed to register selected shape", exc_info=True)
                failures += 1

        if success:
            self._refresh_categories()
            self._refresh_list()
            if failures:
                self._notify(self._ts("Registered {} shape(s). Some failed.").format(success))
            else:
                self._notify(self._ts("Registered {} shape(s).").format(success))
        else:
            self._notify(self._ts("No shapes were registered."))

    def register_selection(self) -> None:
        self._on_register_selection()

    def _on_register_selection_merged(self) -> None:
        selection = self._resolve_selection()
        if not selection:
            self._notify(self._ts("Select vector shapes to register."))
            return

        objects: list[VectorObject] = []
        for _layer_id, obj, _bbox in selection:
            if isinstance(obj, VectorObject) and not obj.preview:
                objects.append(obj)

        if not objects:
            self._notify(self._ts("No valid vector shapes selected."))
            return

        name, ok = QInputDialog.getText(
            self,
            self._ts("Register Shape"),
            self._ts("Name:"),
            text=self._ts("Shape"),
        )
        if not ok or not name.strip():
            return

        category = DEFAULT_CATEGORY
        tags = self._prompt_tags(title=self._ts("Register Tags"), initial=())
        if tags is None:
            return

        base_style = objects[0].style
        mixed_styles = any(obj.style != base_style for obj in objects[1:])

        merged_paths: list[VectorPath] = []
        original_ids: list[str] = []
        for obj in objects:
            merged_paths.extend(obj.paths)
            vec_id = getattr(obj, "id", None)
            if isinstance(vec_id, str):
                original_ids.append(vec_id)

        merged = make_vector_object(
            merged_paths,
            base_style,
            meta={"source": "vector_selection_merge", "original_ids": original_ids},
        )

        try:
            record = self._persist_shape_from_vector(
                name.strip(), merged, category=category, tags=tags
            )
            self._index.add(record)
        except Exception:
            logger.debug("Failed to register merged shape", exc_info=True)
            self._notify(self._ts("Failed to register shape."))
            return

        self._refresh_categories()
        self._refresh_list()
        if mixed_styles:
            self._notify(self._ts("Registered shape (style unified from the first selection)."))
        else:
            self._notify(self._ts("Registered shape."))

    def register_selection_merged(self) -> None:
        self._on_register_selection_merged()

    def _persist_shape(
        self, name: str, sanitized_svg: str, template: ShapeTemplate, *, tags: tuple[str, ...]
    ) -> ShapeRecord:
        index = getattr(self, "_index", None)
        if issubclass(type(index), ShapeIndex):
            _require_no_load_failure(index)
        shape_id = str(uuid.uuid4())
        return persist_shape_assets(
            root=self._data_root,
            shape_id=shape_id,
            name=name or self._ts("Shape"),
            category=DEFAULT_CATEGORY,
            tags=tags,
            svg_text=sanitized_svg,
            template=template,
            render_thumbnail=render_svg_thumbnail,
            timestamp=datetime.now(UTC).isoformat(),
            default_size=self._get_int_setting("plugins/shape_manager/insert/default_size_px", 320),
        )

    def _persist_shape_from_vector(
        self, name: str, obj: VectorObject, *, category: str, tags: tuple[str, ...]
    ) -> ShapeRecord:
        index = getattr(self, "_index", None)
        if issubclass(type(index), ShapeIndex):
            _require_no_load_failure(index)
        shape_id = str(uuid.uuid4())
        bbox = compute_object_bbox(obj)
        if bbox is None:
            raise ValueError("Vector object has no bounds")

        meta: dict[str, object] = {}
        if isinstance(obj.meta, dict):
            meta.update(obj.meta)
        meta.setdefault("source", "vector_selection")
        if "original_id" not in meta:
            meta["original_id"] = obj.id
        template = ShapeTemplate(
            paths=tuple(obj.paths),
            style=obj.style,
            bbox=bbox,
            meta=meta,
        )

        svg_text = _vector_object_to_svg(obj, bbox)
        return persist_shape_assets(
            root=self._data_root,
            shape_id=shape_id,
            name=name or self._ts("Shape"),
            category=category or DEFAULT_CATEGORY,
            tags=tags,
            svg_text=svg_text,
            template=template,
            render_thumbnail=render_svg_thumbnail,
            timestamp=datetime.now(UTC).isoformat(),
            default_size=self._get_int_setting("plugins/shape_manager/insert/default_size_px", 320),
        )

    def _edit_tags(self, shape_id: str) -> None:
        index = self._index
        record = index.get(shape_id)
        if record is None:
            return
        if issubclass(type(index), ShapeIndex):
            _require_no_load_failure(index)
        tags = self._prompt_tags(title=self._ts("Edit Tags"), initial=record.tags)
        if tags is None:
            return
        updated = replace(
            record,
            tags=tags,
            updated_at_iso=datetime.now(UTC).isoformat(),
        )
        self._index.update(updated)
        self._refresh_categories()
        self._refresh_list()

    def _on_delete_selected_shape(self) -> None:
        shape_id = self._selected_shape_id
        if not shape_id:
            self._notify(self._ts("Select a shape to delete."))
            return
        index, root = self._index, self._data_root
        record = index.get(shape_id)
        if record is None:
            return
        box = QMessageBox(self)
        box.setWindowTitle(self._ts("Delete Shape"))
        box.setText(f"{self._ts('Delete')} '{record.name}'?")
        box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(QMessageBox.StandardButton.Cancel)
        result = box.exec()
        if result != QMessageBox.StandardButton.Ok:
            return
        if self._index is not index or self._data_root != root:
            return
        if not delete_shape_record(index, shape_id, record, root):
            return
        self._refresh_categories()
        self._refresh_list()

    def _prompt_tags(
        self, *, title: str, initial: tuple[str, ...] | None
    ) -> tuple[str, ...] | None:
        initial_text = _format_tags(initial or ())
        text, ok = QInputDialog.getText(
            self,
            title,
            self._ts("Tags (comma-separated):"),
            text=initial_text,
        )
        if not ok:
            return None
        return _parse_tags(text)

    def _rename_shape(self, shape_id: str) -> None:
        index = self._index
        record = index.get(shape_id)
        if record is None:
            return
        if issubclass(type(index), ShapeIndex):
            _require_no_load_failure(index)
        new_name, ok = QInputDialog.getText(
            self,
            self._ts("Rename Shape"),
            self._ts("Name:"),
            text=record.name,
        )
        if not ok or not new_name.strip():
            return
        name = new_name.strip()
        self._index.rename(shape_id, name)
        self._refresh_list()

    def _duplicate_shape(self, shape_id: str) -> None:
        index = self._index
        record = index.get(shape_id)
        if record is None:
            return
        if issubclass(type(index), ShapeIndex):
            _require_no_load_failure(index)
        src_dir = self._data_root / "shapes" / record.shape_id
        new_id = str(uuid.uuid4())
        dst_dir = self._data_root / "shapes" / new_id
        shutil.copytree(src_dir, dst_dir)
        now = datetime.now(UTC).isoformat()
        new_record = ShapeRecord(
            shape_id=new_id,
            name=f"{record.name} {self._ts('Copy')}",
            category=record.category,
            tags=record.tags,
            created_at_iso=now,
            updated_at_iso=now,
            svg_path=f"shapes/{new_id}/shape.svg",
            template_path=f"shapes/{new_id}/template.json",
            thumb_path=f"shapes/{new_id}/thumb.png",
            default_insert_policy=record.default_insert_policy,
            default_size_px=record.default_size_px,
        )
        self._index.add(new_record)
        self._refresh_categories()
        self._refresh_list()

    def _delete_shape(self, shape_id: str) -> None:
        index, root = self._index, self._data_root
        record = index.get(shape_id)
        if record is None:
            return
        result = QMessageBox.question(
            self,
            self._ts("Delete Shape"),
            f"{self._ts('Delete')} '{record.name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if result != QMessageBox.StandardButton.Yes:
            return
        if self._index is not index or self._data_root != root:
            return
        if not delete_shape_record(index, shape_id, record, root):
            return
        self._refresh_categories()
        self._refresh_list()

    # --- Canvas insertion ----------------------------------------------
    def _insert_imported_svg_record(self, record: ShapeRecord) -> bool:
        if not self._host:
            return False
        canvas_size = self._get_canvas_size()
        if canvas_size is None:
            return False
        payload = {
            "kind": "library",
            "shape_id": record.shape_id,
            "insert_policy": record.default_insert_policy,
            "default_size_px": record.default_size_px,
            "keep_aspect": True,
        }
        return self._insert_shape_from_payload(
            payload,
            (canvas_size[0] * 0.5, canvas_size[1] * 0.5),
            vector_kind="draw",
            select_inserted=True,
        )

    def _insert_shape_from_payload(
        self,
        payload: dict,
        pos: tuple[float, float],
        *,
        vector_kind: str = "shape",
        select_inserted: bool = False,
    ) -> bool:
        if not self._host:
            return False
        if str(payload.get("kind", "library")) == "basic":
            return self._insert_basic_shape_from_payload(payload, pos)
        return self._insert_library_shape_from_payload(
            payload,
            pos,
            vector_kind=vector_kind,
            select_inserted=select_inserted,
        )

    def _insert_library_shape_from_payload(
        self,
        payload: dict,
        pos: tuple[float, float],
        *,
        vector_kind: str,
        select_inserted: bool,
    ) -> bool:
        shape_id = str(payload.get("shape_id", ""))
        record = self._index.get(shape_id)
        if record is None:
            self._notify(self._ts("Shape not found."))
            return False
        template = self._load_template(record)
        if template is None:
            self._notify(self._ts("Template not available."))
            return False

        insert_policy = str(payload.get("insert_policy", record.default_insert_policy))
        keep_aspect = bool(payload.get("keep_aspect", True))
        default_size = int(payload.get("default_size_px", record.default_size_px))
        auto_fit = self._get_bool_setting("plugins/shape_manager/insert/auto_fit_on_drop", False)
        keep_stroke_width = self._get_bool_setting(
            "plugins/shape_manager/transform/keep_stroke_width", True
        )
        canvas_size = self._get_canvas_size()

        vector_obj = self._instantiate_template(
            template,
            pos,
            insert_policy=insert_policy,
            default_size=default_size,
            keep_aspect=keep_aspect,
            auto_fit=auto_fit,
            canvas_size=canvas_size,
            keep_stroke_width=keep_stroke_width,
        )
        if vector_obj is None:
            self._notify(self._ts("Failed to build shape."))
            return False

        canvas = self._host.canvas
        created_layer_id = self._create_drop_vector_layer_id(record.name, vector_kind=vector_kind)
        if not created_layer_id:
            self._notify(self._ts("Failed to create vector layer."))
            return False

        cmd = VectorCreateCommand(
            vector_obj,
            layer_id=created_layer_id,
            merge_key=None,
            created_layer_id=created_layer_id,
        )
        try:
            success = canvas.submit(cmd, merge_key=None)
        except Exception:
            success = False
        if not success:
            self._notify(self._ts("Canvas insert failed."))
            return False
        if select_inserted:
            self._select_inserted_vector(canvas, vector_obj, created_layer_id)
        return True

    def _insert_basic_shape_from_payload(self, payload: dict, pos: tuple[float, float]) -> bool:
        if not self._host:
            return False
        basic_shape_id = str(payload.get("basic_shape_id", ""))
        definition = get_basic_shape_definition(basic_shape_id)
        if definition is None:
            self._notify(self._ts("Basic shape not found."))
            return False
        try:
            default_size = int(payload.get("default_size_px", definition.default_size_px))
        except Exception:
            default_size = definition.default_size_px

        vector_obj = create_basic_shape_object(
            basic_shape_id,
            pos,
            default_size_px=default_size,
        )
        if basic_shape_id in ("stream_lines", "solid_flash", "urchin_flash"):
            vector_obj = configure_effect_shape(self._host, basic_shape_id, pos, default_size, self)
        if vector_obj is None:
            if basic_shape_id not in ("stream_lines", "solid_flash", "urchin_flash"):
                self._notify(self._ts("Failed to build basic shape."))
            return False

        canvas = self._host.canvas
        shape_label = self._translate_basic_shape_name(definition.display_name)
        created_layer_id = self._create_drop_vector_layer_id(shape_label)
        if not created_layer_id:
            self._notify(self._ts("Failed to create vector layer."))
            return False

        cmd = VectorCreateCommand(
            vector_obj,
            layer_id=created_layer_id,
            merge_key=None,
            created_layer_id=created_layer_id,
        )
        try:
            success = canvas.submit(cmd, merge_key=None)
        except Exception:
            success = False
        if not success:
            self._notify(self._ts("Canvas insert failed."))
            return False
        return True

    def _create_drop_vector_layer_id(
        self, shape_name: str = "", *, vector_kind: str = "shape"
    ) -> str | None:
        if not self._host:
            return None
        canvas = self._host.canvas
        layer_name = self._unique_shape_layer_name(canvas, shape_name) if shape_name else None
        add_vector_layer = getattr(canvas, "add_vector_layer", None)
        if callable(add_vector_layer):
            try:
                ref = add_vector_layer(layer_name, vector_kind=vector_kind)
                return getattr(ref, "id", None)
            except Exception:
                logger.debug("add_vector_layer failed", exc_info=True)
                return None
        add_layer = getattr(canvas, "add_layer", None)
        if callable(add_layer):
            try:
                ref = add_layer(
                    layer_name or "Shape Layer",
                    layer_type="vector",
                    vector_kind=vector_kind,
                )
                return getattr(ref, "id", None)
            except TypeError:
                logger.debug("add_layer does not accept layer_type", exc_info=True)
            except Exception:
                logger.debug("add_layer failed", exc_info=True)
        return None

    @staticmethod
    def _unique_shape_layer_name(canvas: object, base_name: str) -> str:
        """Generate a unique layer name like 'Ellipse', 'Ellipse 2', 'Ellipse 3'."""
        get_layers = getattr(canvas, "get_layers", None)
        existing_names: set[str] = set()
        if callable(get_layers):
            with contextlib.suppress(Exception):
                existing_names = {getattr(ref, "name", "") for ref in get_layers()}
        if base_name not in existing_names:
            return base_name
        n = 2
        while f"{base_name} {n}" in existing_names:
            n += 1
        return f"{base_name} {n}"

    @staticmethod
    def _select_inserted_vector(canvas: object, obj: VectorObject, layer_id: str) -> None:
        bbox = compute_object_bbox(obj)
        try:
            canvas._vector_selection = {  # type: ignore[attr-defined]  # duck-typed canvas attr
                "id": obj.id,
                "layer_id": layer_id,
                "items": [{"id": obj.id, "layer_id": layer_id}],
                "mode": "multi",
                "transform_bounds": bbox,
                "overlay_layer_id": layer_id,
                "source": "shape_manager_import",
            }
        except Exception:
            return
        try:
            if hasattr(canvas, "notify_vector_preview_changed"):
                canvas.notify_vector_preview_changed()
            elif hasattr(canvas, "_recompose"):
                canvas._recompose()
        except Exception:
            logger.debug("Inserted SVG vector selection update failed", exc_info=True)

    def _instantiate_template(
        self,
        template: ShapeTemplate,
        pos: tuple[float, float],
        *,
        insert_policy: str,
        default_size: int,
        keep_aspect: bool,
        auto_fit: bool,
        canvas_size: tuple[float, float] | None,
        keep_stroke_width: bool,
    ):
        bbox = template.bbox
        width = max(1.0, bbox[2] - bbox[0])
        height = max(1.0, bbox[3] - bbox[1])
        center = ((bbox[0] + bbox[2]) * 0.5, (bbox[1] + bbox[3]) * 0.5)

        if auto_fit and canvas_size is not None:
            insert_policy = "fit_canvas_contain"

        if (
            insert_policy in ("fit_canvas_contain", "fit_canvas_stretch")
            and canvas_size is not None
        ):
            canvas_w, canvas_h = canvas_size
            if insert_policy == "fit_canvas_stretch":
                sx = canvas_w / width
                sy = canvas_h / height
            else:
                s = min(canvas_w / width, canvas_h / height) if keep_aspect else 1.0
                sx = s if keep_aspect else canvas_w / width
                sy = s if keep_aspect else canvas_h / height
            target_center = (canvas_w * 0.5, canvas_h * 0.5)
        else:
            if keep_aspect:
                s = default_size / max(width, height)
                sx = sy = s
            else:
                sx = default_size / width
                sy = default_size / height
            target_center = pos

        obj = make_vector_object(template.paths, template.style, meta=dict(template.meta))
        obj = scale_object(obj, center, sx, sy, keep_stroke_width=keep_stroke_width)
        dx = target_center[0] - center[0]
        dy = target_center[1] - center[1]
        obj = translate_object(obj, dx, dy)
        return obj

    def _load_template(self, record: ShapeRecord) -> ShapeTemplate | None:
        template_path = self._data_root / record.template_path
        if not template_path.exists():
            return None
        try:
            payload = json.loads(template_path.read_text(encoding="utf-8"))
            return ShapeTemplate.from_dict(payload)
        except Exception:
            logger.debug("Failed to load template", exc_info=True)
            return None

    # --- Align / distribute -------------------------------------------
    def _on_fit_contain(self) -> None:
        self._fit_to_canvas(keep_aspect=True)

    def _on_fit_stretch(self) -> None:
        self._fit_to_canvas(keep_aspect=False)

    def _fit_to_canvas(self, *, keep_aspect: bool) -> None:
        selection = self._resolve_selection()
        if not selection:
            self._notify(self._ts("Select vector shapes to apply."))
            return
        canvas_size = self._get_canvas_size()
        if canvas_size is None:
            self._notify(self._ts("Canvas size unavailable."))
            return
        canvas_w, canvas_h = canvas_size
        updates = []
        keep_stroke_width = self._get_bool_setting(
            "plugins/shape_manager/transform/keep_stroke_width", True
        )
        for layer_id, obj, bbox in selection:
            if bbox is None:
                continue
            width = max(1.0, bbox[2] - bbox[0])
            height = max(1.0, bbox[3] - bbox[1])
            if keep_aspect:
                s = min(canvas_w / width, canvas_h / height)
                sx = sy = s
            else:
                sx = canvas_w / width
                sy = canvas_h / height
            center = ((bbox[0] + bbox[2]) * 0.5, (bbox[1] + bbox[3]) * 0.5)
            updated = scale_object(obj, center, sx, sy, keep_stroke_width=keep_stroke_width)
            dx = canvas_w * 0.5 - center[0]
            dy = canvas_h * 0.5 - center[1]
            updated = translate_object(updated, dx, dy)
            updates.append((layer_id, updated))
        self._submit_replacements(self._ts("Shape Fit"), updates)

    def _on_center_canvas(self) -> None:
        selection = self._resolve_selection()
        if not selection:
            self._notify(self._ts("Select vector shapes to apply."))
            return
        canvas_size = self._get_canvas_size()
        if canvas_size is None:
            self._notify(self._ts("Canvas size unavailable."))
            return
        canvas_center = (canvas_size[0] * 0.5, canvas_size[1] * 0.5)
        updates = []
        for layer_id, obj, bbox in selection:
            if bbox is None:
                continue
            center = ((bbox[0] + bbox[2]) * 0.5, (bbox[1] + bbox[3]) * 0.5)
            dx = canvas_center[0] - center[0]
            dy = canvas_center[1] - center[1]
            updates.append((layer_id, translate_object(obj, dx, dy)))
        self._submit_replacements(self._ts("Shape Center"), updates)

    def _on_align(self, mode: str) -> None:
        selection = self._resolve_selection()
        if not selection:
            self._notify(self._ts("Select vector shapes to apply."))
            return
        align_to = self._align_reference_combo.currentData()
        if align_to == "canvas":
            canvas_size = self._get_canvas_size()
            if canvas_size is None:
                self._notify(self._ts("Canvas size unavailable."))
                return
            target_bbox = (0.0, 0.0, canvas_size[0], canvas_size[1])
        else:
            target_bbox = merge_bboxes([bbox for _, _, bbox in selection if bbox is not None])
            if target_bbox is None:
                self._notify(self._ts("Selection bounds unavailable."))
                return
        updates = []
        for layer_id, obj, bbox in selection:
            if bbox is None:
                continue
            dx, dy = _align_delta(bbox, target_bbox, mode)
            updates.append((layer_id, translate_object(obj, dx, dy)))
        self._submit_replacements(self._ts("Shape Align"), updates)

    def _on_distribute_h(self) -> None:
        self._distribute(axis="x")

    def _on_distribute_v(self) -> None:
        self._distribute(axis="y")

    def _distribute(self, *, axis: str) -> None:
        selection = self._resolve_selection()
        if len(selection) < 3:
            self._notify(self._ts("Select at least 3 shapes to distribute."))
            return
        if axis == "x":
            sorted_items = sorted(selection, key=lambda item: (item[2][0] + item[2][2]) * 0.5)
            total = sum(item[2][2] - item[2][0] for item in sorted_items if item[2])
            min_x = sorted_items[0][2][0]
            max_x = sorted_items[-1][2][2]
            gap = max(0.0, (max_x - min_x - total) / (len(sorted_items) - 1))
            cursor = min_x
            updates = []
            for layer_id, obj, bbox in sorted_items:
                width = bbox[2] - bbox[0]
                dx = cursor - bbox[0]
                updates.append((layer_id, translate_object(obj, dx, 0.0)))
                cursor += width + gap
        else:
            sorted_items = sorted(selection, key=lambda item: (item[2][1] + item[2][3]) * 0.5)
            total = sum(item[2][3] - item[2][1] for item in sorted_items if item[2])
            min_y = sorted_items[0][2][1]
            max_y = sorted_items[-1][2][3]
            gap = max(0.0, (max_y - min_y - total) / (len(sorted_items) - 1))
            cursor = min_y
            updates = []
            for layer_id, obj, bbox in sorted_items:
                height = bbox[3] - bbox[1]
                dy = cursor - bbox[1]
                updates.append((layer_id, translate_object(obj, 0.0, dy)))
                cursor += height + gap
        self._submit_replacements(self._ts("Shape Distribute"), updates)

    def _submit_replacements(
        self, label: str, updates: Iterable[tuple[str | None, object]]
    ) -> None:
        if not self._host:
            return
        canvas = self._host.canvas
        canvas.begin_transaction(label)
        try:
            for layer_id, obj in updates:
                if obj is None:
                    continue
                cmd = VectorReplaceCommand(obj.id, obj, layer_id=layer_id)
                canvas.submit(cmd, merge_key=None)
        finally:
            canvas.end_transaction()

    def _resolve_selection(  # noqa: PLR0912
        self,
    ) -> list[tuple[str | None, object, tuple[float, float, float, float] | None]]:
        if not self._host:
            return []
        canvas = self._host.canvas
        selection = getattr(canvas, "_vector_selection", None)
        if not isinstance(selection, dict):
            return []
        ids: list[tuple[str, str | None]] = []
        if "items" in selection and isinstance(selection["items"], list):
            for item in selection["items"]:
                if not isinstance(item, dict):
                    continue
                vec_id = item.get("id")
                if isinstance(vec_id, str):
                    ids.append((vec_id, item.get("layer_id")))
        elif "ids" in selection and isinstance(selection["ids"], list):
            for vec_id in selection["ids"]:
                if isinstance(vec_id, str):
                    ids.append((vec_id, selection.get("layer_id")))
        else:
            vec_id = selection.get("id")
            if isinstance(vec_id, str):
                ids.append((vec_id, selection.get("layer_id")))

        if not ids:
            return []

        objects = _vector_store(canvas)
        obj_map = {getattr(obj, "id", None): (layer_id, obj) for layer_id, obj in objects}
        result = []
        for vec_id, layer_hint in ids:
            entry = obj_map.get(vec_id)
            if entry is None:
                continue
            layer_id, obj = entry
            if layer_hint is not None and layer_id not in (None, layer_hint):
                continue
            bbox = compute_object_bbox(obj)
            if bbox is None:
                continue
            result.append((layer_id, obj, bbox))
        return result

    def can_convert_selected_basic_shapes(self) -> bool:
        for _layer_id, obj, _bbox in self._resolve_selection():
            if is_editable_parametric_shape(obj):
                return True
        return False

    def convert_selected_basic_shapes_to_vector(self) -> None:
        updates: list[tuple[str | None, object]] = []
        for layer_id, obj, _bbox in self._resolve_selection():
            converted = convert_parametric_to_vector(obj)
            if converted is None:
                continue
            updates.append((layer_id, converted))
        if not updates:
            self._notify(self._ts("No editable basic shapes are selected."))
            return
        self._submit_replacements(self._ts("Convert Basic Shape"), updates)
        self._mark_converted_layers_as_draw_vector(
            layer_id for layer_id, _obj in updates if layer_id is not None
        )
        self._notify(self._ts("Converted {} basic shape(s).").format(len(updates)))

    def _mark_converted_layers_as_draw_vector(self, layer_ids: Iterable[str]) -> None:
        if not self._host:
            return
        canvas = self._host.canvas
        for layer_id in dict.fromkeys(layer_ids):
            if self._layer_has_editable_parametric_shapes(canvas, layer_id):
                continue
            name = self._unique_vector_layer_name(canvas, layer_id)
            set_kind = getattr(canvas, "set_vector_kind", None)
            if callable(set_kind):
                try:
                    if set_kind(layer_id, "draw", name=name):
                        continue
                except TypeError:
                    set_kind(layer_id, "draw")
            self._rename_layer_direct(canvas, layer_id, name)

    @staticmethod
    def _layer_has_editable_parametric_shapes(canvas: object, layer_id: str) -> bool:
        for item_layer_id, obj in _vector_store(canvas):
            if item_layer_id != layer_id:
                continue
            if is_editable_parametric_shape(obj):
                return True
        return False

    @staticmethod
    def _unique_vector_layer_name(canvas: object, layer_id: str) -> str:
        get_layers = getattr(canvas, "get_layers", None)
        existing_names: set[str] = set()
        if callable(get_layers):
            with contextlib.suppress(Exception):
                existing_names = {
                    str(getattr(ref, "name", ""))
                    for ref in get_layers()
                    if getattr(ref, "id", None) != layer_id
                }
        n = 1
        while True:
            name = f"Vector Layer {n}"
            if name not in existing_names:
                return name
            n += 1

    @staticmethod
    def _rename_layer_direct(canvas: object, layer_id: str, name: str) -> None:
        getter = getattr(canvas, "_get_layer_by_id", None)
        if not callable(getter):
            return
        layer = getter(layer_id)
        if layer is not None and hasattr(layer, "name"):
            layer.name = name

    # --- Utility -------------------------------------------------------
    def _on_filter_changed(self) -> None:
        if self._settings:
            self._settings.set(
                "plugins/shape_manager/ui/last_category",
                self._category_combo.currentData() or "All",
            )
            self._settings.set(
                "plugins/shape_manager/ui/last_tag",
                self._tag_combo.currentData() or "All",
            )
            self._settings.set("plugins/shape_manager/ui/search_text", self._search_edit.text())
        self._refresh_list()

    def _get_canvas_size(self) -> tuple[float, float] | None:
        if not self._host:
            return None
        canvas = self._host.canvas
        width = getattr(canvas, "_width", None)
        height = getattr(canvas, "_height", None)
        if width is None or height is None:
            width = getattr(canvas, "width", None)
            height = getattr(canvas, "height", None)
        if width is None or height is None:
            return None
        try:
            return (float(width), float(height))
        except (TypeError, ValueError):
            return None

    def _get_bool_setting(self, key: str, default: bool) -> bool:
        if not self._settings:
            return default
        return bool(self._settings.get(key, default))

    def _get_int_setting(self, key: str, default: int) -> int:
        if not self._settings:
            return default
        try:
            return int(self._settings.get(key, default))
        except Exception:
            return default

    def _notify(self, message: str) -> None:
        if self._host:
            try:
                self._host.ui.show_message(message)
                return
            except Exception:
                logger.debug("ui show_message failed", exc_info=True)
        QMessageBox.information(self, self._ts("Shape Manager"), message)

    def _ts(self, text: str) -> str:
        from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

        return translated_literal_domain("ShapeManagerPlugin", text, use_fallback=True)

    def _translate_basic_shape_category(self, category: str) -> str:
        from loomstroke.ui.translations import translate_with_fallback  # noqa: PLC0415

        return translate_with_fallback("ShapeManagerPlugin", category)

    def _translate_basic_shape_name(self, name: str) -> str:
        from loomstroke.ui.translations import translate_with_fallback  # noqa: PLC0415

        return translate_with_fallback("ShapeManagerPlugin", name)

    def _normalize_filter_value(self, value: object) -> str | None:
        if value is None:
            return None
        if isinstance(value, str):
            candidate = value.strip()
            if not candidate or candidate in {"All", self._ts("All")}:
                return None
            return candidate
        return str(value)


def _vector_store(canvas) -> list[tuple[str | None, object]]:
    getter = getattr(canvas, "get_vector_objects", None)
    if callable(getter):
        try:
            return getter()
        except Exception:
            logger.debug("get_vector_objects failed", exc_info=True)
    store = getattr(canvas, "_vector_objects", None)
    if store is None:
        return []
    return [(None, obj) for obj in store]


def _align_delta(
    bbox: tuple[float, float, float, float],
    target: tuple[float, float, float, float],
    mode: str,
) -> tuple[float, float]:
    min_x, min_y, max_x, max_y = bbox
    t_min_x, t_min_y, t_max_x, t_max_y = target
    dx = 0.0
    dy = 0.0
    if mode == "left":
        dx = t_min_x - min_x
    elif mode == "center_x":
        dx = (t_min_x + t_max_x) * 0.5 - (min_x + max_x) * 0.5
    elif mode == "right":
        dx = t_max_x - max_x
    elif mode == "top":
        dy = t_min_y - min_y
    elif mode == "center_y":
        dy = (t_min_y + t_max_y) * 0.5 - (min_y + max_y) * 0.5
    elif mode == "bottom":
        dy = t_max_y - max_y
    return dx, dy


def _vector_object_to_svg(obj: VectorObject, bbox: tuple[float, float, float, float]) -> str:
    min_x, min_y, max_x, max_y = bbox
    width = max(1.0, max_x - min_x)
    height = max(1.0, max_y - min_y)
    view_box = f"{_fmt_float(min_x)} {_fmt_float(min_y)} {_fmt_float(width)} {_fmt_float(height)}"

    style_attrs = _style_to_svg_attrs(obj.style)
    attr_str = " ".join(f'{key}="{value}"' for key, value in style_attrs.items())
    path_elements: list[str] = []
    for path in obj.paths:
        d = _vector_path_to_svg_d(path)
        if not d:
            continue
        if attr_str:
            path_elements.append(f'<path d="{d}" {attr_str} />')
        else:
            path_elements.append(f'<path d="{d}" />')
    body = "\n  ".join(path_elements)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view_box}" '
        f'width="{_fmt_float(width)}" height="{_fmt_float(height)}">\n'
        f"  {body}\n"
        "</svg>\n"
    )


def _vector_path_to_svg_d(path: VectorPath) -> str:
    anchors = list(path.anchors)
    if not anchors:
        return ""
    start = anchors[0]
    parts = [f"M {_fmt_float(start.pos[0])} {_fmt_float(start.pos[1])}"]
    for prev, curr in itertools.pairwise(anchors):
        parts.append(_svg_segment(prev, curr))
    if path.closed:
        first = anchors[0]
        last = anchors[-1]
        if last.pos != first.pos or last.h_out or first.h_in:
            parts.append(_svg_segment(last, first))
        parts.append("Z")
    return " ".join(parts)


def _svg_segment(prev: Anchor, curr: Anchor) -> str:
    has_curve = bool(prev.h_out or curr.h_in)
    if has_curve:
        c1 = _handle_point(prev.pos, prev.h_out)
        c2 = _handle_point(curr.pos, curr.h_in)
        return (
            f"C {_fmt_float(c1[0])} {_fmt_float(c1[1])} "
            f"{_fmt_float(c2[0])} {_fmt_float(c2[1])} "
            f"{_fmt_float(curr.pos[0])} {_fmt_float(curr.pos[1])}"
        )
    return f"L {_fmt_float(curr.pos[0])} {_fmt_float(curr.pos[1])}"


def _handle_point(
    pos: tuple[float, float], handle: tuple[float, float] | None
) -> tuple[float, float]:
    if handle is None:
        return pos
    return (pos[0] + handle[0], pos[1] + handle[1])


def _style_to_svg_attrs(style: VectorStyle) -> dict[str, str]:
    stroke = _rgb_to_hex(style.stroke_color)
    fill = "none"
    if style.fill_enabled and style.fill_color is not None:
        fill = _rgb_to_hex(style.fill_color)
    attrs = {
        "fill": fill,
        "stroke": stroke,
        "stroke-width": _fmt_float(style.stroke_width),
        "stroke-linejoin": str(style.join),
        "stroke-linecap": str(style.cap),
        "stroke-miterlimit": _fmt_float(style.miter_limit),
    }
    if style.dash:
        attrs["stroke-dasharray"] = " ".join(_fmt_float(v) for v in style.dash)
    return attrs


def _rgb_to_hex(value: tuple[int, int, int]) -> str:
    r = max(0, min(255, int(value[0])))
    g = max(0, min(255, int(value[1])))
    b = max(0, min(255, int(value[2])))
    return f"#{r:02x}{g:02x}{b:02x}"


def _fmt_float(value: float) -> str:
    text = f"{value:.3f}"
    text = text.rstrip("0").rstrip(".")
    return text if text else "0"


def _format_tags(tags: tuple[str, ...]) -> str:
    if not tags:
        return ""
    return ", ".join(tags)


def _parse_tags(text: str) -> tuple[str, ...]:
    if not text:
        return ()
    cleaned = text.replace(",", " ")
    raw_parts = [part.strip() for part in cleaned.split() if part.strip()]
    tags: list[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        if part.startswith("#"):
            part = part[1:]  # noqa: PLW2901
        if not part:
            continue
        key = part.lower()
        if key in seen:
            continue
        seen.add(key)
        tags.append(part)
    return tuple(tags)
