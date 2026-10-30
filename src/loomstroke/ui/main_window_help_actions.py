"""Help menu action coordination for MainWindow."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.widget_translations import bind_widget_text

from .. import __version__
from .dialogs.menu_telemetry_dialog import MenuTelemetryDialog
from .theme import image_asset_path, text_logo_filename_for_theme


class MainWindowHelpActionCoordinator:
    """Coordinate Help menu side effects and About dialog construction."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def open_docs(self) -> None:
        self._window._event_bus.emit("help.docs", {})

    def open_log_folder(self) -> None:
        self._window._event_bus.emit("help.logs", {})

    def show_menu_visibility_log(self) -> None:
        window = self._window
        records = window._menu_telemetry_records_snapshot()
        dialog = MenuTelemetryDialog(
            records,
            log_path=window._menu_telemetry_path,
            loader=window._load_menu_telemetry_from_disk,
            parent=cast("QDialog", window),
        )
        window._menu_telemetry_dialog = dialog
        dialog.finished.connect(window._on_menu_telemetry_dialog_closed)
        dialog.exec()

    def check_updates(self) -> None:
        self._window._event_bus.emit("app.check_updates", {})

    def about(self) -> None:
        dlg = self._create_about_dialog()
        layout = QVBoxLayout(dlg)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        project_root = Path(__file__).resolve().parents[3]
        self._add_logo_row(layout, dlg)
        self._add_about_info(layout)
        license_text, license_files = self._add_license_area(layout, project_root)
        third_party_text, third_party_file = self._add_third_party_area(layout, project_root)
        self._add_button_row(
            layout,
            dlg,
            license_text=license_text,
            license_files=license_files,
            third_party_text=third_party_text,
            third_party_file=third_party_file,
        )

        dlg.adjustSize()
        dlg.exec()

    def _create_about_dialog(self) -> QDialog:
        dlg = QDialog(self._window)
        bind_widget_text(
            dlg,
            "About LoomStroke",
            domain="MainWindowHelpActionCoordinator",
            attribute="windowTitle",
        )
        favicon_path = image_asset_path("favicon.png")
        if favicon_path.exists():
            dlg.setWindowIcon(QIcon(str(favicon_path)))
        dlg.setMinimumSize(400, 0)
        return dlg

    def _add_logo_row(self, layout: QVBoxLayout, dlg: QDialog) -> None:
        logo_row = QWidget(dlg)
        logo_layout = QHBoxLayout(logo_row)
        logo_layout.setContentsMargins(0, 0, 0, 0)
        logo_layout.setSpacing(8)
        logo_added = False
        for filename, height in (("logo.png", 48), (self.text_logo_filename(), 36)):
            pixmap = QPixmap(str(image_asset_path(filename)))
            if pixmap.isNull():
                continue
            logo_label = QLabel(logo_row)
            logo_label.setPixmap(
                pixmap.scaledToHeight(height, Qt.TransformationMode.SmoothTransformation)
            )
            logo_layout.addWidget(logo_label)
            logo_added = True
        if not logo_added:
            title = QLabel("LoomStroke", logo_row)
            title.setStyleSheet("font-family: Segoe UI Black; font-size: 20px;")
            logo_layout.addWidget(title)
        logo_layout.addStretch(1)
        layout.addWidget(logo_row)

    def _add_about_info(self, layout: QVBoxLayout) -> None:
        info = bind_widget_text(
            QLabel(),
            "<p style='margin:2px 0'>Version {version}</p>"
            "<p style='margin:2px 0'>Plugin-first paint application with AI integration.</p>"
            "<p style='margin:2px 0'>Copyright NakamuraShippo. All rights reserved.</p>"
            "<p style='margin:2px 0'>Contact: "
            '<a href="mailto:nakamurashippo@gmail.com">'
            "nakamurashippo@gmail.com</a></p>",
            domain="MainWindowHelpActionCoordinator",
            version=__version__,
        )
        info.setTextFormat(Qt.TextFormat.RichText)
        info.setOpenExternalLinks(True)
        info.setWordWrap(True)
        layout.addWidget(info)

    @staticmethod
    def _add_license_area(
        layout: QVBoxLayout,
        project_root: Path,
    ) -> tuple[QTextEdit, dict[str, Path]]:
        # License toggle area
        license_text = QTextEdit()
        license_text.setReadOnly(True)
        license_text.setVisible(False)
        license_text.setMinimumHeight(200)
        layout.addWidget(license_text)

        # License file paths
        _license_files = {
            "English": project_root / "LICENSE",
            "Japanese": project_root / "LICENSE_JA",
        }
        return license_text, _license_files

    @staticmethod
    def _add_third_party_area(
        layout: QVBoxLayout,
        project_root: Path,
    ) -> tuple[QTextEdit, Path]:
        # Third-party license area (separate from app license)
        tp_text = QTextEdit()
        tp_text.setReadOnly(True)
        tp_text.setVisible(False)
        tp_text.setMinimumHeight(200)
        layout.addWidget(tp_text)
        return tp_text, project_root / "THIRD_PARTY_LICENSES"

    def _add_button_row(
        self,
        layout: QVBoxLayout,
        dlg: QDialog,
        *,
        license_text: QTextEdit,
        license_files: dict[str, Path],
        third_party_text: QTextEdit,
        third_party_file: Path,
    ) -> None:
        # Bottom button row: [License] [EN/JA] [Third-Party] --- [Close]
        btn_row = QHBoxLayout()
        btn_row.setContentsMargins(0, 4, 0, 0)

        self._add_license_buttons(
            btn_row,
            dlg,
            license_text=license_text,
            license_files=license_files,
        )
        self._add_third_party_button(
            btn_row,
            dlg,
            third_party_text=third_party_text,
            third_party_file=third_party_file,
        )
        btn_row.addStretch()

        close_btn = bind_widget_text(
            QPushButton(), "Close", domain="MainWindowHelpActionCoordinator"
        )
        close_btn.clicked.connect(dlg.close)
        btn_row.addWidget(close_btn)

        layout.addLayout(btn_row)

    def _add_license_buttons(
        self,
        btn_row: QHBoxLayout,
        dlg: QDialog,
        *,
        license_text: QTextEdit,
        license_files: dict[str, Path],
    ) -> None:
        _license_visible = False
        _license_lang = "English"

        def _load_license(lang: str) -> None:
            nonlocal _license_lang
            _license_lang = lang
            path = license_files.get(lang)
            license_text.setPlainText(self._read_about_text(path, "License file not found."))

        def _toggle_license() -> None:
            nonlocal _license_visible
            if _license_visible:
                license_text.setVisible(False)
                lang_btn.setVisible(False)
                bind_widget_text(
                    license_btn,
                    "License",
                    domain="MainWindowHelpActionCoordinator",
                    attribute="text",
                )
                _license_visible = False
                dlg.adjustSize()
            else:
                _load_license(_license_lang)
                license_text.setVisible(True)
                lang_btn.setVisible(True)
                bind_widget_text(
                    license_btn,
                    "Hide License",
                    domain="MainWindowHelpActionCoordinator",
                    attribute="text",
                )
                _license_visible = True

        def _switch_lang() -> None:
            new_lang = "Japanese" if _license_lang == "English" else "English"
            _load_license(new_lang)
            lang_btn.setText("EN" if new_lang == "Japanese" else "JA")

        license_btn = bind_widget_text(
            QPushButton(), "License", domain="MainWindowHelpActionCoordinator"
        )
        license_btn.clicked.connect(_toggle_license)
        btn_row.addWidget(license_btn)

        lang_btn = QPushButton("JA")
        lang_btn.setFixedWidth(36)
        lang_btn.setVisible(False)
        lang_btn.clicked.connect(_switch_lang)
        btn_row.addWidget(lang_btn)

    def _add_third_party_button(
        self,
        btn_row: QHBoxLayout,
        dlg: QDialog,
        *,
        third_party_text: QTextEdit,
        third_party_file: Path,
    ) -> None:
        _tp_visible = False

        def _toggle_tp() -> None:
            nonlocal _tp_visible
            if _tp_visible:
                third_party_text.setVisible(False)
                bind_widget_text(
                    tp_btn,
                    "Third-Party Licenses",
                    domain="MainWindowHelpActionCoordinator",
                    attribute="text",
                )
                _tp_visible = False
                dlg.adjustSize()
            else:
                third_party_text.setPlainText(
                    self._read_about_text(third_party_file, "File not found.")
                )
                third_party_text.setVisible(True)
                bind_widget_text(
                    tp_btn,
                    "Hide Third-Party",
                    domain="MainWindowHelpActionCoordinator",
                    attribute="text",
                )
                _tp_visible = True

        tp_btn = bind_widget_text(
            QPushButton(), "Third-Party Licenses", domain="MainWindowHelpActionCoordinator"
        )
        tp_btn.clicked.connect(_toggle_tp)
        btn_row.addWidget(tp_btn)

    @staticmethod
    def _read_about_text(path: Path | None, fallback: str) -> str:
        if path and path.exists():
            try:
                return path.read_text(encoding="utf-8")
            except OSError:
                return fallback
        return fallback

    def text_logo_filename(self) -> str:
        theme = self._window._settings.get("ui/theme", "dark")
        return text_logo_filename_for_theme(theme if isinstance(theme, str) else "dark")
