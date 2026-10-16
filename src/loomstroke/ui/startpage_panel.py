"""StartPage panel widget."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.event_bus_extensions import get_extended_event_bus
from ..core.events import Events
from .theme import image_asset_path, text_logo_filename_for_theme
from .translations import TranslationBinder, translated_literal_domain

logger = logging.getLogger(__name__)


class StartPagePanel(QWidget):
    """StartPage shown when no document is loaded."""

    def __init__(self, host: Any | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host = host
        self._event_bus = getattr(host, "events", None) or get_extended_event_bus()
        self._translation_binder = TranslationBinder(self.tr)
        self._event_handlers: list[Any] = []
        self._event_tokens: list[Any] = []
        self._recent_files: list[str] = []
        self._text_logo_label: QLabel | None = None
        self._text_logo_height = 0
        self._build_ui()
        self._subscribe_language_events()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        content_widget = QWidget(self)
        self._build_content(content_widget)
        self._compose_layout(layout, content_widget)
        self._update_recent_visibility()

    def _compose_layout(self, layout: QVBoxLayout, content_widget: QWidget) -> None:
        row_layout = QHBoxLayout()
        row_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        row_layout.addStretch(1)
        row_layout.addWidget(content_widget)
        row_layout.addStretch(1)
        layout.addStretch(1)
        layout.addLayout(row_layout)
        layout.addStretch(1)

    def _build_content(self, container: QWidget) -> None:
        content_layout = QVBoxLayout(container)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._build_title(content_layout)
        self._build_action_buttons(content_layout)
        self._build_recent_section(content_layout)

    def _build_title(self, content_layout: QVBoxLayout) -> None:
        logo_row = self._build_logo_row(logo_height=72, text_height=52)

        subtitle = QLabel()
        subtitle.setObjectName("startpage_subtitle")
        subtitle.setStyleSheet("color: #666;")
        subtitle_text = self._ts("Start a new document or open an existing file.")
        self._translation_binder.register(subtitle.setText, subtitle_text)

        content_layout.addWidget(logo_row)
        content_layout.addWidget(subtitle)

    def _build_logo_row(self, *, logo_height: int, text_height: int) -> QWidget:
        row = QWidget(self)
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        added = False
        text_logo = self._text_logo_filename()
        for filename, height in (("logo.png", logo_height), (text_logo, text_height)):
            pixmap = QPixmap(str(image_asset_path(filename)))
            if pixmap.isNull():
                continue
            label = QLabel(row)
            label.setObjectName(f"startpage_{Path(filename).stem}")
            label.setPixmap(
                pixmap.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)
            )
            layout.addWidget(label)
            added = True
            if filename == text_logo:
                self._text_logo_label = label
                self._text_logo_height = height
        if not added:
            from PySide6.QtGui import QFont  # noqa: PLC0415

            title = QLabel(row)
            title.setObjectName("startpage_title")
            title_font = QFont("Segoe UI", 16)
            title_font.setWeight(QFont.Weight.Black)
            title.setFont(title_font)
            title_text = self._ts("LoomStroke")
            self._translation_binder.register(title.setText, title_text)
            layout.addWidget(title)
        layout.addStretch(1)
        return row

    def _text_logo_filename(self) -> str:
        theme = self._theme_setting()

        return text_logo_filename_for_theme(theme)

    def _theme_setting(self) -> str:
        settings = getattr(self._host, "settings", None)
        getter = getattr(settings, "get", None)
        if not callable(getter):
            return "dark"
        try:
            value = getter("ui/theme", "dark")
        except Exception:
            logger.debug("StartPage theme setting read failed", exc_info=True)
            return "dark"
        return value if isinstance(value, str) else "dark"

    def refresh_theme_assets(self) -> None:
        if self._text_logo_label is None or self._text_logo_height <= 0:
            return
        filename = self._text_logo_filename()
        pixmap = QPixmap(str(image_asset_path(filename)))
        if pixmap.isNull():
            return
        self._text_logo_label.setObjectName(f"startpage_{Path(filename).stem}")
        self._text_logo_label.setPixmap(
            pixmap.scaledToHeight(
                self._text_logo_height,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _build_action_buttons(self, content_layout: QVBoxLayout) -> None:
        self._new_button = QPushButton()
        new_text = self._ts("New Document...")
        self._translation_binder.register(self._new_button.setText, new_text)
        self._open_button = QPushButton()
        open_text = self._ts("Open...")
        self._translation_binder.register(self._open_button.setText, open_text)

        self._new_button.clicked.connect(self._request_new_document)
        self._open_button.clicked.connect(self._request_open_document)
        content_layout.addSpacing(12)
        content_layout.addWidget(self._new_button)
        content_layout.addWidget(self._open_button)

    def _build_recent_section(self, content_layout: QVBoxLayout) -> None:
        recent_label = QLabel()
        recent_label.setStyleSheet("margin-top: 12px; font-weight: 600;")
        recent_text = self._ts("Recent")
        self._translation_binder.register(recent_label.setText, recent_text)

        self._recent_list = QListWidget()
        self._recent_list.itemDoubleClicked.connect(self._open_recent_item)
        self._recent_list.setMinimumHeight(140)

        self._recent_empty = QLabel()
        self._recent_empty.setStyleSheet("color: #888;")
        empty_text = self._ts("No recent files")
        self._translation_binder.register(self._recent_empty.setText, empty_text)

        content_layout.addSpacing(16)
        content_layout.addWidget(recent_label)
        content_layout.addWidget(self._recent_list)
        content_layout.addWidget(self._recent_empty)
        content_layout.addStretch(1)

    def _subscribe_language_events(self) -> None:
        handler = self._on_language_changed
        self._event_handlers.append(handler)
        subscribe = getattr(self._event_bus, "on", None) or getattr(
            self._event_bus, "subscribe", None
        )
        if callable(subscribe):
            token = subscribe(Events.LANGUAGE_CHANGED, handler)
            self._event_tokens.append(token)

    def _on_language_changed(self, _payload: Any) -> None:
        self.refresh_translations()

    def set_recent_files(self, files: list[str]) -> None:
        self._recent_files = [str(path) for path in files if path]
        self._recent_list.clear()
        for path in self._recent_files:
            label = Path(path).name or path
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, path)
            self._recent_list.addItem(item)
        self._update_recent_visibility()

    def refresh_recent_files(self) -> None:
        self.set_recent_files(self._recent_files)

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self.tr)
        self._translation_binder.retranslate()

    def _ts(self, text: str) -> str:
        return translated_literal_domain("StartPagePanel", text, use_fallback=True)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def _update_recent_visibility(self) -> None:
        has_recent = bool(self._recent_files)
        self._recent_list.setVisible(has_recent)
        self._recent_empty.setVisible(not has_recent)

    def _emit_event(self, event: str, payload: dict[str, Any]) -> None:
        emitter = getattr(self._event_bus, "emit", None) or getattr(
            self._event_bus, "publish", None
        )
        if callable(emitter):
            emitter(event, payload)
        else:
            logger.debug("Event bus missing emit/publish for %s", event)

    def _request_new_document(self) -> None:
        self._emit_event("file.new", {})

    def _request_open_document(self) -> None:
        self._emit_event("file.open", {})

    def _open_recent_item(self, item: QListWidgetItem) -> None:
        path = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(path, str) and path:
            self._emit_event("file.open", {"path": path})


__all__ = ["StartPagePanel"]
