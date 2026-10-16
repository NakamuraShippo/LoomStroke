"""Category tab state and UI helpers for ComfyUI node tabs."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QLabel,
    QListWidget,
    QListWidgetItem,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


@dataclass
class NodeMetadata:
    """Precomputed facts used for category filtering."""

    node_id: str
    node_type: str
    display_name: str
    param_names: tuple[str, ...]
    has_image_param: bool
    is_sampler: bool
    is_controlnet: bool
    value_types: tuple[str, ...]


CategoryFilter = Callable[[NodeMetadata], bool]


@dataclass
class CategoryPage:
    """Container for category-specific UI elements."""

    key: str
    title: str
    widget: QWidget
    list_widget: QListWidget
    tab_container: QWidget
    tab_layout: QVBoxLayout
    filter_func: CategoryFilter
    parent_tab: QTabWidget
    visible_node_ids: set[str] = field(default_factory=set)
    placeholder_label: QLabel | None = None


class CategoryTabManager:
    """Owns category pages, active category state, and tab visibility."""

    def __init__(
        self,
        *,
        category_tabs: QTabWidget,
        parameter_tabs: QTabWidget,
        translate: Callable[[str], str],
        entries_provider: Callable[[], Iterable[NodeMetadata]],
        node_id_for_tab: Callable[[int], str | None],
        tab_index_for_node: Callable[[str], int | None],
    ) -> None:
        self.pages: dict[str, CategoryPage] = {}
        self.active_category_key = ""
        self._category_tabs = category_tabs
        self._parameter_tabs = parameter_tabs
        self._translate = translate
        self._entries_provider = entries_provider
        self._node_id_for_tab = node_id_for_tab
        self._tab_index_for_node = tab_index_for_node
        self._selection_sync = False
        self._parameter_tabs_parent: QWidget | None = None

    def set_tab_key(self, tab_widget: QTabWidget, index: int, key: str) -> None:
        tab_widget.tabBar().setTabData(index, key)

    def tab_key(self, tab_widget: QTabWidget, index: int) -> str | None:
        if index < 0:
            return None
        value = tab_widget.tabBar().tabData(index)
        return value if isinstance(value, str) else None

    def build_page(
        self,
        *,
        key: str,
        title: str,
        filter_func: CategoryFilter,
        parent_tab: QTabWidget | None = None,
    ) -> CategoryPage:
        page_widget = QWidget()
        page_layout = QVBoxLayout(page_widget)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)

        list_widget = QListWidget()
        list_widget.setAlternatingRowColors(True)
        list_widget.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        list_widget.itemSelectionChanged.connect(
            lambda key=key: self.on_list_selection_changed(key)
        )
        splitter.addWidget(list_widget)

        tab_container = QWidget()
        tab_layout = QVBoxLayout(tab_container)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(0)
        tab_layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        placeholder = QLabel(self._translate("No nodes match this category."))
        placeholder.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        placeholder.setStyleSheet("color: #888; padding: 12px;")
        tab_layout.addWidget(placeholder)
        splitter.addWidget(tab_container)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        page_layout.addWidget(splitter)

        parent = parent_tab or self._category_tabs
        page = CategoryPage(
            key=key,
            title=title,
            widget=page_widget,
            list_widget=list_widget,
            tab_container=tab_container,
            tab_layout=tab_layout,
            filter_func=filter_func,
            parent_tab=parent,
            placeholder_label=placeholder,
        )
        self.pages[key] = page
        index = parent.addTab(page_widget, self._translate(title))
        self.set_tab_key(parent, index, key)
        return page

    def refresh(self) -> None:
        for page in self.pages.values():
            self.populate(page)

    def activate_category(self, key: str) -> None:
        page = self.pages.get(key)
        if not page:
            return
        self.active_category_key = key
        self.attach_parameter_tabs(page)
        self.populate(page)
        self.apply_tab_visibility(page.visible_node_ids)

    def apply_active_filter(self) -> None:
        if self.active_category_key not in self.pages:
            return
        self.activate_category(self.active_category_key)

    def attach_parameter_tabs(self, page: CategoryPage) -> None:
        if self._parameter_tabs_parent is page.tab_container:
            return
        self.detach_parameter_tabs()
        self._parameter_tabs.setParent(page.tab_container)
        page.tab_layout.addWidget(self._parameter_tabs)
        self._parameter_tabs_parent = page.tab_container

    def detach_parameter_tabs(self) -> None:
        if not self._parameter_tabs_parent:
            return
        layout = self._parameter_tabs_parent.layout()
        if layout is not None:
            layout.removeWidget(self._parameter_tabs)
        self._parameter_tabs.setParent(None)
        self._parameter_tabs_parent = None

    def populate(self, page: CategoryPage) -> None:
        entries = [meta for meta in self._entries_provider() if page.filter_func(meta)]
        page.visible_node_ids = {entry.node_id for entry in entries}

        page.list_widget.blockSignals(True)
        page.list_widget.clear()
        for entry in entries:
            item = QListWidgetItem(entry.display_name)
            tooltip = f"{entry.node_type} • {entry.node_id}" if entry.node_type else entry.node_id
            item.setToolTip(tooltip)
            item.setData(Qt.ItemDataRole.UserRole, entry.node_id)
            page.list_widget.addItem(item)
        page.list_widget.blockSignals(False)

        if entries:
            self._selection_sync = True
            self.select_list_item(page, entries[0].node_id)
            self._selection_sync = False
            if page.key == self.active_category_key:
                self.focus_node(entries[0].node_id)
        else:
            page.list_widget.clearSelection()

        self.update_placeholder(page)

    def apply_tab_visibility(self, allowed_ids: set[str] | None) -> None:
        match_all = allowed_ids is None
        allowed = allowed_ids or set()
        first_visible: int | None = None
        for index in range(self._parameter_tabs.count()):
            node_id = self._node_id_for_tab(index)
            visible = match_all or (node_id in allowed)
            try:
                self._parameter_tabs.setTabVisible(index, visible)
            except AttributeError:
                widget = self._parameter_tabs.widget(index)
                if widget is not None:
                    widget.setVisible(visible)
            if visible and first_visible is None:
                first_visible = index
        if (not match_all) and allowed and first_visible is not None:
            current_id = self._node_id_for_tab(self._parameter_tabs.currentIndex())
            if current_id not in allowed:
                self._selection_sync = True
                self._parameter_tabs.setCurrentIndex(first_visible)
                self._selection_sync = False
        page = self.pages.get(self.active_category_key)
        if page:
            self.update_placeholder(page)

    def update_placeholder(self, page: CategoryPage) -> None:
        has_nodes = bool(page.visible_node_ids)
        if page.placeholder_label:
            page.placeholder_label.setVisible(not has_nodes)
        if self._parameter_tabs_parent is page.tab_container:
            self._parameter_tabs.setVisible(has_nodes)

    def on_list_selection_changed(self, category_key: str) -> None:
        if self._selection_sync or category_key != self.active_category_key:
            return
        page = self.pages.get(category_key)
        if not page:
            return
        item = page.list_widget.currentItem()
        if not item:
            return
        node_id = item.data(Qt.ItemDataRole.UserRole)
        self.focus_node(str(node_id) if node_id is not None else None)

    def on_parameter_tab_changed(self, index: int) -> None:
        if self._selection_sync:
            return
        node_id = self._node_id_for_tab(index)
        if not node_id:
            return
        page = self.pages.get(self.active_category_key)
        if not page or node_id not in page.visible_node_ids:
            return
        self._selection_sync = True
        self.select_list_item(page, node_id)
        self._selection_sync = False

    def focus_node(self, node_id: str | None) -> None:
        if not node_id:
            return
        index = self._tab_index_for_node(node_id)
        if index is None:
            return
        self._selection_sync = True
        self._parameter_tabs.setCurrentIndex(index)
        self._selection_sync = False

    def select_list_item(self, page: CategoryPage, node_id: str) -> None:
        for row in range(page.list_widget.count()):
            item = page.list_widget.item(row)
            if item.data(Qt.ItemDataRole.UserRole) == node_id:
                page.list_widget.setCurrentRow(row)
                page.list_widget.scrollToItem(item)
                break


__all__ = [
    "CategoryFilter",
    "CategoryPage",
    "CategoryTabManager",
    "NodeMetadata",
]
