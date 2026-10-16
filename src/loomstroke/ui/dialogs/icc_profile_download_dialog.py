"""User-initiated browser handoff to official ICC terms/information pages."""

from __future__ import annotations

import logging
from collections.abc import Callable
from html import escape

from PySide6.QtCore import QEvent, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QLabel,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..icc_profile_download_sources import ICC_PROFILE_DOWNLOAD_SOURCES
from ..translations import TranslationBinder, translate_with_fallback

logger = logging.getLogger(__name__)


class IccProfileDownloadDialog(QDialog):
    """Show review pages without downloading or modifying the ICC registry."""

    def __init__(
        self,
        parent: QWidget | None = None,
        *,
        url_opener: Callable[[QUrl], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self._url_opener = url_opener or QDesktopServices.openUrl
        self._allowed_urls = frozenset(source.url for source in ICC_PROFILE_DOWNLOAD_SOURCES)
        self._failed_url: str | None = None
        self._binder = TranslationBinder(self._translate)
        self._binder.register(self.setWindowTitle, "Representative ICC Profiles")

        layout = QVBoxLayout(self)
        for text in (
            "Open an official page in your browser, review its terms, and download a profile "
            "yourself. Then use Load ICC Profile... to select the downloaded CMYK ICC file.",
            "Choose the printing condition specified by your printer; region alone is not enough.",
        ):
            label = QLabel(self)
            label.setWordWrap(True)
            self._binder.register(label.setText, text)
            layout.addWidget(label)

        self._tree = QTreeWidget(self)
        self._tree.setRootIsDecorated(False)
        self._tree.setColumnCount(3)
        self._tree.setSelectionMode(QTreeWidget.SelectionMode.NoSelection)
        self._tree.header().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self._tree.header().setStretchLastSection(True)
        self._page_labels: list[QLabel] = []
        for source in ICC_PROFILE_DOWNLOAD_SOURCES:
            item = QTreeWidgetItem(self._tree)
            item.setData(0, Qt.ItemDataRole.UserRole, source.source_id)
            item.setText(1, source.profile_name)
            label = QLabel(self._tree)
            label.setOpenExternalLinks(False)
            label.setTextInteractionFlags(
                Qt.TextInteractionFlag.LinksAccessibleByMouse
                | Qt.TextInteractionFlag.LinksAccessibleByKeyboard
            )
            label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            label.setToolTip(source.url)
            label.linkActivated.connect(self._open_page)
            self._page_labels.append(label)
            self._tree.setItemWidget(item, 2, label)
        layout.addWidget(self._tree, 1)

        self._status = QLabel(self)
        self._status.setWordWrap(True)
        self._status.setTextFormat(Qt.TextFormat.PlainText)
        self._status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._status.hide()
        layout.addWidget(self._status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        self._binder.register(
            buttons.button(QDialogButtonBox.StandardButton.Close).setText, "Close"
        )
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(870, 470)
        self.refresh_translations()

    def _open_page(self, url: str) -> None:
        if url not in self._allowed_urls:
            logger.warning("Rejected an unlisted ICC provider page")
            return
        try:
            opened = self._url_opener(QUrl(url))
        except (OSError, RuntimeError):
            logger.warning("Could not open the ICC provider page", exc_info=True)
            opened = False
        self._failed_url = None if opened else url
        self._refresh_status()

    def _refresh_status(self) -> None:
        if self._failed_url is None:
            self._status.clear()
            self._status.hide()
            return
        self._status.setText(
            self._translate(
                "Could not open the browser. Copy the page address and open it in your browser."
            )
            + "\n"
            + self._failed_url
        )
        self._status.show()

    def refresh_translations(self) -> None:
        self._binder.retranslate()
        self._tree.setHeaderLabels(
            [self._translate(text) for text in ("Region", "Profile", "Official page")]
        )
        for row, source in enumerate(ICC_PROFILE_DOWNLOAD_SOURCES):
            self._tree.topLevelItem(row).setText(0, self._translate(source.region_source))
            label = self._page_labels[row]
            link_text = self._translate("Review terms / download page")
            label.setText(
                f'{escape(source.provider)}<br><a href="{escape(source.url, quote=True)}">'
                f"{escape(link_text)}</a>"
            )
            label.setAccessibleName(f"{source.profile_name}: {link_text}")
        self._refresh_status()

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "_status"):
            self.refresh_translations()
        super().changeEvent(event)

    @staticmethod
    def _translate(text: str) -> str:
        return translate_with_fallback("IccProfileDownloads", text)
