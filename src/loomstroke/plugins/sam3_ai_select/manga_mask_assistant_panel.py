"""Panel bridge for SAM3 Manga Mask Assistant table actions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from ...ai_selection import (
    SAM3_MANGA_MASK_ASSISTANT_TABLE_COLUMNS,
    SAM3_MANGA_MASK_ASSISTANT_TABLE_MODEL_SCHEMA_VERSION,
    SAM3_MANGA_MASK_CATEGORY_VALUES,
    SAM3_MANGA_MASK_PRESET_VALUES,
    manga_mask_category_definitions,
)
from ...ui.translations import translated_literal_domain
from .manga_mask_assistant_table_widget import Sam3MangaMaskAssistantTableWidget

_TRANSLATION_DOMAIN = "SAM3MangaMaskAssistant"
_PANEL_ACTION_RESULT_SCHEMA_VERSION = "sam3.manga_mask_assistant_panel_action_result.v1"
_SCORECARD_SNAPSHOT_SCHEMA_VERSION = "sam3.manga_mask_assistant_scorecard_snapshot.v1"
_SCORECARD_TOOL_ID = "sam3_ai_select"
_PACKAGE_BUILD_ACTION_ID = "sam3_manga_mask_assistant.package_build"
_ACCEPTANCE_ACTION_ID = "sam3_manga_mask_assistant.candidate_acceptance"
_ASYNC_REQUIRED_BLOCKED_REASONS = frozenset(
    {
        "ai_select_manga_inpaint_submit_requires_async_executor",
    }
)


class _MangaMaskAssistantToolLike(Protocol):
    def get_manga_mask_assistant_table_model_snapshot(
        self,
        preset: str,
    ) -> dict[str, Any]: ...

    def execute_manga_mask_assistant_action(self, action_id: str) -> dict[str, Any]: ...


class Sam3MangaMaskAssistantPanel(QWidget):
    """Bind a Manga Mask Assistant table widget to a snapshot/action provider."""

    action_executed = Signal(dict)

    def __init__(
        self,
        tool: _MangaMaskAssistantToolLike | None = None,
        parent: QWidget | None = None,
        action_scheduler: Callable[[str, dict[str, object] | None], bool] | None = None,
    ) -> None:
        super().__init__(parent)
        self._tool: _MangaMaskAssistantToolLike | None = None
        self._action_scheduler = action_scheduler
        self._last_table_model: dict[str, Any] = _empty_table_model("all")
        self._last_action_result: dict[str, Any] | None = None
        self._last_build_readiness: dict[str, Any] | None = None
        self._last_scorecard_snapshot: dict[str, Any] | None = None
        self._status_label = QLabel(self)
        self._scorecard_label = QLabel(self)
        self._build_category_label = QLabel(self)
        self._build_category_combo = QComboBox(self)
        self._build_package_button = QToolButton(self)
        self._preset_label = QLabel(self)
        self._preset_combo = QComboBox(self)
        # CT-23: explicit panel bbox source + preview cancel.
        self._panel_source_label = QLabel(self)
        self._panel_source_combo = QComboBox(self)
        self._cancel_preview_button = QToolButton(self)
        self._table_widget = Sam3MangaMaskAssistantTableWidget(self)
        self._setup_ui()
        self._table_widget.action_requested.connect(self.execute_action)
        self._table_widget.acceptance_requested.connect(self.set_candidate_acceptance)
        self._build_package_button.clicked.connect(self.build_package_from_last_session)
        self._cancel_preview_button.clicked.connect(self.cancel_preview_submit)
        self.bind_tool(tool)

    @property
    def last_action_result(self) -> dict[str, Any] | None:
        return dict(self._last_action_result) if self._last_action_result is not None else None

    @property
    def last_build_readiness(self) -> dict[str, Any] | None:
        return dict(self._last_build_readiness) if self._last_build_readiness is not None else None

    @property
    def last_scorecard_snapshot(self) -> dict[str, Any] | None:
        if self._last_scorecard_snapshot is None:
            return None
        return dict(self._last_scorecard_snapshot)

    def bind_tool(self, tool: _MangaMaskAssistantToolLike | None) -> None:
        self._tool = tool
        self.refresh_from_tool()

    def set_action_scheduler(
        self,
        action_scheduler: Callable[[str, dict[str, object] | None], bool] | None,
    ) -> None:
        self._action_scheduler = action_scheduler

    def refresh_from_tool(self) -> dict[str, Any]:
        preset = self.selected_preset()
        tool = self._tool
        if tool is None:
            table_model = _empty_table_model(
                preset,
                warning_codes=["manga_mask_assistant_tool_missing"],
            )
            self._set_table_model(table_model, _ts("No Manga Mask Assistant source"))
            self._refresh_scorecard_summary(tool)
            self._refresh_build_controls(tool)
            return dict(table_model)

        snapshot_getter = getattr(tool, "get_manga_mask_assistant_table_model_snapshot", None)
        if not callable(snapshot_getter):
            table_model = _empty_table_model(
                preset,
                warning_codes=["manga_mask_assistant_table_model_missing"],
            )
            self._set_table_model(table_model, _ts("Manga Mask Assistant unavailable"))
            self._refresh_scorecard_summary(tool)
            self._refresh_build_controls(tool)
            return dict(table_model)

        try:
            raw_table_model = snapshot_getter(preset)
        except Exception as exc:  # noqa: BLE001 - UI bridge must not leak provider details.
            table_model = _empty_table_model(
                preset,
                warning_codes=["manga_mask_assistant_table_model_failed"],
            )
            self._set_table_model(table_model, f"{_ts('Refresh failed')}: {type(exc).__name__}")
            self._refresh_scorecard_summary(tool)
            self._refresh_build_controls(tool)
            return dict(table_model)

        if isinstance(raw_table_model, Mapping):
            table_model = dict(raw_table_model)
            self._set_table_model(table_model, _status_text_for_table_model(table_model))
            self._refresh_scorecard_summary(tool)
            self._refresh_build_controls(tool)
            return dict(table_model)

        table_model = _empty_table_model(
            preset,
            warning_codes=["manga_mask_assistant_table_model_invalid"],
        )
        self._set_table_model(table_model, _ts("Manga Mask Assistant unavailable"))
        self._refresh_scorecard_summary(tool)
        self._refresh_build_controls(tool)
        return dict(table_model)

    def build_package_from_last_session(self) -> dict[str, Any]:
        category = self.selected_build_category()
        tool = self._tool
        if category is None:
            result = _panel_action_result_payload(
                _PACKAGE_BUILD_ACTION_ID,
                status="blocked",
                handled=False,
                blocked_reason_codes=["manga_mask_assistant_category_required"],
            )
            self._finish_action(result)
            return result

        if tool is None:
            result = _panel_action_result_payload(
                _PACKAGE_BUILD_ACTION_ID,
                status="blocked",
                handled=False,
                blocked_reason_codes=["manga_mask_assistant_tool_missing"],
            )
            self._finish_action(result)
            return result

        builder = getattr(tool, "build_manga_mask_assistant_package_from_last_session", None)
        if not callable(builder):
            result = _panel_action_result_payload(
                _PACKAGE_BUILD_ACTION_ID,
                status="blocked",
                handled=False,
                blocked_reason_codes=["manga_mask_assistant_package_build_executor_missing"],
            )
            self._finish_action(result)
            return result

        try:
            panel_bboxes = self._selected_panel_bboxes()
            raw_result = (
                builder(category, panel_bboxes=panel_bboxes)
                if panel_bboxes is not None
                else builder(category)
            )
        except Exception as exc:  # noqa: BLE001 - keep UI result JSON-safe and scrubbed.
            result = _panel_action_result_payload(
                _PACKAGE_BUILD_ACTION_ID,
                status="failed",
                handled=False,
                error_type=type(exc).__name__,
            )
            self._finish_action(result)
            return result

        if isinstance(raw_result, Mapping):
            result = dict(raw_result)
        else:
            result = _panel_action_result_payload(
                _PACKAGE_BUILD_ACTION_ID,
                status="failed",
                handled=False,
                error_type=type(raw_result).__name__,
            )
        self._finish_action(result)
        return dict(result)

    def selected_panel_source(self) -> str:
        value = self._panel_source_combo.currentData()
        return str(value) if value else "none"

    def _selected_panel_bboxes(self) -> dict[str, Any] | None:
        kind = self.selected_panel_source()
        resolver = getattr(self._tool, "resolve_panel_geometry", None)
        if kind == "none" or not callable(resolver):
            return None
        geometry = resolver(kind)
        bboxes = geometry.get("panelBboxes") if isinstance(geometry, Mapping) else None
        return dict(bboxes) if isinstance(bboxes, Mapping) and bboxes else None

    def cancel_preview_submit(self) -> dict[str, Any]:
        canceller = getattr(self._tool, "cancel_last_preview_submit", None)
        if not callable(canceller):
            result = _panel_action_result_payload(
                "sam3_ai_select.cancel_preview_submit",
                status="blocked",
                handled=False,
                blocked_reason_codes=["manga_mask_assistant_tool_missing"],
            )
            self._finish_action(result)
            return result
        outcome = canceller()
        ok = bool(outcome.get("ok")) if isinstance(outcome, Mapping) else False
        reason = str(outcome.get("reasonCode") or "") if isinstance(outcome, Mapping) else ""
        result = _panel_action_result_payload(
            "sam3_ai_select.cancel_preview_submit",
            status="cancelled" if ok else "blocked",
            handled=ok,
            blocked_reason_codes=[] if ok else [reason or "cancel_unsupported"],
        )
        self._finish_action(result)
        return result

    def execute_action(self, action_id: str) -> dict[str, Any]:
        normalized_action_id = str(action_id or "").strip()
        tool = self._tool
        if not normalized_action_id:
            result = _panel_action_result_payload(
                normalized_action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["manga_mask_assistant_action_id_missing"],
            )
            self._finish_action(result)
            return result

        if tool is None:
            result = _panel_action_result_payload(
                normalized_action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["manga_mask_assistant_tool_missing"],
            )
            self._finish_action(result)
            return result

        executor = getattr(tool, "execute_manga_mask_assistant_action", None)
        if not callable(executor):
            result = _panel_action_result_payload(
                normalized_action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["manga_mask_assistant_action_executor_missing"],
            )
            self._finish_action(result)
            return result

        try:
            raw_result = executor(normalized_action_id)
        except Exception as exc:  # noqa: BLE001 - keep UI result JSON-safe and scrubbed.
            result = _panel_action_result_payload(
                normalized_action_id,
                status="failed",
                handled=False,
                error_type=type(exc).__name__,
            )
            self._finish_action(result)
            return result

        if isinstance(raw_result, Mapping):
            result = dict(raw_result)
            scheduled_result = self._maybe_schedule_async_action(normalized_action_id, result)
            if scheduled_result is not None:
                result = scheduled_result
        else:
            result = _panel_action_result_payload(
                normalized_action_id,
                status="failed",
                handled=False,
                error_type=type(raw_result).__name__,
            )
        self._finish_action(result)
        return dict(result)

    def _maybe_schedule_async_action(
        self,
        action_id: str,
        result: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        if not _requires_async_scheduler(result):
            return None
        scheduler = self._action_scheduler
        if scheduler is None:
            return None

        blocked_reason_codes = _string_list(result.get("blockedReasonCodes"))
        try:
            scheduled = bool(scheduler(action_id, None))
        except Exception:
            scheduled = False
        if scheduled:
            return _panel_action_result_payload(
                action_id,
                status="scheduled",
                handled=True,
            )
        return _panel_action_result_payload(
            action_id,
            status="blocked",
            handled=False,
            blocked_reason_codes=[
                *blocked_reason_codes,
                "ai_select_async_action_schedule_failed",
            ],
        )

    def set_candidate_acceptance(self, request: object) -> dict[str, Any]:
        if not isinstance(request, Mapping):
            result = _panel_action_result_payload(
                _ACCEPTANCE_ACTION_ID,
                status="blocked",
                handled=False,
                blocked_reason_codes=["manga_mask_assistant_acceptance_request_invalid"],
            )
            self._finish_action(result)
            return result

        candidate_id = _optional_text(request.get("candidateId"))
        accepted = bool(request.get("accepted"))
        scope = _optional_text(request.get("scope"))
        panel_id = _optional_text(request.get("panelId"))
        if candidate_id is None:
            result = _panel_action_result_payload(
                _ACCEPTANCE_ACTION_ID,
                status="blocked",
                handled=False,
                blocked_reason_codes=["manga_mask_assistant_candidate_id_missing"],
            )
            self._finish_action(result)
            return result

        tool = self._tool
        if tool is None:
            result = _panel_action_result_payload(
                _ACCEPTANCE_ACTION_ID,
                status="blocked",
                handled=False,
                blocked_reason_codes=["manga_mask_assistant_tool_missing"],
            )
            self._finish_action(result)
            return result

        executor = getattr(tool, "set_manga_mask_assistant_candidate_acceptance", None)
        if not callable(executor):
            result = _panel_action_result_payload(
                _ACCEPTANCE_ACTION_ID,
                status="blocked",
                handled=False,
                blocked_reason_codes=["manga_mask_assistant_acceptance_executor_missing"],
            )
            self._finish_action(result)
            return result

        try:
            raw_result = executor(
                candidate_id,
                accepted,
                scope=scope,
                panel_id=panel_id,
            )
        except Exception as exc:  # noqa: BLE001 - keep UI result JSON-safe and scrubbed.
            result = _panel_action_result_payload(
                _ACCEPTANCE_ACTION_ID,
                status="failed",
                handled=False,
                error_type=type(exc).__name__,
            )
            self._finish_action(result)
            return result

        if isinstance(raw_result, Mapping):
            result = dict(raw_result)
        else:
            result = _panel_action_result_payload(
                _ACCEPTANCE_ACTION_ID,
                status="failed",
                handled=False,
                error_type=type(raw_result).__name__,
            )
        self._finish_action(result)
        return dict(result)

    def row_count(self) -> int:
        return self._table_widget.row_count()

    def selected_preset(self) -> str:
        value = _optional_text(self._preset_combo.currentData())
        if value in SAM3_MANGA_MASK_PRESET_VALUES:
            return value
        return "all"

    def selected_build_category(self) -> str | None:
        value = _optional_text(self._build_category_combo.currentData())
        if value in SAM3_MANGA_MASK_CATEGORY_VALUES:
            return value
        return None

    def set_build_category(self, category: str) -> None:
        normalized_category = (
            category if category in SAM3_MANGA_MASK_CATEGORY_VALUES else _default_build_category()
        )
        index = self._build_category_combo.findData(normalized_category)
        if index < 0:
            index = self._build_category_combo.findData(_default_build_category())
        if index < 0:
            return
        if self._build_category_combo.currentIndex() == index:
            self._refresh_build_controls(self._tool)
            return
        self._build_category_combo.setCurrentIndex(index)

    def set_preset(self, preset: str) -> None:
        normalized_preset = preset if preset in SAM3_MANGA_MASK_PRESET_VALUES else "all"
        index = self._preset_combo.findData(normalized_preset)
        if index < 0:
            index = self._preset_combo.findData("all")
        if index < 0:
            return
        if self._preset_combo.currentIndex() == index:
            self.refresh_from_tool()
            return
        self._preset_combo.setCurrentIndex(index)

    def status_text(self) -> str:
        return self._status_label.text()

    def table_widget(self) -> Sam3MangaMaskAssistantTableWidget:
        return self._table_widget

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override name.
        if event.type() == QEvent.Type.LanguageChange:
            current_category = self.selected_build_category()
            current_preset = self.selected_preset()
            self._rebuild_build_category_combo(current_category)
            self._rebuild_preset_combo(current_preset)
            self.refresh_from_tool()
        super().changeEvent(event)

    def _setup_ui(self) -> None:
        self.setObjectName("Sam3MangaMaskAssistantPanel")
        self._status_label.setObjectName("sam3MangaMaskAssistantPanelStatus")
        self._scorecard_label.setObjectName("sam3MangaMaskAssistantScorecardSummary")
        self._build_category_combo.setObjectName("sam3MangaMaskAssistantBuildCategory")
        self._build_package_button.setObjectName("sam3MangaMaskAssistantBuildPackageButton")
        self._build_package_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._rebuild_build_category_combo(_default_build_category())
        self._build_category_combo.currentIndexChanged.connect(
            lambda _index: self._refresh_build_controls(self._tool)
        )
        self._build_package_button.setText(_ts("Build Package"))
        self._preset_combo.setObjectName("sam3MangaMaskAssistantPreset")
        self._rebuild_preset_combo("all")
        self._preset_combo.currentIndexChanged.connect(lambda _index: self.refresh_from_tool())

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self._status_label)
        layout.addWidget(self._scorecard_label)
        build_layout = QHBoxLayout()
        build_layout.setContentsMargins(0, 0, 0, 0)
        build_layout.setSpacing(6)
        self._build_category_label.setText(_ts("Category"))
        build_layout.addWidget(self._build_category_label)
        build_layout.addWidget(self._build_category_combo, 1)
        build_layout.addWidget(self._build_package_button)
        layout.addLayout(build_layout)
        preset_layout = QHBoxLayout()
        preset_layout.setContentsMargins(0, 0, 0, 0)
        preset_layout.setSpacing(6)
        self._preset_label.setText(_ts("Preset"))
        preset_layout.addWidget(self._preset_label)
        preset_layout.addWidget(self._preset_combo, 1)
        layout.addLayout(preset_layout)
        panel_source_layout = QHBoxLayout()
        panel_source_layout.setContentsMargins(0, 0, 0, 0)
        panel_source_layout.setSpacing(6)
        self._panel_source_label.setText(_ts("Panel source"))
        self._panel_source_combo.setObjectName("sam3MangaMaskAssistantPanelSource")
        for kind in ("none", "comic_panels", "vector_rectangles"):
            self._panel_source_combo.addItem(_ts(kind), kind)
        panel_source_layout.addWidget(self._panel_source_label)
        panel_source_layout.addWidget(self._panel_source_combo, 1)
        self._cancel_preview_button.setObjectName("sam3MangaMaskAssistantCancelPreview")
        self._cancel_preview_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextOnly)
        self._cancel_preview_button.setText(_ts("Cancel Preview"))
        panel_source_layout.addWidget(self._cancel_preview_button)
        layout.addLayout(panel_source_layout)
        layout.addWidget(self._table_widget)

    def _rebuild_build_category_combo(self, selected_category: str | None) -> None:
        category = (
            selected_category
            if selected_category in SAM3_MANGA_MASK_CATEGORY_VALUES
            else _default_build_category()
        )
        blocked = self._build_category_combo.blockSignals(True)
        try:
            self._build_category_combo.clear()
            for definition in manga_mask_category_definitions():
                self._build_category_combo.addItem(
                    _ts(definition.default_label_en),
                    definition.value,
                )
            index = self._build_category_combo.findData(category)
            if index < 0:
                index = self._build_category_combo.findData(_default_build_category())
            if index >= 0:
                self._build_category_combo.setCurrentIndex(index)
        finally:
            self._build_category_combo.blockSignals(blocked)
        self._build_category_label.setText(_ts("Category"))
        self._build_package_button.setText(_ts("Build Package"))

    def _rebuild_preset_combo(self, selected_preset: str) -> None:
        blocked = self._preset_combo.blockSignals(True)
        try:
            self._preset_combo.clear()
            for preset in SAM3_MANGA_MASK_PRESET_VALUES:
                self._preset_combo.addItem(_preset_label(preset), preset)
            index = self._preset_combo.findData(selected_preset)
            if index < 0:
                index = self._preset_combo.findData("all")
            if index >= 0:
                self._preset_combo.setCurrentIndex(index)
        finally:
            self._preset_combo.blockSignals(blocked)
        self._preset_label.setText(_ts("Preset"))

    def _set_table_model(self, table_model: Mapping[str, Any], status_text: str) -> None:
        self._last_table_model = dict(table_model)
        self._table_widget.set_table_model(table_model)
        self._status_label.setText(status_text)

    def _refresh_build_controls(self, tool: _MangaMaskAssistantToolLike | None) -> None:
        readiness: dict[str, Any] | None = None
        blocked_reason_codes: list[str] = []
        enabled = False
        if tool is None:
            blocked_reason_codes = ["manga_mask_assistant_tool_missing"]
        elif not callable(
            getattr(tool, "build_manga_mask_assistant_package_from_last_session", None)
        ):
            blocked_reason_codes = ["manga_mask_assistant_package_build_executor_missing"]
        else:
            readiness_getter = getattr(
                tool,
                "get_manga_mask_assistant_package_build_readiness_snapshot",
                None,
            )
            if not callable(readiness_getter):
                blocked_reason_codes = ["manga_mask_assistant_package_build_readiness_missing"]
            else:
                readiness, enabled, blocked_reason_codes = _package_build_readiness_state(
                    readiness_getter,
                    self.selected_build_category(),
                )

        self._last_build_readiness = dict(readiness) if readiness is not None else None
        self._build_package_button.setEnabled(enabled)
        if enabled:
            self._build_package_button.setToolTip(
                _ts("Build Manga mask package from last SAM3 session")
            )
        else:
            self._build_package_button.setToolTip(_build_unavailable_tooltip(blocked_reason_codes))

    def _refresh_scorecard_summary(self, tool: _MangaMaskAssistantToolLike | None) -> None:
        snapshot = _scorecard_snapshot(tool)
        self._last_scorecard_snapshot = dict(snapshot)
        text = _scorecard_summary_text(snapshot)
        self._scorecard_label.setText(text)
        self._scorecard_label.setToolTip(text)

    def _finish_action(self, result: dict[str, Any]) -> None:
        self._last_action_result = dict(result)
        self.refresh_from_tool()
        status = str(result.get("status") or "").strip()
        if status:
            self._status_label.setText(f"{_ts('Action')}: {status}")
        self.action_executed.emit(dict(result))


def _empty_table_model(
    preset: str,
    *,
    warning_codes: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": SAM3_MANGA_MASK_ASSISTANT_TABLE_MODEL_SCHEMA_VERSION,
        "filterResultSchemaVersion": None,
        "activePreset": preset,
        "totalRowCount": 0,
        "visibleRowCount": 0,
        "hiddenRowCount": 0,
        "columns": list(SAM3_MANGA_MASK_ASSISTANT_TABLE_COLUMNS),
        "rows": [],
        "warningCodes": list(warning_codes or []),
    }


def _panel_action_result_payload(
    action_id: str,
    *,
    status: str,
    handled: bool,
    blocked_reason_codes: list[str] | None = None,
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _PANEL_ACTION_RESULT_SCHEMA_VERSION,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "blockedReasonCodes": list(blocked_reason_codes or []),
        "errorType": error_type,
    }


def _package_build_readiness_state(
    readiness_getter: Any,
    category: str | None,
) -> tuple[dict[str, Any] | None, bool, list[str]]:
    try:
        raw_readiness = readiness_getter(category)
    except Exception as exc:  # noqa: BLE001 - tooltip/result must stay scrubbed.
        return (
            {
                "ready": False,
                "blockedReasonCodes": ["manga_mask_assistant_package_build_readiness_failed"],
                "errorType": type(exc).__name__,
            },
            False,
            ["manga_mask_assistant_package_build_readiness_failed"],
        )
    if not isinstance(raw_readiness, Mapping):
        return None, False, ["manga_mask_assistant_package_build_readiness_invalid"]
    readiness = dict(raw_readiness)
    enabled = bool(readiness.get("ready"))
    blocked_reason_codes = [
        str(code)
        for code in readiness.get("blockedReasonCodes", [])
        if _optional_text(code) is not None
    ]
    if not enabled and not blocked_reason_codes:
        blocked_reason_codes = ["manga_mask_assistant_package_build_not_ready"]
    return readiness, enabled, blocked_reason_codes


def _scorecard_snapshot(tool: _MangaMaskAssistantToolLike | None) -> dict[str, Any]:
    if tool is None:
        return _scorecard_blocked_snapshot(["manga_mask_assistant_tool_missing"])

    getter = getattr(tool, "get_manga_mask_assistant_scorecard_snapshot", None)
    if not callable(getter):
        return _scorecard_blocked_snapshot(["manga_mask_assistant_scorecard_snapshot_missing"])

    try:
        raw_snapshot = getter()
    except Exception as exc:  # noqa: BLE001 - keep scorecard summary scrubbed.
        return _scorecard_blocked_snapshot(
            ["manga_mask_assistant_scorecard_snapshot_failed"],
            error_type=type(exc).__name__,
        )
    if not isinstance(raw_snapshot, Mapping):
        return _scorecard_blocked_snapshot(["manga_mask_assistant_scorecard_snapshot_invalid"])
    return dict(raw_snapshot)


def _scorecard_blocked_snapshot(
    blocked_reason_codes: list[str],
    *,
    error_type: str | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": _SCORECARD_SNAPSHOT_SCHEMA_VERSION,
        "tool": _SCORECARD_TOOL_ID,
        "ready": False,
        "ok": False,
        "packageId": None,
        "candidateCount": 0,
        "acceptedCandidateCount": 0,
        "readyCandidateCount": 0,
        "scorecard": None,
        "blockedReasonCodes": list(blocked_reason_codes),
        "errorType": error_type,
    }


def _scorecard_summary_text(snapshot: Mapping[str, Any]) -> str:
    blocked = [
        code
        for code in (_optional_text(item) for item in snapshot.get("blockedReasonCodes", []))
        if code is not None
    ]
    if blocked:
        return _ts("Scorecard: {reason}").format(reason=", ".join(blocked))
    ready_count = _optional_int(snapshot.get("readyCandidateCount")) or 0
    accepted_count = _optional_int(snapshot.get("acceptedCandidateCount")) or 0
    candidate_count = _optional_int(snapshot.get("candidateCount")) or 0
    if bool(snapshot.get("ok")):
        return _ts("Scorecard: OK - {ready} ready / {accepted} accepted / {total} total").format(
            ready=ready_count,
            accepted=accepted_count,
            total=candidate_count,
        )
    return _ts("Scorecard: {ready} ready / {accepted} accepted / {total} total").format(
        ready=ready_count,
        accepted=accepted_count,
        total=candidate_count,
    )


def _build_unavailable_tooltip(blocked_reason_codes: list[str]) -> str:
    reason = ", ".join(blocked_reason_codes or ["manga_mask_assistant_package_build_not_ready"])
    return _ts("Build Package unavailable: {reason}").format(reason=reason)


def _status_text_for_table_model(table_model: Mapping[str, Any]) -> str:
    rows = table_model.get("rows")
    row_count = len(rows) if isinstance(rows, list) else 0
    if row_count <= 0:
        base_text = _ts("No mask candidates")
    else:
        visible = _optional_int(table_model.get("visibleRowCount")) or row_count
        total = _optional_int(table_model.get("totalRowCount")) or row_count
        base_text = _ts("{visible} visible / {total} total").format(visible=visible, total=total)
    submit_text = _inpaint_submit_status_text(table_model)
    if submit_text:
        return f"{base_text} | {submit_text}"
    return base_text


def _inpaint_submit_status_text(table_model: Mapping[str, Any]) -> str | None:
    status = _optional_text(table_model.get("mangaInpaintSubmitStatus"))
    if status is None:
        return None
    text = f"{_ts('Inpaint submit')}: {status}"
    reasons = table_model.get("mangaInpaintSubmitBlockedReasonCodes")
    if isinstance(reasons, list) and reasons:
        reason_text = ", ".join(str(reason) for reason in reasons if str(reason).strip())
        if reason_text:
            text = f"{text} ({reason_text})"
    if table_model.get("mangaInpaintSubmitDiffQaOk") is False:
        text = f"{text} | {_ts('Diff QA')}: warning"
    return text


def _requires_async_scheduler(result: Mapping[str, Any]) -> bool:
    if result.get("status") != "blocked":
        return False
    blocked_reason_codes = set(_string_list(result.get("blockedReasonCodes")))
    return bool(blocked_reason_codes & _ASYNC_REQUIRED_BLOCKED_REASONS)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        text = _optional_text(item)
        if text is not None:
            result.append(text)
    return result


def _preset_label(preset: str) -> str:
    if preset == "speech_bubble_protection":
        return _ts("Speech Bubble Protection")
    if preset == "character_extraction":
        return _ts("Character Extraction")
    if preset == "background_extraction":
        return _ts("Background Extraction")
    if preset == "inpaint_prep":
        return _ts("Inpaint Prep")
    return _ts("All")


def _default_build_category() -> str:
    return SAM3_MANGA_MASK_CATEGORY_VALUES[0]


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(float(value))
    except (OverflowError, TypeError, ValueError):
        return None


def _ts(text: str) -> str:
    return translated_literal_domain(_TRANSLATION_DOMAIN, text, use_fallback=True)


__all__ = ["Sam3MangaMaskAssistantPanel"]
