"""Panel bridge for SAM3 AI Select candidate list actions."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QToolButton, QVBoxLayout, QWidget

from ...ui.translations import translated_literal_domain
from ...ui.widgets.sam31_memory_release import Sam31MemoryReleaseWidget
from .candidate_list_model import CANDIDATE_LIST_VIEW_MODEL_SCHEMA_VERSION
from .candidate_list_widget import Sam3AISelectCandidateListWidget
from .mask_refine_dialog import show_mask_refine_dialog

_TRANSLATION_DOMAIN = "SAM3AISelect"
_TOOL_ID = "sam3_ai_select"
_PANEL_ACTION_RESULT_SCHEMA_VERSION = "sam3.ai_select_candidate_list_panel_action_result.v1"
_COMBINE_SELECTED_ACTION_ID = "sam3_ai_select.combine.selected"
_GROUP_SELECTED_TO_LAYER_GROUP_ACTION_ID = "sam3_ai_select.group.selected_to_layer_group"
_MASK_REFINE_ACTION_ID = "sam3_ai_select.mask_refine.selected"
_COMBINE_MODES = ("union", "subtract", "intersect", "xor")
_ASYNC_REQUIRED_BLOCKED_REASONS = frozenset(
    {
        "ai_select_run_requires_async_executor",
        "ai_select_refine_requires_async_executor",
        "ai_select_preview_submit_requires_async_executor",
    }
)


class _CandidateListToolLike(Protocol):
    def get_candidate_list_view_model_snapshot(self) -> dict[str, Any]: ...

    def execute_context_action(self, action_id: str, **kwargs: Any) -> dict[str, Any]: ...


class Sam3AISelectCandidateListPanel(QWidget):
    """Bind a SAM3 candidate list widget to a tool action executor."""

    action_executed = Signal(dict)

    def __init__(
        self,
        tool: _CandidateListToolLike | None = None,
        parent: QWidget | None = None,
        canvas_provider: Callable[[], object | None] | None = None,
        action_scheduler: Callable[[str, dict[str, object] | None], bool] | None = None,
        dialog_factory: Callable[[QWidget], dict[str, object] | None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._tool: _CandidateListToolLike | None = None
        self._canvas_provider = canvas_provider
        self._action_scheduler = action_scheduler
        self._dialog_factory = dialog_factory or show_mask_refine_dialog
        self._pending_mask_refine_options: dict[str, object] | None = None
        self._last_view_model: dict[str, Any] = _empty_view_model()
        self._last_action_result: dict[str, Any] | None = None
        self._status_label = QLabel(self)
        self._combine_mode_combo = QComboBox(self)
        self._combine_button = QToolButton(self)
        self._layer_group_button = QToolButton(self)
        self._mask_refine_button = QToolButton(self)
        self._candidate_widget = Sam3AISelectCandidateListWidget(self)
        self._setup_ui()
        self._candidate_widget.action_requested.connect(self.execute_action)
        self._candidate_widget.selection_changed.connect(
            lambda _candidate_ids: self._sync_combine_controls()
        )
        self._combine_button.clicked.connect(
            lambda: self.execute_action(_COMBINE_SELECTED_ACTION_ID)
        )
        self._layer_group_button.clicked.connect(
            lambda: self.execute_action(_GROUP_SELECTED_TO_LAYER_GROUP_ACTION_ID)
        )
        self._mask_refine_button.clicked.connect(self.open_mask_refine_dialog)
        self.bind_tool(tool)

    @property
    def last_action_result(self) -> dict[str, Any] | None:
        return dict(self._last_action_result) if self._last_action_result is not None else None

    def bind_tool(self, tool: _CandidateListToolLike | None) -> None:
        self._tool = tool
        self.refresh_from_tool()

    def set_canvas_provider(
        self,
        canvas_provider: Callable[[], object | None] | None,
    ) -> None:
        self._canvas_provider = canvas_provider

    def set_action_scheduler(
        self,
        action_scheduler: Callable[[str, dict[str, object] | None], bool] | None,
    ) -> None:
        self._action_scheduler = action_scheduler

    def refresh_from_tool(self) -> dict[str, Any]:
        tool = self._tool
        if tool is None:
            view_model = _empty_view_model()
            self._set_view_model(view_model, _ts("No AI Select tool"))
            return dict(view_model)

        snapshot_getter = getattr(tool, "get_candidate_list_view_model_snapshot", None)
        if not callable(snapshot_getter):
            view_model = _empty_view_model(warning_codes=["ai_select_view_model_missing"])
            self._set_view_model(view_model, _ts("Candidate list unavailable"))
            return dict(view_model)

        try:
            raw_view_model = snapshot_getter()
        except Exception as exc:  # noqa: BLE001 - UI bridge must not leak tool failures.
            view_model = _empty_view_model(warning_codes=["ai_select_view_model_failed"])
            self._set_view_model(view_model, f"{_ts('Refresh failed')}: {type(exc).__name__}")
            return dict(view_model)

        if isinstance(raw_view_model, Mapping):
            view_model = dict(raw_view_model)
        else:
            view_model = _empty_view_model()
        self._set_view_model(view_model, _status_text_for_view_model(view_model))
        return dict(view_model)

    def execute_action(self, action_id: str) -> dict[str, Any]:
        normalized_action_id = str(action_id or "").strip()
        tool = self._tool
        if not normalized_action_id:
            result = _panel_action_result_payload(
                normalized_action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["ai_select_action_id_missing"],
            )
            self._finish_action(result)
            return result

        if tool is None:
            result = _panel_action_result_payload(
                normalized_action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["ai_select_tool_missing"],
            )
            self._finish_action(result)
            return result

        executor = getattr(tool, "execute_context_action", None)
        if not callable(executor):
            result = _panel_action_result_payload(
                normalized_action_id,
                status="blocked",
                handled=False,
                blocked_reason_codes=["ai_select_action_executor_missing"],
            )
            self._finish_action(result)
            return result

        try:
            raw_result = executor(
                normalized_action_id,
                **self._executor_kwargs(normalized_action_id),
            )
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

    def row_count(self) -> int:
        return self._candidate_widget.row_count()

    def current_candidate_id(self) -> str | None:
        return self._candidate_widget.current_candidate_id()

    def selected_candidate_ids(self) -> list[str]:
        return self._candidate_widget.selected_candidate_ids()

    def status_text(self) -> str:
        return self._status_label.text()

    def candidate_widget(self) -> Sam3AISelectCandidateListWidget:
        return self._candidate_widget

    def open_mask_refine_dialog(self) -> dict[str, Any] | None:
        """Open the morphology dialog and run the mask-refine action on accept."""

        payload = self._dialog_factory(self)
        if payload is None:
            return None
        self._pending_mask_refine_options = dict(payload)
        try:
            return self.execute_action(_MASK_REFINE_ACTION_ID)
        finally:
            # Executor paths clear the stash on attach; this covers early
            # returns that never reach the executor kwargs builder.
            self._pending_mask_refine_options = None

    def _executor_kwargs(self, action_id: str) -> dict[str, object]:
        kwargs: dict[str, object] = {}
        canvas = self._resolve_canvas()
        if canvas is not None:
            kwargs["canvas"] = canvas
        if action_id in {_COMBINE_SELECTED_ACTION_ID, _GROUP_SELECTED_TO_LAYER_GROUP_ACTION_ID}:
            kwargs["selected_candidate_ids"] = self.selected_candidate_ids()
        if action_id == _COMBINE_SELECTED_ACTION_ID:
            kwargs["combine_mode"] = self._selected_combine_mode()
        if action_id == _MASK_REFINE_ACTION_ID:
            # Attach and clear in one place so blocked results or exceptions
            # can never leak stale options into a later action.
            kwargs["mask_refine_options"] = self._pending_mask_refine_options
            self._pending_mask_refine_options = None
        return kwargs

    def _resolve_canvas(self) -> object | None:
        provider = self._canvas_provider
        if provider is None:
            return None
        try:
            return provider()
        except Exception:
            return None

    def _setup_ui(self) -> None:
        self.setObjectName("Sam3AISelectCandidateListPanel")
        self._status_label.setObjectName("sam3CandidateListPanelStatus")
        self._combine_mode_combo.setObjectName("sam3CandidateListCombineMode")
        for mode in _COMBINE_MODES:
            self._combine_mode_combo.addItem(_combine_mode_label(mode), mode)
        self._combine_mode_combo.currentIndexChanged.connect(
            lambda _index: self._sync_combine_controls()
        )
        self._combine_button.setObjectName("sam3CandidateListCombineSelected")
        self._combine_button.setText(_ts("Combine Selected"))
        self._layer_group_button.setObjectName("sam3CandidateListLayerGroup")
        self._layer_group_button.setText(_ts("Layer Group"))
        self._mask_refine_button.setObjectName("sam3CandidateListMaskRefine")
        self._mask_refine_button.setText(_ts("Refine Mask..."))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        layout.addWidget(self._status_label)
        layout.addWidget(Sam31MemoryReleaseWidget(self))
        combine_layout = QHBoxLayout()
        combine_layout.setContentsMargins(0, 0, 0, 0)
        combine_layout.setSpacing(6)
        combine_layout.addWidget(QLabel(_ts("Mode"), self))
        combine_layout.addWidget(self._combine_mode_combo, 1)
        combine_layout.addWidget(self._combine_button)
        combine_layout.addWidget(self._layer_group_button)
        combine_layout.addWidget(self._mask_refine_button)
        layout.addLayout(combine_layout)
        layout.addWidget(self._candidate_widget)
        self._sync_combine_controls()

    def _set_view_model(self, view_model: Mapping[str, Any], status_text: str) -> None:
        self._last_view_model = dict(view_model)
        self._candidate_widget.set_view_model(view_model)
        self._status_label.setText(status_text)
        self._sync_combine_controls()

    def _finish_action(self, result: dict[str, Any]) -> None:
        self._last_action_result = dict(result)
        self.refresh_from_tool()
        status = str(result.get("status") or "").strip()
        if status:
            status_text = f"{_ts('Action')}: {status}"
            # Refine results carry the base candidate id so the user can
            # verify which candidate an operation actually targeted.
            target_candidate_id = _optional_text(result.get("targetCandidateId"))
            if target_candidate_id is not None:
                status_text = f"{status_text} → {target_candidate_id}"
            self._status_label.setText(status_text)
        self.action_executed.emit(dict(result))

    def _selected_combine_mode(self) -> str:
        value = self._combine_mode_combo.currentData()
        text = _optional_text(value)
        return text if text in _COMBINE_MODES else "union"

    def _sync_combine_controls(self) -> None:
        selected_count = len(self.selected_candidate_ids())
        enabled = self._tool is not None and selected_count >= 2
        self._combine_button.setEnabled(enabled)
        combine_description = _ts(
            "Combine the multi-selected candidates (Ctrl+click rows) into one mask "
            "using the Mode"
        )
        layer_group_description = _ts(
            "Cut each multi-selected candidate to its own layer inside a new layer group"
        )
        if enabled:
            self._combine_button.setToolTip(combine_description)
            self._layer_group_button.setToolTip(layer_group_description)
        else:
            hint = _ts("Select at least two candidates")
            self._combine_button.setToolTip(f"{combine_description}\n{hint}")
            self._layer_group_button.setToolTip(f"{layer_group_description}\n{hint}")
        self._layer_group_button.setEnabled(enabled)
        # Mask refine operates on the session's current candidate, not the
        # multi-select set.
        refine_enabled = (
            self._tool is not None
            and _optional_text(self._last_view_model.get("selectedCandidateId")) is not None
        )
        self._mask_refine_button.setEnabled(refine_enabled)
        mask_refine_description = _ts(
            "Manually adjust the selected candidate's mask "
            "(feather / expand / fill holes — no AI inference)"
        )
        self._mask_refine_button.setToolTip(
            mask_refine_description
            if refine_enabled
            else f"{mask_refine_description}\n{_ts('Select a candidate')}"
        )


def _empty_view_model(*, warning_codes: list[str] | None = None) -> dict[str, Any]:
    return {
        "schemaVersion": CANDIDATE_LIST_VIEW_MODEL_SCHEMA_VERSION,
        "tool": _TOOL_ID,
        "candidateListSchemaVersion": None,
        "contextActionsSchemaVersion": None,
        "hasOverlayModel": False,
        "hasSession": False,
        "candidateSetId": None,
        "selectedCandidateId": None,
        "selectedCandidateIndex": None,
        "operation": None,
        "availableCandidateIds": [],
        "rejectedCandidateIds": [],
        "visibleCandidateIds": [],
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
        "tool": _TOOL_ID,
        "actionId": action_id,
        "status": status,
        "handled": bool(handled),
        "blockedReasonCodes": list(blocked_reason_codes or []),
        "errorType": error_type,
    }


def _status_text_for_view_model(view_model: Mapping[str, Any]) -> str:
    rows = view_model.get("rows")
    row_count = len(rows) if isinstance(rows, list) else 0
    if row_count <= 0:
        base_text = _ts("No candidates")
    else:
        selected_candidate_id = _optional_text(view_model.get("selectedCandidateId"))
        count_text = _ts("{count} candidates").format(count=row_count)
        if selected_candidate_id is None:
            base_text = count_text
        else:
            base_text = f"{count_text} | {_ts('Selected')}: {selected_candidate_id}"
    preview_submit_text = _preview_submit_status_text(view_model)
    if preview_submit_text:
        return f"{base_text} | {preview_submit_text}"
    return base_text


def _preview_submit_status_text(view_model: Mapping[str, Any]) -> str | None:
    status = _optional_text(view_model.get("previewSubmitStatus"))
    if status is None:
        return None
    text = f"{_ts('Preview submit')}: {status}"
    reasons = view_model.get("previewSubmitBlockedReasonCodes")
    if isinstance(reasons, list) and reasons:
        reason_text = ", ".join(str(reason) for reason in reasons if str(reason).strip())
        if reason_text:
            text = f"{text} ({reason_text})"
    if view_model.get("previewSubmitDiffQaOk") is False:
        text = f"{text} | {_ts('Diff QA')}: warning"
    return text


def _combine_mode_label(mode: str) -> str:
    if mode == "subtract":
        return _ts("Subtract")
    if mode == "intersect":
        return _ts("Intersect")
    if mode == "xor":
        return _ts("Xor")
    return _ts("Union")


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


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _ts(text: str) -> str:
    return translated_literal_domain(_TRANSLATION_DOMAIN, text, use_fallback=True)
