"""Comic Assets panel — a tab per asset category, drag items onto the canvas.

Today: "フキダシ" (balloons). Adding a category = add it to
``comic_assets.CATEGORY_LABELS`` (a tab appears automatically; the store is
category-agnostic). Items are dragged with ``ASSET_MIME`` + a thumbnail;
the canvas emits ``ui.canvas.drop`` and the comic tool places the asset at
the drop point (``ComicToolsPlugin.place_asset``).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from PySide6.QtCore import QByteArray, QPoint, QSize, Qt
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

from .comic_assets import ASSET_MIME, CATEGORY_BALLOON, CATEGORY_LABELS, ComicAsset, drop_payload

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
    def _build_tabs(self) -> None:
        categories = list(CATEGORY_LABELS)
        try:
            for category in self._tool.asset_store.categories():
                if category not in categories:
                    categories.append(category)
        except Exception:
            logger.debug("asset categories listing failed", exc_info=True)
        for category in categories:
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
            register_btn = QPushButton("選択中のフキダシを登録")
            register_btn.setToolTip("Comic パネルで選択中のフキダシをこのタブの素材として保存")
            register_btn.clicked.connect(self._register_selected_balloon)
            buttons.addWidget(register_btn)
        rename_btn = QPushButton("名前変更")
        rename_btn.clicked.connect(lambda _c=False, cat=category: self._rename_current(cat))
        delete_btn = QPushButton("削除")
        delete_btn.clicked.connect(lambda _c=False, cat=category: self._delete_current(cat))
        reload_btn = QPushButton("再読込")
        reload_btn.clicked.connect(self.refresh)
        buttons.addWidget(rename_btn)
        buttons.addWidget(delete_btn)
        buttons.addStretch(1)
        buttons.addWidget(reload_btn)
        page_layout.addLayout(buttons)
        self.lists[category] = listing
        self.tabs.addTab(page, CATEGORY_LABELS.get(category, category))

    # ── data ──────────────────────────────────────────────────────────
    def refresh(self) -> None:
        for category, listing in self.lists.items():
            current = listing.currentItem()
            current_id = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
            listing.clear()
            try:
                assets = self._tool.asset_store.list(category)
            except Exception:
                logger.debug("asset listing failed", exc_info=True)
                assets = []
            for asset in assets:
                item = QListWidgetItem(asset.name)
                item.setData(Qt.ItemDataRole.UserRole, asset.asset_id)
                item.setToolTip(f"{asset.name}\n{asset.asset_id}\nドラッグでキャンバスへ配置")
                icon = _icon_from_png(asset.thumbnail_png)
                if icon is not None:
                    item.setIcon(icon)
                listing.addItem(item)
                if current_id is not None and asset.asset_id == current_id:
                    listing.setCurrentItem(item)

    def _current(self, category: str) -> tuple[AssetListWidget, str | None]:
        listing = self.lists[category]
        item = listing.currentItem()
        asset_id = item.data(Qt.ItemDataRole.UserRole) if item is not None else None
        return listing, (asset_id if isinstance(asset_id, str) else None)

    # ── actions ───────────────────────────────────────────────────────
    def _register_selected_balloon(self) -> None:
        asset: ComicAsset | None = self._tool.register_selected_balloon_as_asset()
        if asset is None:
            self.status_label.setText("登録できません: Comic パネルでフキダシを選択してください")
            return
        self.refresh()
        self.status_label.setText(f"登録しました: {asset.name}")
        self.tabs.setCurrentIndex(list(self.lists).index(asset.category))

    def _rename_current(self, category: str) -> None:
        listing, asset_id = self._current(category)
        if asset_id is None:
            return
        current_name = listing.currentItem().text()
        name, ok = QInputDialog.getText(self, "名前変更", "素材名:", text=current_name)
        if not ok or not name.strip():
            return
        self._tool.asset_store.rename(category, asset_id, name.strip())
        self.refresh()

    def _delete_current(self, category: str) -> None:
        listing, asset_id = self._current(category)
        if asset_id is None:
            return
        name = listing.currentItem().text()
        answer = QMessageBox.question(self, "素材の削除", f"「{name}」を削除しますか?")
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._tool.asset_store.delete(category, asset_id)
        self.refresh()

    def _place_at_center(self, item: QListWidgetItem) -> None:
        listing = item.listWidget()
        category = getattr(listing, "category", CATEGORY_BALLOON)
        asset_id = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(asset_id, str):
            return
        balloon_id = self._tool.place_asset(category, asset_id, None)
        self.status_label.setText(
            "ページ中央に配置しました" if balloon_id else "配置できませんでした (キャンバスなし)"
        )


def _icon_from_png(data: bytes | None) -> QIcon | None:
    if not data:
        return None
    pixmap = QPixmap()
    if not pixmap.loadFromData(data, "PNG"):
        return None
    return QIcon(pixmap)


__all__ = ["AssetListWidget", "ComicAssetsPanelWidget"]
