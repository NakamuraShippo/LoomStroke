from __future__ import annotations

import csv
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHeaderView,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..translations import TranslationBinder, translate_with_fallback, translated_literal


@dataclass(frozen=True)
class MenuTelemetryRecord:
    timestamp: str
    menu_id: str
    enabled: bool
    source: str


class MenuTelemetryDialog(QDialog):
    """Simple viewer for MenuVisibilityEvent history."""

    def __init__(
        self,
        records: Sequence[MenuTelemetryRecord],
        *,
        log_path: Path | None = None,
        loader: Callable[[], Sequence[MenuTelemetryRecord]] | None = None,
        parent: QDialog | None = None,
    ) -> None:
        super().__init__(parent)
        self._translation_binder = TranslationBinder(self._translate_runtime)
        layout = QVBoxLayout(self)

        self.setWindowTitle(self._ts("Menu Visibility Log"))

        self._log_path = log_path
        self._loader = loader
        self._records: list[MenuTelemetryRecord] = []

        self._table = QTableWidget(0, 4, self)
        self._table.setHorizontalHeaderLabels(["Time", "Menu", "Enabled", "Source"])
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self._table.verticalHeader().setVisible(False)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self._table)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, parent=self)
        self._close_button = buttons.button(QDialogButtonBox.StandardButton.Close)
        if self._close_button is not None:
            self._translation_binder.register(self._close_button.setText, self._ts("Close"))
        refresh_btn = QPushButton(self._ts("Refresh Logs"), self)
        self._refresh_button = refresh_btn
        buttons.addButton(refresh_btn, QDialogButtonBox.ButtonRole.ActionRole)
        refresh_btn.clicked.connect(self.refresh_from_source)
        export_btn = QPushButton(self._ts("Export CSV"), self)
        self._export_button = export_btn
        buttons.addButton(export_btn, QDialogButtonBox.ButtonRole.ActionRole)
        export_btn.clicked.connect(self.export_csv)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        layout.addWidget(buttons)

        self.set_records(records)
        self._translation_binder.register(self.setWindowTitle, self._ts("Menu Visibility Log"))
        self.refresh_translations()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("MenuTelemetryDialog", text)

    def _set_headers(self) -> None:
        self._table.setHorizontalHeaderLabels(
            [
                self._ts("Time"),
                self._ts("Menu"),
                self._ts("Enabled"),
                self._ts("Source"),
            ]
        )

    def refresh_translations(self) -> None:
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        self._set_headers()
        self._refresh_button.setText(self._ts("Refresh Logs"))
        self._export_button.setText(self._ts("Export CSV"))

    def set_records(self, records: Sequence[MenuTelemetryRecord]) -> None:
        self._records = list(records)
        self._table.setRowCount(len(self._records))
        for row, record in enumerate(self._records):
            self._write_row(row, record)
        self._table.scrollToBottom()

    def append_record(self, record: MenuTelemetryRecord) -> None:
        self._records.append(record)
        row = self._table.rowCount()
        self._table.insertRow(row)
        self._write_row(row, record)
        self._table.scrollToBottom()

    def refresh_from_source(self) -> None:
        records: Sequence[MenuTelemetryRecord] | None = None
        if self._loader:
            records = self._loader()
        elif self._log_path:
            records = self._load_from_path(self._log_path)
        if records is None:
            return
        self.set_records(records)

    def export_csv(self) -> None:
        if not self._records:
            QMessageBox.information(
                self,
                self._ts("Menu Telemetry"),
                self._ts("No menu visibility records to export."),
            )
            return
        target = self._choose_export_path()
        if target is None:
            return
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("w", newline="", encoding="utf-8") as handle:
                writer = csv.writer(handle)
                writer.writerow(["timestamp", "menu_id", "enabled", "source"])
                for record in self._records:
                    writer.writerow(
                        [
                            record.timestamp,
                            record.menu_id,
                            str(record.enabled).lower(),
                            record.source,
                        ]
                    )
        except Exception as exc:  # pragma: no cover - filesystem failure is rare
            QMessageBox.warning(
                self,
                self._ts("Menu Telemetry"),
                self._ts("Failed to export CSV: {error}").format(error=exc),
            )
            return
        message = self._ts("Exported {count} records to {path}").format(
            count=len(self._records),
            path=target,
        )
        QMessageBox.information(self, self._ts("Menu Telemetry"), message)

    def _choose_export_path(self) -> Path | None:
        default = str(
            (self._log_path or Path("telemetry") / "menu_visibility.jsonl").with_suffix(".csv")
        )
        selected, _ = QFileDialog.getSaveFileName(
            self,
            self._ts("Export Menu Visibility CSV"),
            default,
            self._ts("CSV Files (*.csv)"),
        )
        if not selected:
            return None
        return Path(selected)

    def _write_row(self, row: int, record: MenuTelemetryRecord) -> None:
        self._table.setItem(row, 0, QTableWidgetItem(record.timestamp))
        self._table.setItem(row, 1, QTableWidgetItem(record.menu_id))
        enabled_item = QTableWidgetItem("On" if record.enabled else "Off")
        enabled_item.setText(self._ts("On") if record.enabled else self._ts("Off"))
        enabled_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(row, 2, enabled_item)
        self._table.setItem(row, 3, QTableWidgetItem(record.source))

    @staticmethod
    def _load_from_path(path: Path) -> Sequence[MenuTelemetryRecord]:
        if not path.exists():
            return []
        records: list[MenuTelemetryRecord] = []
        try:
            for raw_line in path.read_text(encoding="utf-8").splitlines():
                line = raw_line.strip()
                if not line:
                    continue
                data = json.loads(line)
                records.append(
                    MenuTelemetryRecord(
                        timestamp=str(data.get("timestamp", "")),
                        menu_id=str(data.get("menu_id", "unknown")),
                        enabled=bool(data.get("enabled", False)),
                        source=str(data.get("source", "system")),
                    )
                )
        except json.JSONDecodeError:
            return []
        return records[-64:]
