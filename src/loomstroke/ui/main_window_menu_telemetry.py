"""Menu visibility telemetry coordination for MainWindow."""

from __future__ import annotations

import json
import logging
from contextlib import suppress
from datetime import datetime
from typing import Any

from ..core.events import Events, MenuVisibilityEvent
from .dialogs.menu_telemetry_dialog import MenuTelemetryRecord

logger = logging.getLogger("loomstroke.ui.main_window")
menu_logger = logging.getLogger("loomstroke.telemetry.menu")


class MainWindowMenuTelemetryCoordinator:
    """Coordinate menu visibility events, in-memory telemetry, and JSONL writes."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def emit_menu_visibility_changed(
        self,
        menu_id: str,
        enabled: bool,
        source: str | None,
    ) -> None:
        window = self._window
        legacy_payload = {"menu_id": menu_id, "enabled": bool(enabled)}
        event_payload = MenuVisibilityEvent(menu_id=menu_id, enabled=bool(enabled), source=source)
        self.record_menu_visibility(menu_id, enabled, source)
        with suppress(Exception):
            window._event_bus.emit("ui.menu_visibility_changed", legacy_payload)
            window._event_bus.emit(Events.MENU_VISIBILITY_CHANGED, event_payload)
        host_api = getattr(window, "_host_api", None)
        host_events = getattr(host_api, "events", None) if host_api else None
        if host_events and host_events is not window._event_bus:
            emitter = getattr(host_events, "emit", None) or getattr(host_events, "publish", None)
            if callable(emitter):
                with suppress(Exception):
                    emitter("ui.menu_visibility_changed", legacy_payload)
                    emitter(Events.MENU_VISIBILITY_CHANGED, event_payload)

    def record_menu_visibility(self, menu_id: str, enabled: bool, source: str | None) -> None:
        window = self._window
        source_label = source or "system"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        record_tuple = (timestamp, menu_id, bool(enabled), source_label)
        window._menu_visibility_log.append(record_tuple)
        record_obj = MenuTelemetryRecord(
            timestamp=timestamp,
            menu_id=menu_id,
            enabled=bool(enabled),
            source=source_label,
        )
        if window._menu_telemetry_dialog is not None:
            window._menu_telemetry_dialog.append_record(record_obj)
        try:
            window._menu_telemetry_path.parent.mkdir(parents=True, exist_ok=True)
            with window._menu_telemetry_path.open("a", encoding="utf-8") as handle:
                json_record = {
                    "timestamp": timestamp,
                    "menu_id": menu_id,
                    "enabled": bool(enabled),
                    "source": source_label,
                }
                handle.write(json.dumps(json_record, ensure_ascii=False) + "\n")
        except Exception:  # pragma: no cover - telemetry writes best-effort
            logger.debug("Failed to append menu telemetry", exc_info=True)
        if not menu_logger.isEnabledFor(logging.INFO):
            return
        menu_logger.info(
            "menu_visibility menu_id=%s enabled=%s source=%s",
            menu_id,
            bool(enabled),
            source_label,
        )

    def records_snapshot(self) -> list[MenuTelemetryRecord]:
        return [
            MenuTelemetryRecord(
                timestamp=entry[0],
                menu_id=entry[1],
                enabled=entry[2],
                source=entry[3],
            )
            for entry in self._window._menu_visibility_log
        ]

    def load_from_disk(self) -> list[MenuTelemetryRecord]:
        path = self._window._menu_telemetry_path
        if not path.exists():
            return self.records_snapshot()
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
        except (OSError, json.JSONDecodeError):
            logger.debug("Failed to load menu telemetry log", exc_info=True)
            return self.records_snapshot()
        return records[-64:]

    def on_dialog_closed(self, _: int) -> None:
        self._window._menu_telemetry_dialog = None
