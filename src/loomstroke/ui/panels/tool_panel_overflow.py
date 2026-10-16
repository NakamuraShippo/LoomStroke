"""Overflow menu controller for :mod:`loomstroke.ui.panels.tool_panel`."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QSize
from PySide6.QtWidgets import QSizePolicy, QWidget


class ToolOverflowMenuController:
    """Owns ToolPropertiesPanel bar overflow layout decisions."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def bar_items_in_order(self) -> list[QWidget]:
        owner = self._owner
        if not owner._bar_items:
            return []
        return [item for item in owner._bar_items if item is not None]

    def update(self) -> None:
        owner = self._owner
        if not self.can_update():
            return
        if owner._content_container is not None and owner._content_container.width() <= 0:
            owner._content_container.adjustSize()

        items = self.bar_items_in_order()
        candidates = self.collect_candidates(items)
        if not items or not candidates:
            self.update_no_candidates(items)
            return
        self.update_with_candidates(items, candidates)

    def can_update(self) -> bool:
        owner = self._owner
        return (
            owner._bar_mode
            and owner._overflow_button is not None
            and owner.isVisible()
            and not owner._resize_in_progress
        )

    def collect_candidates(self, items: list[QWidget]) -> list[QWidget]:
        owner = self._owner
        return [item for item in items if item.isVisible() or item in owner._overflowed_items]

    def update_no_candidates(self, items: list[QWidget]) -> None:
        self.rebuild_overflow_layout([])
        self.rebuild_bar_layout(items)
        self.set_overflow_visible(False)

    def update_with_candidates(
        self,
        items: list[QWidget],
        candidates: list[QWidget],
    ) -> None:
        owner = self._owner
        available = self.get_scroll_area_width()
        if available <= 0:
            owner._schedule_overflow_update(50)
            return

        required = self.required_width(candidates)
        if required <= available:
            self.update_no_candidates(items)
            return

        self.set_overflow_visible(True)
        if owner.layout() is not None:
            owner.layout().activate()

        available = self.get_scroll_area_width()
        if available <= 0:
            return

        _, overflow_items = self.split_items(candidates, available)
        if not overflow_items:
            self.update_no_candidates(items)
            return

        self.rebuild_overflow_layout(overflow_items)
        overflow_set = set(overflow_items)
        bar_items = [item for item in items if item not in overflow_set]
        self.rebuild_bar_layout(bar_items)

    def get_scroll_area_width(self) -> int:
        return int(self._owner._get_scroll_area_width())

    def set_overflow_visible(self, visible: bool) -> None:
        overflow_button = self._owner._overflow_button
        if overflow_button is None:
            return
        overflow_button.setVisible(visible)

    def required_width(self, items: list[QWidget]) -> int:
        if not items:
            return 0
        spacing = self._owner._content_layout.spacing()
        spacing = max(spacing, 0)
        total = 0
        for index, item in enumerate(items):
            width = self.item_width(item)
            if index:
                total += spacing
            total += width
        return total

    def split_items(
        self, items: list[QWidget], available_width: int
    ) -> tuple[list[QWidget], list[QWidget]]:
        if available_width <= 0:
            return [], list(items)
        spacing = self._owner._content_layout.spacing()
        spacing = max(spacing, 0)
        visible: list[QWidget] = []
        overflow: list[QWidget] = []
        used = 0
        for index, item in enumerate(items):
            width = self.item_width(item)
            next_used = used + width + (spacing if visible else 0)
            if next_used > available_width:
                overflow = items[index:]
                break
            visible.append(item)
            used = next_used
        return visible, overflow

    def item_width(self, item: QWidget) -> int:
        if item.isVisible():
            width = item.width()
            if width > 0:
                return width
        size_hint = item.sizeHint().width()
        min_hint = item.minimumSizeHint().width()
        min_width = item.minimumWidth()
        min_hint = max(min_hint, min_width)
        policy = item.sizePolicy().horizontalPolicy()
        if policy == QSizePolicy.Policy.Fixed:
            return max(size_hint, min_hint, 0)
        if min_hint > 0:
            return min_hint
        return max(size_hint, 0)

    def rebuild_overflow_layout(self, overflow_items: list[QWidget]) -> None:
        owner = self._owner
        if (
            owner._overflow_layout is None
            or owner._overflow_container is None
            or owner._content_container is None
        ):
            return
        content_container = owner._content_container
        overflow_container = owner._overflow_container
        content_updates = content_container.updatesEnabled()
        overflow_updates = overflow_container.updatesEnabled()
        content_container.setUpdatesEnabled(False)
        overflow_container.setUpdatesEnabled(False)
        try:
            while owner._overflow_layout.count():
                item = owner._overflow_layout.takeAt(0)
                widget = item.widget()
                if widget is None:
                    continue
                widget.hide()
                if widget.parent() is not content_container:
                    widget.setParent(content_container)
            for widget in overflow_items:
                owner._content_layout.removeWidget(widget)
                if widget.parent() is not overflow_container:
                    widget.hide()
                    widget.setParent(overflow_container)
                owner._overflow_layout.addWidget(widget)
                widget.show()
            owner._overflowed_items = set(overflow_items)
            owner._overflow_container.setVisible(bool(overflow_items))
            self.sync_menu_size(overflow_items)
        finally:
            content_container.setUpdatesEnabled(content_updates)
            overflow_container.setUpdatesEnabled(overflow_updates)
            content_container.update()
            overflow_container.update()

    def rebuild_bar_layout(self, bar_items: list[QWidget]) -> None:
        owner = self._owner
        if owner._content_container is None:
            return
        content_container = owner._content_container
        updates_enabled = content_container.updatesEnabled()
        content_container.setUpdatesEnabled(False)
        try:
            for item in owner._bar_items:
                owner._content_layout.removeWidget(item)
            insert_index = max(0, owner._content_layout.count() - 1)
            for item in bar_items:
                if item.parent() is not content_container:
                    item.hide()
                    item.setParent(content_container)
                owner._content_layout.insertWidget(insert_index, item)
                if item is owner.placeholder and not owner.placeholder.isVisible():
                    item.hide()
                else:
                    item.show()
                insert_index += 1
        finally:
            content_container.setUpdatesEnabled(updates_enabled)
            content_container.update()

    def sync_menu_size(self, overflow_items: list[QWidget]) -> None:
        owner = self._owner
        if owner._overflow_menu is None or owner._overflow_container is None:
            return
        if not overflow_items:
            owner._overflow_container.setMinimumSize(QSize(0, 0))
            owner._overflow_menu.setMinimumSize(QSize(0, 0))
            return
        if owner._overflow_layout is not None:
            owner._overflow_layout.activate()
        owner._overflow_container.adjustSize()
        size_hint = owner._overflow_container.sizeHint()
        min_hint = owner._overflow_container.minimumSizeHint()
        target = size_hint.expandedTo(min_hint)
        if target.width() <= 0 or target.height() <= 0:
            return
        owner._overflow_container.setMinimumSize(target)
        owner._overflow_menu.setMinimumSize(target)
        owner._overflow_menu.adjustSize()
