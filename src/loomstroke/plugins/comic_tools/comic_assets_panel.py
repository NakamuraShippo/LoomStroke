"""Comic Assets panel — a tab per asset category, drag items onto the canvas.

Today: "フキダシ" (balloons). Adding a category = add it to
``comic_assets.CATEGORY_LABELS`` (a tab appears automatically; the store is
category-agnostic). Items are dragged with ``ASSET_MIME`` + a thumbnail;
the canvas emits ``ui.canvas.drop`` and the comic tool places the asset at
the drop point (``ComicToolsPlugin.place_asset``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QByteArray, QEvent, QPoint, QSize, Qt
from PySide6.QtGui import QDrag, QIcon, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.comic_assets_feedback_strings import LOAD_FAILED
from loomstroke.ui.translations import (
    TranslationBinder,
    translate_with_fallback,
    translated_literal_domain,
)

from .comic_assets import ASSET_MIME, CATEGORY_BALLOON, CATEGORY_LABELS, ComicAsset, drop_payload
from .comic_assets_listing import read_assets_for_panel, unavailable_listing

if TYPE_CHECKING:
    from .tool import ComicToolsPlugin

logger = logging.getLogger(__name__)


class AssetListWidget(QListWidget):
    """Icon list whose items start a drag carrying the asset MIME."""

    def __init__(self, category: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.category = category
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(QSize(96, 96))
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setSpacing(8)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setDragEnabled(True)

    def startDrag(self, supportedActions: Qt.DropActions) -> None:  # type: ignore[override]  # noqa: N803 - Qt override
        del supportedActions
        item = self.currentItem()
        if item is None:
            return
        asset_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(asset_id, str):
            return
        mime = self.mimeData([item])
        mime.setData(ASSET_MIME, QByteArray(drop_payload(self.category, asset_id).encode("utf-8")))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pixmap = item.icon().pixmap(64, 64)
        if not pixmap.isNull():
            drag.setPixmap(pixmap)
            drag.setHotSpot(QPoint(pixmap.width() // 2, pixmap.height() // 2))
        drag.exec(Qt.DropAction.CopyAction)


class ComicAssetsPanelWidget(QWidget):
    def __init__(self, tool: ComicToolsPlugin, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tool = tool
        self._translations = TranslationBinder(self._ts)
        self._status_source: str | None = None
        self._status_values: dict[str, str] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        self.lists: dict[str, AssetListWidget] = {}
        self._build_tabs()
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        self.refresh()

    # ── tabs ──────────────────────────────────────────────────────────
    def _resolve_asset_store(self) -> Any:
        store = self._tool.asset_store
        return self._tool._asset_store() if store is None else store

    def _build_tabs(self) -> None:
        for category in CATEGORY_LABELS:
            self._add_category_tab(category)

    def _add_category_tab(self, category: str) -> None:
        if category in self.lists:
            return
        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.setContentsMargins(0, 0, 0, 0)
        listing = AssetListWidget(category)
        listing.itemDoubleClicked.connect(self._place_at_center)
        page_layout.addWidget(listing, 1)
        buttons = QHBoxLayout()
        if category == CATEGORY_BALLOON:
            register_btn = QPushButton()
            self._translations.register(register_btn.setText, "Register Selected Balloon")
            self._translations.register(
                register_btn.setToolTip,
                "Save the balloon selected in the Comic panel as an asset in this tab.",
            )
            register_btn.clicked.connect(self._register_selected_balloon)
            buttons.addWidget(register_btn)
        rename_btn = QPushButton()
        self._translations.register(rename_btn.setText, "Rename")
        rename_btn.clicked.connect(lambda _c=False, cat=category: self._rename_current(cat))
        delete_btn = QPushButton()
        self._translations.register(delete_btn.setText, "Delete")
        delete_btn.clicked.connect(lambda _c=False, cat=category: self._delete_current(cat))
        reload_btn = QPushButton()
        self._translations.register(reload_btn.setText, "Reload")
        reload_btn.clicked.connect(self.refresh)
        buttons.addWidget(rename_btn)
        buttons.addWidget(delete_btn)
        buttons.addStretch(1)
        buttons.addWidget(reload_btn)
        page_layout.addLayout(buttons)
        self.lists[category] = listing
        self.tabs.addTab(page, self._category_label(category))

    def _ts(self, source: str) -> str:
        return translate_with_fallback("ComicAssetsPanel", source)

    def _category_label(self, category: str) -> str:
        return (
            self._ts("Balloons")
            if category == CATEGORY_BALLOON
            else CATEGORY_LABELS.get(category, category)
        )

    def _asset_tooltip(self, name: str, asset_id: str) -> str:
        return self._ts("{name}\n{asset_id}\nDrag onto the canvas to place").format(
            name=name, asset_id=asset_id
        )

    # ── data ──────────────────────────────────────────────────────────
    def refresh(self) -> None:
        previously_failed = getattr(self, "_asset_load_failed", False)
        try:
            result = read_assets_for_panel(self._resolve_asset_store())
        except Exception:
            logger.debug("Comic asset store could not be initialized")
            result = unavailable_listing()
        for category in result.categories:
            self._add_category_tab(category)
        failed = set(result.failed_categories)
        if result.root_failed:
            failed.update(self.lists)
        # Publish availability before clear() can dispatch an old item's signal.
        self._asset_failed_categories = frozenset(failed)
        self._asset_load_failed = result.root_failed or bool(failed)
        for category, listing in self.lists.items():
            current = listing.currentItem()
            current_id = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
            listing.clear()
            assets = () if category in failed else result.assets.get(category, ())
            for asset in assets:
                item = QListWidgetItem(asset.name)
                item.setData(Qt.ItemDataRole.UserRole, asset.asset_id)
                item.setToolTip(self._asset_tooltip(asset.name, asset.asset_id))
                icon = _icon_from_png(asset.thumbnail_png)
                if icon is not None:
                    item.setIcon(icon)
                listing.addItem(item)
                if current_id is not None and asset.asset_id == current_id:
                    listing.setCurrentItem(item)
        self._refresh_asset_load_notice(clear=previously_failed)

    def _refresh_asset_load_notice(self, *, clear: bool = False) -> None:
        label = getattr(self, "status_label", None)
        if label is None:  # Qt can deliver language events during construction.
            return
        if getattr(self, "_asset_load_failed", False):
            label.setText(
                translated_literal_domain("ComicAssetsPanel", LOAD_FAILED, use_fallback=True)
            )
        elif clear:
            label.setText("")
            self._status_source = None
            self._status_values = {}

    def _asset_category_available(self, category: str) -> bool:
        return category not in getattr(self, "_asset_failed_categories", ())

    def _set_asset_action_status(self, message: str, **values: str) -> None:
        self._status_source = message
        self._status_values = values
        if getattr(self, "_asset_load_failed", False):
            self._refresh_asset_load_notice()
        else:
            self.status_label.setText(self._ts(message).format(**values))

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            if hasattr(self, "status_label"):
                self._translations.retranslate()
                for index, (category, listing) in enumerate(self.lists.items()):
                    self.tabs.setTabText(index, self._category_label(category))
                    for row in range(listing.count()):
                        item = listing.item(row)
                        item.setToolTip(
                            self._asset_tooltip(item.text(), item.data(Qt.ItemDataRole.UserRole))
                        )
                if self._status_source is not None:
                    self._set_asset_action_status(self._status_source, **self._status_values)
            self._refresh_asset_load_notice()

    def _current(self, category: str) -> tuple[AssetListWidget, str | None]:
        listing = self.lists[category]
        item = listing.currentItem()
        asset_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return listing, (asset_id if isinstance(asset_id, str) else None)

    # ── actions ───────────────────────────────────────────────────────
    def _register_selected_balloon(self) -> None:
        asset: ComicAsset | None = self._tool.register_selected_balloon_as_asset()
        if asset is None:
            self._set_asset_action_status("Cannot register: select a balloon in the Comic panel.")
            return
        self.refresh()
        self._set_asset_action_status("Registered: {name}", name=asset.name)
        self.tabs.setCurrentIndex(list(self.lists).index(asset.category))

    def _rename_current(self, category: str) -> None:
        if not self._asset_category_available(category):
            return
        listing, asset_id = self._current(category)
        if asset_id is None:
            return
        current_name = listing.currentItem().text()
        name, ok = QInputDialog.getText(
            self, self._ts("Rename"), self._ts("Asset name:"), text=current_name
        )
        if not ok or not name.strip() or not self._asset_category_available(category):
            return
        self._tool.asset_store.rename(category, asset_id, name.strip())
        self.refresh()

    def _delete_current(self, category: str) -> None:
        if not self._asset_category_available(category):
            return
        listing, asset_id = self._current(category)
        if asset_id is None:
            return
        name = listing.currentItem().text()
        answer = QMessageBox.question(
            self, self._ts("Delete Asset"), self._ts('Delete "{name}"?').format(name=name)
        )
        if answer != QMessageBox.StandardButton.Yes or not self._asset_category_available(category):
            return
        self._tool.asset_store.delete(category, asset_id)
        self.refresh()

    def _place_at_center(self, item: QListWidgetItem) -> None:
        try:
            listing = item.listWidget()
            category = getattr(listing, "category", CATEGORY_BALLOON)
            asset_id = item.data(Qt.ItemDataRole.UserRole)
        except RuntimeError:  # A queued callback can outlive a cleared Qt item.
            return
        if not self._asset_category_available(category):
            return
        if not isinstance(asset_id, str):
            return
        balloon_id = self._tool.place_asset(category, asset_id, None)
        self._set_asset_action_status(
            "Placed at the page center." if balloon_id else "Could not place the asset (no canvas)."
        )


def _icon_from_png(data: bytes | None) -> QIcon | None:
    if not data:
        return None
    pixmap = QPixmap()
    if not pixmap.loadFromData(data, "PNG"):
        return None
    return QIcon(pixmap)


__all__ = ["AssetListWidget", "ComicAssetsPanelWidget"]
