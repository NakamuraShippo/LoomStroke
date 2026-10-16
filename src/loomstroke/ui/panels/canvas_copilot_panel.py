"""Canvas Copilot dock panel for Canvas Agent plan/preview/apply wiring."""

from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Mapping, Sequence
from contextlib import suppress
from typing import Any

from PySide6.QtCore import QEvent, Qt
from PySide6.QtWidgets import (
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .canvas_agent_preview_presenter import CanvasAgentPreviewPresenter

logger = logging.getLogger(__name__)

_READY_PLAN_STATES = frozenset({"planned", "ready"})
_PANEL_APPLY_ALLOWED_OPERATIONS = frozenset(
    {
        "create_layer",
        "duplicate_layer",
        "rename_layer",
        "set_layer_opacity",
        "set_layer_blend_mode",
        "add_text_object",
        "add_vector_shape",
        "import_asset_as_layer",
        "submit_generation_to_new_layer",
        "apply_selection_mask",
    }
)


class CanvasCopilotPanel(QWidget):
    """Canvas Copilot panel wired through the Canvas Agent MCP facade.

    The panel keeps only display snapshots. It never mutates the document
    directly; preview/apply/verify calls go through the service facade.
    """

    def __init__(self, service: Any | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._init_state(service)
        self._init_widgets()
        self._setup_ui()
        self.refresh_translations()
        self.refresh_context()

    def _init_state(self, service: Any | None) -> None:
        self._service = service
        self._state = "idle"
        self._last_context: dict[str, Any] | None = None
        self._last_plan: dict[str, Any] | None = None
        self._last_changeset: dict[str, Any] | None = None
        self._last_preview: dict[str, Any] | None = None
        self._last_apply_result: dict[str, Any] | None = None
        self._last_verification: dict[str, Any] | None = None
        self._candidate_stack: dict[str, Any] | None = None
        self._selected_candidate_id: str | None = None
        self._latest_panel_apply_id: str | None = None
        self._latest_panel_apply_undo_count_before: int | None = None
        self._latest_panel_apply_undo_count_after: int | None = None
        self._apply_generation = 0
        self._active_apply_generation: int | None = None
        self._apply_task: asyncio.Task[Any] | None = None
        self._candidate_task: asyncio.Task[Any] | None = None
        self._candidate_generation_active = False
        self._preview_presenter = CanvasAgentPreviewPresenter()
        self._observed_canvas: Any | None = None
        self._field_labels: dict[str, QLabel] = {}
        self._chips: dict[str, QLabel] = {}
        self._clarification_option_buttons: dict[str, QPushButton] = {}

    def _init_widgets(self) -> None:  # noqa: PLR0915
        self._status_label = QLabel(self)
        self._refresh_button = QPushButton(self)
        self._prompt_edit = QPlainTextEdit(self)
        self._plan_button = QPushButton(self)
        self._preview_button = QPushButton(self)
        self._apply_button = QPushButton(self)
        self._discard_button = QPushButton(self)
        self._undo_button = QPushButton(self)
        self._verify_button = QPushButton(self)

        self._context_group = QGroupBox(self)
        self._plan_group = QGroupBox(self)
        self._clarification_group = QGroupBox(self)
        self._blocked_group = QGroupBox(self)
        self._candidate_group = QGroupBox(self)
        self._preview_group = QGroupBox(self)
        self._approval_group = QGroupBox(self)
        self._apply_result_group = QGroupBox(self)
        self._verify_group = QGroupBox(self)
        self._controls_group = QGroupBox(self)

        self._plan_status_value = QLabel("-", self)
        self._intent_value = QLabel("-", self)
        self._target_value = QLabel("-", self)
        self._approval_value = QLabel("-", self)
        self._run_value = QLabel("-", self)
        self._steps_list = QListWidget(self)
        self._clarification_list = QListWidget(self)
        self._clarification_question_value = QLabel("-", self)
        self._clarification_reason_value = QLabel("-", self)
        self._clarification_target_value = QLabel("-", self)
        self._clarification_options_widget = QWidget(self)
        self._blocked_list = QListWidget(self)
        self._candidate_status_value = QLabel("-", self)
        self._candidate_selected_value = QLabel("-", self)
        self._candidate_list = QListWidget(self)
        self._generate_candidates_button = QPushButton(self)
        self._select_candidate_button = QPushButton(self)
        self._preview_id_value = QLabel("-", self)
        self._preview_mode_value = QLabel("-", self)
        self._preview_status_value = QLabel("-", self)
        self._preview_mutation_value = QLabel("-", self)
        self._visual_preview_value = QLabel("-", self)
        self._thumbnail_assets_value = QLabel("-", self)
        self._overlay_value = QLabel("-", self)
        self._ticket_id_value = QLabel("-", self)
        self._ticket_scope_value = QLabel("-", self)
        self._ticket_expiry_value = QLabel("-", self)
        self._apply_id_value = QLabel("-", self)
        self._apply_status_value = QLabel("-", self)
        self._apply_history_value = QLabel("-", self)
        self._apply_layers_value = QLabel("-", self)
        self._verify_status_value = QLabel("-", self)
        self._verify_revision_value = QLabel("-", self)

    @property
    def service(self) -> Any | None:
        return self._service

    @property
    def last_context(self) -> dict[str, Any] | None:
        return self._last_context

    @property
    def last_plan(self) -> dict[str, Any] | None:
        return self._last_plan

    @property
    def last_changeset(self) -> dict[str, Any] | None:
        return self._last_changeset

    @property
    def last_preview(self) -> dict[str, Any] | None:
        return self._last_preview

    @property
    def last_apply_result(self) -> dict[str, Any] | None:
        return self._last_apply_result

    @property
    def last_verification(self) -> dict[str, Any] | None:
        return self._last_verification

    @property
    def candidate_stack(self) -> dict[str, Any] | None:
        return self._candidate_stack

    @property
    def selected_candidate_id(self) -> str | None:
        return self._selected_candidate_id

    @property
    def state(self) -> str:
        return self._state

    @property
    def status_text(self) -> str:
        return self._status_label.text()

    def set_service(self, service: Any | None) -> None:
        self._disconnect_canvas_observer()
        self._preview_presenter.clear(reason="service_change")
        self._service = service
        self._clear_plan_and_artifacts()
        self.refresh_context()

    def set_prompt_text(self, text: str) -> None:
        self._prompt_edit.setPlainText(text)

    def refresh_context(self) -> dict[str, Any] | None:
        """Refresh the read-only edit context displayed by the panel."""

        if self._state == "applying":
            self._set_status(self.tr("Apply is in progress."))
            return self._last_context

        self._clear_plan_and_artifacts()
        if self._service is None:
            self._last_context = None
            self._render_context()
            self._render_plan()
            self._render_artifacts()
            self._set_state("idle")
            self._set_status(self.tr("MCP runtime is not available."))
            return None

        try:
            context = self._service.get_edit_context({"includeThumbnail": False})
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            reason = _reason_code(exc)
            if reason:
                _log_panel_failure("context_refresh", exc, reason=reason)
                status = self.tr("Canvas context is not available: {reason}").format(reason=reason)
            else:
                _log_panel_failure("context_refresh", exc)
                status = self.tr("Canvas context refresh failed: {error}").format(
                    error=type(exc).__name__
                )
            self._last_context = None
            self._render_context()
            self._set_state("failed")
            self._set_status(status)
            return None

        self._last_context = context
        self._sync_canvas_observer()
        self._render_context()
        self._set_state("idle")
        self._set_status(self.tr("Context ready."))
        return context

    def request_plan(self) -> dict[str, Any] | None:
        """Request a local read-only Canvas Agent plan for the prompt."""

        if self._service is None:
            self._set_status(self.tr("MCP runtime is not available."))
            return None

        if self._state == "applying":
            self._set_status(self.tr("Apply is in progress."))
            return None

        prompt = self._prompt_edit.toPlainText().strip()
        if not prompt:
            self._set_status(self.tr("Prompt is required."))
            return None

        context = self._last_context or self.refresh_context()
        if context is None:
            return None

        self._clear_runtime_artifacts(clear_apply=True)
        self._set_state("planning")
        self._set_status(self.tr("Planning edit."))
        try:
            plan = self._service.plan_canvas_edit(
                {
                    "prompt": prompt,
                    "editContext": context,
                    "clientRequestId": "ui.canvas_copilot",
                }
            )
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            reason = _reason_code(exc)
            _log_panel_failure("plan", exc, reason=reason)
            self._last_plan = None
            self._render_plan()
            self._set_state("failed")
            self._set_status(
                self.tr("Planning failed: {error}").format(error=reason or type(exc).__name__)
            )
            return None

        self._last_plan = plan
        self._render_plan()
        if _plan_has_unresolved_clarification(plan):
            self._set_state("clarification_required")
            self._set_status(self.tr("Clarification needed."))
        elif _sequence(plan.get("blockedReasons")):
            self._set_state("blocked")
            self._set_status(self.tr("Plan has safety blocks."))
        else:
            self._set_state("plan_ready")
            self._set_status(self.tr("Plan ready."))
        return plan

    def request_clarification_option(
        self,
        selected_option_id: str,
        payload: Mapping[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Answer the current actionable clarification without applying changes."""

        if self._service is None:
            self._set_status(self.tr("MCP runtime is not available."))
            return None
        if self._state == "applying":
            self._set_status(self.tr("Apply is in progress."))
            return None
        plan = _mapping(self._last_plan)
        clarification = _mapping(plan.get("clarification"))
        run_id = str(plan.get("runId") or "").strip()
        clarification_id = str(clarification.get("clarificationId") or "").strip()
        if not run_id or not clarification_id:
            self._set_status(self.tr("Clarification is not available."))
            return None
        answer = getattr(self._service, "answer_canvas_clarification", None)
        if not callable(answer):
            self._set_status(self.tr("Clarification resolution is not available."))
            return None

        self._clear_runtime_artifacts(clear_apply=True, clear_candidates=False)
        self._set_state("resolving_clarification")
        self._set_status(self.tr("Resolving clarification."))
        try:
            result = answer(
                {
                    "runId": run_id,
                    "clarificationId": clarification_id,
                    "selectedOptionId": selected_option_id,
                    "payload": dict(payload or {}),
                }
            )
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            reason = _reason_code(exc)
            _log_panel_failure("clarification", exc, reason=reason)
            self._set_state("failed")
            self._set_status(
                self.tr("Clarification failed: {error}").format(error=reason or type(exc).__name__)
            )
            return None

        result_mapping = _mapping(result)
        status = str(result_mapping.get("status") or "").strip()
        if isinstance(result_mapping.get("plan"), Mapping):
            self._last_plan = dict(result_mapping["plan"])
        if isinstance(result_mapping.get("changeset"), Mapping):
            self._last_changeset = dict(result_mapping["changeset"])
        self._render_plan()
        self._render_artifacts()
        if status == "resolved":
            self._set_state("plan_ready")
            self._set_status(self.tr("Clarification resolved. Preview ready."))
        elif status == "discarded":
            self._set_state("discarded")
            self._set_status(self.tr("Preview discarded."))
        elif status == "requires_user_action":
            self._set_state("clarification_required")
            self._set_status(
                self.tr("Action required: {message}").format(
                    message=_text(result_mapping.get("message") or selected_option_id)
                )
            )
        else:
            self._set_state("blocked")
            self._set_status(self.tr("Clarification is blocked."))
        return dict(result_mapping)

    def request_preview(self) -> dict[str, Any] | None:
        """Create a non-mutating changeset preview for the current plan."""

        if self._service is None:
            self._set_status(self.tr("MCP runtime is not available."))
            return None
        if self._state == "applying":
            self._set_status(self.tr("Apply is in progress."))
            return None
        if not self._has_ready_plan():
            self._set_status(self.tr("Preview requires a ready plan."))
            return None

        plan = _mapping(self._last_plan)
        run_id = str(plan.get("runId") or "").strip()
        existing_changeset = _mapping(self._last_changeset)
        reusable_changeset = (
            dict(existing_changeset)
            if existing_changeset
            and not self._candidate_plan_ready()
            and str(existing_changeset.get("runId") or "").strip() == run_id
            else None
        )
        try:
            self._clear_runtime_artifacts(
                clear_apply=True,
                clear_candidates=not self._candidate_plan_ready(),
            )
            self._set_state("changeset_ready")
            if reusable_changeset is not None:
                changeset = reusable_changeset
            else:
                changeset_args = self._changeset_arguments_for_preview(plan, run_id)
                if changeset_args is None:
                    self._set_state("plan_ready")
                    self._set_status(self.tr("Select a candidate before preview."))
                    return None
                changeset = self._service.create_canvas_changeset(changeset_args)
            self._last_changeset = dict(changeset)

            changeset_id = str(changeset.get("changesetId") or "").strip()
            preview_args = (
                {"changesetId": changeset_id} if changeset_id else {"changeset": changeset}
            )
            preview = self._service.preview_canvas_changeset(preview_args)
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            reason = _reason_code(exc)
            _log_panel_failure("preview", exc, reason=reason)
            self._clear_runtime_artifacts(clear_apply=True)
            self._set_state("failed")
            self._set_status(
                self.tr("Preview failed: {error}").format(error=reason or type(exc).__name__)
            )
            return None

        self._last_preview = dict(preview)
        self._present_visual_preview(self._last_preview)
        self._render_artifacts()
        if preview.get("blocked"):
            self._set_state("blocked")
            self._set_status(self.tr("Preview is blocked."))
        elif self._approval_ticket_matches_changeset():
            self._set_state("awaiting_approval")
            self._set_status(self.tr("Preview ready."))
        else:
            self._set_state("preview_ready")
            self._set_status(self.tr("Preview ready."))
        return self._last_preview

    async def request_generation_candidates_async(self) -> dict[str, Any] | None:  # noqa: PLR0911
        """Submit an asset-only generation request and render its candidate stack."""

        if self._service is None:
            self._set_status(self.tr("MCP runtime is not available."))
            return None
        if self._state == "applying":
            self._set_status(self.tr("Apply is in progress."))
            return None
        if not self._candidate_plan_ready():
            self._set_status(self.tr("Candidate generation requires an AI generation plan."))
            return None
        if self._candidate_generation_active:
            self._set_status(self.tr("Candidate generation is already in progress."))
            return None
        layer_id = self._candidate_source_layer_id()
        if not layer_id:
            self._set_status(self.tr("Candidate generation requires a source layer."))
            return None

        self._candidate_generation_active = True
        self._clear_runtime_artifacts(clear_apply=True, clear_candidates=False)
        self._set_status(self.tr("Generating candidates."))
        self._update_controls()
        try:
            job = await _maybe_await(
                self._service.submit_generation(
                    {
                        "layerId": layer_id,
                        "applyToCanvas": False,
                        "metadata": {
                            "source": "canvas_copilot",
                            "candidateRequest": True,
                            "candidateCount": 3,
                            "sourcePromptDigest": _mapping(self._last_plan).get(
                                "sourcePromptDigest"
                            ),
                        },
                    }
                )
            )
            job_id = str(_mapping(job).get("jobId") or "").strip()
            if not job_id:
                self._set_status(self.tr("Candidate generation did not create a job."))
                return None
            waited = await _maybe_await(
                self._service.wait_job({"jobId": job_id, "timeoutMs": 30000, "pollIntervalMs": 50})
            )
            if str(_mapping(waited).get("status") or "") != "completed":
                reason = _text(
                    _mapping(_mapping(waited).get("metadata")).get("reason_code")
                    or _mapping(waited).get("status")
                    or "failed"
                )
                self._set_status(
                    self.tr("Candidate generation failed: {reason}").format(reason=reason)
                )
                return None
            stack = self._service.build_canvas_candidate_stack(
                {"runId": _mapping(self._last_plan).get("runId"), "job": waited}
            )
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            reason = _reason_code(exc)
            _log_panel_failure("generation_candidates", exc, reason=reason)
            self._set_status(
                self.tr("Candidate generation failed: {error}").format(
                    error=reason or type(exc).__name__
                )
            )
            return None
        finally:
            self._candidate_generation_active = False
            self._update_controls()

        self._candidate_stack = dict(stack)
        self._selected_candidate_id = None
        self._render_candidates()
        count = len(_sequence(self._candidate_stack.get("candidates")))
        self._set_state("plan_ready")
        self._set_status(self.tr("Candidate stack ready: {count} assets.").format(count=count))
        return self._candidate_stack

    def request_generation_candidates(self) -> Any:
        """Button slot wrapper for async candidate generation."""

        coroutine = self.request_generation_candidates_async()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)
        task = loop.create_task(coroutine)
        self._candidate_task = task
        return task

    def request_select_candidate(self, candidate_id: str | None = None) -> dict[str, Any] | None:
        """Select one generated candidate for preview/apply."""

        if self._service is None:
            self._set_status(self.tr("MCP runtime is not available."))
            return None
        stack = _mapping(self._candidate_stack)
        if not stack:
            self._set_status(self.tr("Candidate stack is not available."))
            return None
        selected = candidate_id or self._current_candidate_id()
        if not selected:
            self._set_status(self.tr("Select a candidate before preview."))
            return None
        try:
            stack = self._service.select_canvas_candidate(
                {"candidateStack": stack, "selectedCandidateId": selected}
            )
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            reason = _reason_code(exc)
            _log_panel_failure("candidate_select", exc, reason=reason)
            self._set_status(
                self.tr("Candidate selection failed: {error}").format(
                    error=reason or type(exc).__name__
                )
            )
            return None
        self._candidate_stack = dict(stack)
        self._selected_candidate_id = str(stack.get("selectedCandidateId") or "")
        self._clear_runtime_artifacts(clear_apply=True, clear_candidates=False)
        self._render_candidates()
        self._set_state("plan_ready")
        self._set_status(self.tr("Candidate selected."))
        return self._candidate_stack

    async def request_apply_async(self) -> dict[str, Any] | None:
        """Apply the preview-approved changeset through the MCP facade."""

        if self._service is None:
            self._set_status(self.tr("MCP runtime is not available."))
            return None
        if self._active_apply_generation is not None:
            self._set_status(self.tr("Apply is already in progress."))
            return None

        apply_args = self._apply_arguments()
        if apply_args is None:
            self._set_status(self.tr("Apply requires a preview approval ticket."))
            return None

        self._apply_generation += 1
        generation = self._apply_generation
        self._active_apply_generation = generation
        undo_count_before = self._history_undo_count()
        self._preview_presenter.clear(reason="apply_start")
        self._set_state("applying")
        self._set_status(self.tr("Applying edit."))
        try:
            result = await _maybe_await(self._service.apply_canvas_changeset(apply_args))
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            if self._active_apply_generation == generation:
                reason = _reason_code(exc)
                _log_panel_failure("apply", exc, reason=reason)
                self._clear_runtime_artifacts(clear_apply=False)
                self._set_state("failed")
                self._set_status(
                    self.tr("Apply failed: {error}").format(error=reason or type(exc).__name__)
                )
            return None
        finally:
            if self._active_apply_generation == generation:
                self._active_apply_generation = None

        if self._active_apply_generation not in {None, generation}:  # pragma: no cover
            return None

        self._last_apply_result = dict(result)
        self._last_verification = None
        self._latest_panel_apply_id = _str_or_none(result.get("applyId"))
        self._latest_panel_apply_undo_count_before = undo_count_before
        self._latest_panel_apply_undo_count_after = self._history_undo_count()
        self._last_changeset = None
        self._last_preview = None
        self._clear_candidate_stack(render=False)
        self._render_artifacts()
        self._set_state("applied")
        self._set_status(self.tr("Apply complete."))
        return self._last_apply_result

    def request_apply(self) -> Any:
        """Button slot wrapper for async apply."""

        coroutine = self.request_apply_async()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(coroutine)
        task = loop.create_task(coroutine)
        self._apply_task = task
        return task

    def request_discard(self) -> None:
        """Discard panel-local preview state without mutating the document."""

        if self._state == "applying":
            self._set_status(self.tr("Apply is in progress."))
            return
        self._clear_runtime_artifacts(clear_apply=True)
        self._render_artifacts()
        self._set_state("discarded")
        self._set_status(self.tr("Preview discarded."))

    def request_undo(self) -> bool:
        """Undo the latest panel apply only when it is still the history top."""

        if not self._panel_undo_available():
            self._set_status(self.tr("Panel undo is not available."))
            return False
        undo = self._history_undo_callable()
        if undo is None:
            self._set_status(self.tr("Panel undo is not available."))
            return False
        try:
            undone = bool(undo())
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            _log_panel_failure("undo", exc)
            self._set_status(self.tr("Undo failed: {error}").format(error=type(exc).__name__))
            return False
        if undone:
            self._latest_panel_apply_id = None
            self._latest_panel_apply_undo_count_before = None
            self._latest_panel_apply_undo_count_after = None
            self._last_apply_result = None
            self._last_verification = None
            self._render_artifacts()
            self._set_state("discarded")
            self._set_status(self.tr("Undo complete."))
        else:
            self._set_status(self.tr("Undo was not applied."))
        return undone

    def request_verify(self) -> dict[str, Any] | None:
        """Run read-only verification for the latest apply result."""

        if self._service is None:
            self._set_status(self.tr("MCP runtime is not available."))
            return None
        apply_result = _mapping(self._last_apply_result)
        apply_id = str(apply_result.get("applyId") or "").strip()
        if not apply_id:
            self._set_status(self.tr("Verify requires an applied edit."))
            return None
        try:
            verification = self._service.verify_canvas_edit({"applyId": apply_id})
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            reason = _reason_code(exc)
            _log_panel_failure("verify", exc, reason=reason)
            self._set_status(
                self.tr("Verify failed: {error}").format(error=reason or type(exc).__name__)
            )
            return None
        self._last_verification = dict(verification)
        self._render_artifacts()
        self._set_state("verified")
        self._set_status(self.tr("Verify complete."))
        return self._last_verification

    def refresh_translations(self) -> None:
        """Refresh static UI text for runtime language changes."""

        self._refresh_button.setText(self.tr("Refresh"))
        self._plan_button.setText(self.tr("Plan"))
        self._preview_button.setText(self.tr("Preview"))
        self._apply_button.setText(self.tr("Apply"))
        self._discard_button.setText(self.tr("Discard"))
        self._undo_button.setText(self.tr("Undo"))
        self._verify_button.setText(self.tr("Verify"))
        self._generate_candidates_button.setText(self.tr("Generate Candidates"))
        self._select_candidate_button.setText(self.tr("Select Candidate"))
        self._prompt_edit.setPlaceholderText(self.tr("Describe the canvas edit..."))

        self._context_group.setTitle(self.tr("Context"))
        self._plan_group.setTitle(self.tr("Plan"))
        self._clarification_group.setTitle(self.tr("Clarification"))
        self._blocked_group.setTitle(self.tr("Safety"))
        self._candidate_group.setTitle(self.tr("Candidates"))
        self._preview_group.setTitle(self.tr("Preview"))
        self._approval_group.setTitle(self.tr("Approval"))
        self._apply_result_group.setTitle(self.tr("Apply Result"))
        self._verify_group.setTitle(self.tr("Verify"))
        self._controls_group.setTitle(self.tr("Next"))

        labels = {
            "status": self.tr("Status"),
            "intent": self.tr("Intent"),
            "target": self.tr("Target"),
            "approval": self.tr("Approval"),
            "run": self.tr("Run"),
            "clarification_question": self.tr("Question"),
            "clarification_reason": self.tr("Reason"),
            "clarification_target": self.tr("Target"),
            "candidate_status": self.tr("Status"),
            "candidate_selected": self.tr("Selected"),
            "preview_id": self.tr("Preview"),
            "preview_mode": self.tr("Mode"),
            "preview_status": self.tr("Status"),
            "preview_mutation": self.tr("Mutation"),
            "preview_visual": self.tr("Visual"),
            "preview_thumbnails": self.tr("Thumbnails"),
            "preview_overlay": self.tr("Overlay"),
            "ticket_id": self.tr("Ticket"),
            "ticket_scope": self.tr("Scope"),
            "ticket_expiry": self.tr("Expires"),
            "apply_id": self.tr("Apply"),
            "apply_status": self.tr("Status"),
            "apply_history": self.tr("History"),
            "apply_layers": self.tr("Layers"),
            "verify_status": self.tr("Status"),
            "verify_revision": self.tr("Revision"),
        }
        for key, text in labels.items():
            label = self._field_labels.get(key)
            if label is not None:
                label.setText(text)

        self._render_context()
        self._render_plan()
        self._render_candidates()
        self._render_artifacts()

    def changeEvent(self, event: QEvent) -> None:  # pragma: no cover - Qt event passthrough
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        self._refresh_button.clicked.connect(self.refresh_context)
        toolbar.addWidget(self._refresh_button)
        toolbar.addWidget(self._status_label, 1)
        layout.addLayout(toolbar)

        self._setup_context_group()

        self._prompt_edit.setObjectName("canvasCopilotPrompt")
        self._prompt_edit.setMaximumBlockCount(80)
        self._prompt_edit.setFixedHeight(76)
        self._prompt_edit.textChanged.connect(self._on_prompt_changed)
        layout.addWidget(self._prompt_edit)

        prompt_actions = QHBoxLayout()
        prompt_actions.setContentsMargins(0, 0, 0, 0)
        self._plan_button.setObjectName("canvasCopilotPlanButton")
        self._plan_button.clicked.connect(self.request_plan)
        prompt_actions.addWidget(self._plan_button)
        prompt_actions.addStretch(1)
        layout.addLayout(prompt_actions)

        self._setup_plan_group()
        self._setup_clarification_group()
        self._setup_list_group(self._blocked_group, self._blocked_list)
        self._setup_candidate_group()
        self._setup_preview_group()
        self._setup_approval_group()
        self._setup_apply_result_group()
        self._setup_verify_group()
        self._setup_controls_group()

        layout.addWidget(self._context_group)
        layout.addWidget(self._plan_group, 1)
        layout.addWidget(self._clarification_group)
        layout.addWidget(self._blocked_group)
        layout.addWidget(self._candidate_group)
        layout.addWidget(self._preview_group)
        layout.addWidget(self._approval_group)
        layout.addWidget(self._apply_result_group)
        layout.addWidget(self._verify_group)
        layout.addWidget(self._controls_group)

    def _setup_context_group(self) -> None:
        row = QHBoxLayout(self._context_group)
        row.setContentsMargins(8, 8, 8, 8)
        row.setSpacing(6)
        for key in ("page", "layer", "selection", "visible_layers", "external"):
            label = QLabel("-", self._context_group)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            label.setProperty("canvasCopilotChip", True)
            self._chips[key] = label
            row.addWidget(label)
        row.addStretch(1)

    def _setup_plan_group(self) -> None:
        layout = QVBoxLayout(self._plan_group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        self._add_plan_row(form, "status", self._plan_status_value)
        self._add_plan_row(form, "intent", self._intent_value)
        self._add_plan_row(form, "target", self._target_value)
        self._add_plan_row(form, "approval", self._approval_value)
        self._add_plan_row(form, "run", self._run_value)
        layout.addLayout(form)

        self._steps_list.setObjectName("canvasCopilotSteps")
        self._steps_list.setMinimumHeight(92)
        layout.addWidget(self._steps_list)

    def _setup_clarification_group(self) -> None:
        layout = QVBoxLayout(self._clarification_group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        self._add_plan_row(form, "clarification_question", self._clarification_question_value)
        self._add_plan_row(form, "clarification_reason", self._clarification_reason_value)
        self._add_plan_row(form, "clarification_target", self._clarification_target_value)
        layout.addLayout(form)

        self._clarification_list.setObjectName("canvasCopilotClarifications")
        self._clarification_list.setMinimumHeight(56)
        layout.addWidget(self._clarification_list)

        options_layout = QVBoxLayout(self._clarification_options_widget)
        options_layout.setContentsMargins(0, 0, 0, 0)
        options_layout.setSpacing(4)
        layout.addWidget(self._clarification_options_widget)

    def _setup_candidate_group(self) -> None:
        layout = QVBoxLayout(self._candidate_group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        self._add_plan_row(form, "candidate_status", self._candidate_status_value)
        self._add_plan_row(form, "candidate_selected", self._candidate_selected_value)
        layout.addLayout(form)

        self._candidate_list.setObjectName("canvasCopilotCandidates")
        self._candidate_list.setMinimumHeight(82)
        self._candidate_list.itemSelectionChanged.connect(self._update_controls)
        layout.addWidget(self._candidate_list)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self._generate_candidates_button.setObjectName("canvasCopilotGenerateCandidatesButton")
        self._select_candidate_button.setObjectName("canvasCopilotSelectCandidateButton")
        self._generate_candidates_button.clicked.connect(self.request_generation_candidates)
        self._select_candidate_button.clicked.connect(lambda: self.request_select_candidate())
        row.addWidget(self._generate_candidates_button)
        row.addWidget(self._select_candidate_button)
        row.addStretch(1)
        layout.addLayout(row)

    def _setup_list_group(self, group: QGroupBox, list_widget: QListWidget) -> None:
        layout = QVBoxLayout(group)
        layout.setContentsMargins(8, 8, 8, 8)
        list_widget.setMinimumHeight(56)
        layout.addWidget(list_widget)

    def _setup_preview_group(self) -> None:
        layout = QFormLayout(self._preview_group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self._add_plan_row(layout, "preview_id", self._preview_id_value)
        self._add_plan_row(layout, "preview_mode", self._preview_mode_value)
        self._add_plan_row(layout, "preview_status", self._preview_status_value)
        self._add_plan_row(layout, "preview_mutation", self._preview_mutation_value)
        self._add_plan_row(layout, "preview_visual", self._visual_preview_value)
        self._add_plan_row(layout, "preview_thumbnails", self._thumbnail_assets_value)
        self._add_plan_row(layout, "preview_overlay", self._overlay_value)

    def _setup_approval_group(self) -> None:
        layout = QFormLayout(self._approval_group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self._add_plan_row(layout, "ticket_id", self._ticket_id_value)
        self._add_plan_row(layout, "ticket_scope", self._ticket_scope_value)
        self._add_plan_row(layout, "ticket_expiry", self._ticket_expiry_value)

    def _setup_apply_result_group(self) -> None:
        layout = QFormLayout(self._apply_result_group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self._add_plan_row(layout, "apply_id", self._apply_id_value)
        self._add_plan_row(layout, "apply_status", self._apply_status_value)
        self._add_plan_row(layout, "apply_history", self._apply_history_value)
        self._add_plan_row(layout, "apply_layers", self._apply_layers_value)

    def _setup_verify_group(self) -> None:
        layout = QFormLayout(self._verify_group)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(6)
        self._add_plan_row(layout, "verify_status", self._verify_status_value)
        self._add_plan_row(layout, "verify_revision", self._verify_revision_value)

    def _setup_controls_group(self) -> None:
        layout = QHBoxLayout(self._controls_group)
        layout.setContentsMargins(8, 8, 8, 8)
        self._preview_button.clicked.connect(self.request_preview)
        self._apply_button.clicked.connect(self.request_apply)
        self._discard_button.clicked.connect(self.request_discard)
        self._undo_button.clicked.connect(self.request_undo)
        self._verify_button.clicked.connect(self.request_verify)
        for button in (
            self._preview_button,
            self._apply_button,
            self._discard_button,
            self._undo_button,
            self._verify_button,
        ):
            button.setEnabled(False)
            layout.addWidget(button)
        layout.addStretch(1)

    def _add_plan_row(self, form: QFormLayout, key: str, value: QLabel) -> None:
        label = QLabel(self)
        self._field_labels[key] = label
        value.setTextInteractionFlags(Qt.TextSelectableByMouse)
        form.addRow(label, value)

    def _render_context(self) -> None:
        context = _mapping(self._last_context)
        page = _mapping(context.get("page"))
        active_layer = _mapping(context.get("activeLayer"))
        selection = _mapping(context.get("selection"))
        safety = _mapping(context.get("safety"))
        layers = _sequence(context.get("layers"))

        page_text = page.get("name") or page.get("pageId") or "-"
        layer_text = active_layer.get("name") or active_layer.get("layerId") or "-"
        selected = bool(selection.get("hasSelection"))
        visible_count = sum(
            1 for layer in layers if isinstance(layer, Mapping) and layer.get("visible", True)
        )
        external = bool(safety.get("externalServicesAllowed"))

        self._chips["page"].setText(self.tr("Page: {page}").format(page=_text(page_text)))
        self._chips["layer"].setText(self.tr("Layer: {layer}").format(layer=_text(layer_text)))
        self._chips["selection"].setText(
            self.tr("Selection: {state}").format(
                state=self.tr("yes") if selected else self.tr("no")
            )
        )
        self._chips["visible_layers"].setText(
            self.tr("Visible: {count}").format(count=visible_count)
        )
        self._chips["external"].setText(
            self.tr("External: {state}").format(state=self.tr("on") if external else self.tr("off"))
        )

    def _render_plan(self) -> None:
        plan = _mapping(self._last_plan)
        intent = _mapping(plan.get("intent"))
        target = _mapping(plan.get("target"))
        steps = _sequence(plan.get("steps"))
        clarification = _mapping(plan.get("clarification"))
        clarification_visible = _plan_has_unresolved_clarification(plan)
        clarifications = _sequence(plan.get("clarificationQuestions"))
        blocked_reasons = _sequence(plan.get("blockedReasons"))

        self._plan_status_value.setText(_text(plan.get("status") or "-"))
        self._intent_value.setText(
            _text(intent.get("kind") or "-")
            if not intent
            else self.tr("{kind} ({confidence})").format(
                kind=_text(intent.get("kind") or "-"),
                confidence=_text(intent.get("confidence") or "-"),
            )
        )
        self._target_value.setText(
            self.tr("Layer {layer}, selection {selection}").format(
                layer=_text(target.get("layerId") or "-"),
                selection=self.tr("yes") if target.get("hasSelection") else self.tr("no"),
            )
        )
        self._approval_value.setText(
            self.tr("required") if plan.get("requiresApprovalBeforeApply") else self.tr("none")
        )
        self._run_value.setText(_text(plan.get("runId") or "-"))

        self._steps_list.clear()
        for step in steps:
            if not isinstance(step, Mapping):
                continue
            title = step.get("title") or step.get("kind") or step.get("id")
            mutation = step.get("documentMutation") or "none"
            self._steps_list.addItem(
                self.tr("{title} - mutation: {mutation}").format(
                    title=_text(title),
                    mutation=_text(mutation),
                )
            )

        self._clarification_list.clear()
        for question in clarifications:
            if isinstance(question, Mapping):
                text = question.get("question") or question.get("reason") or question.get("id")
            else:
                text = question
            self._clarification_list.addItem(_text(text))
        if clarification:
            self._clarification_question_value.setText(_text(clarification.get("question") or "-"))
            self._clarification_reason_value.setText(_text(clarification.get("reason") or "-"))
            self._clarification_target_value.setText(
                _target_resolution_summary(_mapping(clarification.get("targetState")))
            )
            if not clarifications:
                self._clarification_list.addItem(_text(clarification.get("question") or "-"))
            self._render_clarification_options(clarification, enabled=clarification_visible)
        else:
            self._clarification_question_value.setText("-")
            self._clarification_reason_value.setText("-")
            self._clarification_target_value.setText("-")
            self._clear_clarification_buttons()
        self._clarification_group.setVisible(clarification_visible or bool(clarifications))

        self._blocked_list.clear()
        for reason in blocked_reasons:
            if isinstance(reason, Mapping):
                text = reason.get("message") or reason.get("code") or reason.get("action")
            else:
                text = reason
            self._blocked_list.addItem(_text(text))
        self._blocked_group.setVisible(bool(blocked_reasons))
        self._render_candidates()
        self._update_controls()

    def _render_clarification_options(
        self,
        clarification: Mapping[str, Any],
        *,
        enabled: bool,
    ) -> None:
        self._clear_clarification_buttons()
        layout = self._clarification_options_widget.layout()
        if layout is None:
            return
        for option in _sequence(clarification.get("options")):
            if not isinstance(option, Mapping):
                continue
            option_id = str(option.get("optionId") or "").strip()
            if not option_id:
                continue
            button = QPushButton(_text(option.get("label") or option_id), self)
            button.setObjectName(f"canvasCopilotClarificationOption_{option_id}")
            button.setToolTip(_text(option.get("description") or option.get("label") or option_id))
            button.setEnabled(enabled)
            button.clicked.connect(
                lambda _checked=False, current_id=option_id: self.request_clarification_option(
                    current_id
                )
            )
            self._clarification_option_buttons[option_id] = button
            layout.addWidget(button)

    def _clear_clarification_buttons(self) -> None:
        layout = self._clarification_options_widget.layout()
        self._clarification_option_buttons.clear()
        if layout is None:
            return
        while layout.count():
            item = layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_candidates(self) -> None:
        stack = _mapping(self._candidate_stack)
        candidates = _sequence(stack.get("candidates"))
        selected_id = str(stack.get("selectedCandidateId") or self._selected_candidate_id or "")

        self._candidate_list.clear()
        for candidate in candidates:
            if not isinstance(candidate, Mapping):
                continue
            candidate_id = str(candidate.get("candidateId") or "").strip()
            if not candidate_id:
                continue
            label = _text(candidate.get("label") or candidate_id)
            provider = _text(candidate.get("provider") or "-")
            size = _mapping(candidate.get("size"))
            width = _text(size.get("width") or "-")
            height = _text(size.get("height") or "-")
            target_hint = _text(candidate.get("targetLayerHint") or "new_layer")
            thumbnail = _text(candidate.get("thumbnailAssetId") or "-")
            text = self.tr(
                "{label} | {provider} | {width}x{height} | {target} | thumb {thumbnail}"
            ).format(
                label=label,
                provider=provider,
                width=width,
                height=height,
                target=target_hint,
                thumbnail=thumbnail,
            )
            self._candidate_list.addItem(text)
            item = self._candidate_list.item(self._candidate_list.count() - 1)
            item.setData(Qt.ItemDataRole.UserRole, candidate_id)
            if selected_id and candidate_id == selected_id:
                item.setSelected(True)
                self._candidate_list.setCurrentItem(item)

        if candidates:
            self._candidate_status_value.setText(
                self.tr("{count} candidates").format(count=len(candidates))
            )
        elif self._candidate_plan_ready():
            self._candidate_status_value.setText(self.tr("Ready to request external candidates"))
        else:
            self._candidate_status_value.setText("-")
        self._candidate_selected_value.setText(_text(selected_id or "-"))
        self._candidate_group.setVisible(self._candidate_plan_ready() or bool(candidates))
        self._update_controls()

    def _render_artifacts(self) -> None:
        preview = _mapping(self._last_preview)
        ticket = _mapping(preview.get("approvalTicket"))
        apply_result = _mapping(self._last_apply_result)
        verification = _mapping(self._last_verification)
        visual_preview = _mapping(preview.get("visualPreview"))
        thumbnail = _mapping(visual_preview.get("thumbnail"))
        overlay = _mapping(visual_preview.get("canvasOverlay"))

        self._preview_id_value.setText(_text(preview.get("previewId") or "-"))
        self._preview_mode_value.setText(_text(preview.get("mode") or "-"))
        self._preview_status_value.setText(
            self.tr("blocked") if preview.get("blocked") else self.tr("ready")
        )
        self._preview_mutation_value.setText(_text(preview.get("documentMutation") or "-"))
        self._visual_preview_value.setText(_text(visual_preview.get("visualPreviewId") or "-"))
        thumbnail_ids = [
            _text(asset_id)
            for asset_id in (
                thumbnail.get("beforeAssetId"),
                thumbnail.get("afterAssetId"),
                thumbnail.get("diffAssetId"),
            )
            if _text(asset_id)
        ]
        self._thumbnail_assets_value.setText(", ".join(thumbnail_ids) if thumbnail_ids else "-")
        self._overlay_value.setText(
            _overlay_summary(
                ghost_count=len(_sequence(overlay.get("ghostLayers"))),
                target_count=len(_sequence(overlay.get("targetHighlights"))),
                selection_count=len(_sequence(overlay.get("selectionHighlights"))),
            )
            if overlay
            else "-"
        )
        self._preview_group.setVisible(bool(preview))

        self._ticket_id_value.setText(_text(ticket.get("ticketId") or "-"))
        self._ticket_scope_value.setText(_text(ticket.get("scope") or "-"))
        self._ticket_expiry_value.setText(_text(ticket.get("expiresAt") or "-"))
        self._approval_group.setVisible(bool(ticket))

        self._apply_id_value.setText(_text(apply_result.get("applyId") or "-"))
        self._apply_status_value.setText(_text(apply_result.get("status") or "-"))
        self._apply_history_value.setText(_text(apply_result.get("historyLabel") or "-"))
        layer_ids = [
            _text(layer_id)
            for layer_id in _sequence(apply_result.get("createdLayerIds"))
            if _text(layer_id)
        ]
        self._apply_layers_value.setText(", ".join(layer_ids) if layer_ids else "-")
        self._apply_result_group.setVisible(bool(apply_result))

        self._verify_status_value.setText(_text(verification.get("status") or "-"))
        self._verify_revision_value.setText(
            _text(verification.get("currentDocumentRevision") or "-")
        )
        self._verify_group.setVisible(bool(verification))
        self._update_controls()

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)
        self._status_label.setToolTip(text)
        self._update_controls()

    def _set_state(self, state: str) -> None:
        self._state = state
        self._update_controls()

    def _on_prompt_changed(self) -> None:
        if self._state == "applying":
            return
        if self._last_plan is None and self._last_changeset is None and self._last_preview is None:
            return
        self._clear_plan_and_artifacts()
        self._set_state("idle")
        self._set_status(self.tr("Prompt changed. Plan again."))

    def _clear_plan_and_artifacts(self) -> None:
        self._last_plan = None
        self._clear_runtime_artifacts(clear_apply=True)
        self._render_plan()

    def _clear_runtime_artifacts(
        self,
        *,
        clear_apply: bool,
        clear_candidates: bool = True,
    ) -> None:
        self._last_changeset = None
        self._last_preview = None
        self._preview_presenter.clear(reason="runtime_artifacts_cleared")
        if clear_candidates:
            self._clear_candidate_stack(render=False)
        if clear_apply:
            self._last_apply_result = None
            self._last_verification = None
            self._latest_panel_apply_id = None
            self._latest_panel_apply_undo_count_before = None
            self._latest_panel_apply_undo_count_after = None
        self._render_artifacts()

    def _clear_candidate_stack(self, *, render: bool = True) -> None:
        self._candidate_stack = None
        self._selected_candidate_id = None
        self._candidate_list.clear()
        self._candidate_status_value.setText("-")
        self._candidate_selected_value.setText("-")
        self._candidate_group.setVisible(False)
        if render:
            self._render_candidates()

    def _has_ready_plan(self) -> bool:
        plan = _mapping(self._last_plan)
        if not plan:
            return False
        if _plan_has_unresolved_clarification(plan) or _sequence(plan.get("blockedReasons")):
            return False
        status = str(plan.get("status") or "").strip()
        return status in _READY_PLAN_STATES

    def _candidate_plan_ready(self) -> bool:
        return self._has_ready_plan() and _plan_intent_kind(self._last_plan) == "ai_generate"

    def _candidate_selected(self) -> bool:
        stack = _mapping(self._candidate_stack)
        return bool(str(stack.get("selectedCandidateId") or self._selected_candidate_id or ""))

    def _preview_available(self) -> bool:
        if not self._has_ready_plan():
            return False
        if self._candidate_plan_ready():
            return self._candidate_selected()
        return True

    def _changeset_arguments_for_preview(
        self,
        plan: Mapping[str, Any],
        run_id: str,
    ) -> dict[str, Any] | None:
        if self._candidate_plan_ready():
            stack = _mapping(self._candidate_stack)
            selected = str(stack.get("selectedCandidateId") or self._selected_candidate_id or "")
            if not stack or not selected:
                return None
            args: dict[str, Any] = {
                "candidateStack": stack,
                "selectedCandidateId": selected,
            }
            if run_id:
                args["runId"] = run_id
            else:
                args["plan"] = plan
            return args
        return {"runId": run_id} if run_id else {"plan": plan}

    def _candidate_source_layer_id(self) -> str | None:
        plan = _mapping(self._last_plan)
        target = _mapping(plan.get("target"))
        context = _mapping(self._last_context)
        active = _mapping(context.get("activeLayer"))
        layer_id = str(target.get("layerId") or active.get("layerId") or "").strip()
        return layer_id or None

    def _current_candidate_id(self) -> str | None:
        item = self._candidate_list.currentItem()
        if item is None:
            selected = self._candidate_list.selectedItems()
            item = selected[0] if selected else None
        if item is None:
            return None
        candidate_id = item.data(Qt.ItemDataRole.UserRole)
        text = str(candidate_id or "").strip()
        return text or None

    def _approval_ticket_matches_changeset(self) -> bool:
        changeset = _mapping(self._last_changeset)
        preview = _mapping(self._last_preview)
        ticket = _mapping(preview.get("approvalTicket"))
        changeset_id = str(changeset.get("changesetId") or "").strip()
        return bool(
            changeset_id
            and ticket.get("ticketId")
            and str(ticket.get("changesetId") or "").strip() == changeset_id
        )

    def _changeset_apply_allowed_by_panel(self) -> bool:
        changeset = _mapping(self._last_changeset)
        operations = _sequence(changeset.get("operations"))
        if not operations:
            return False
        for operation in operations:
            if not isinstance(operation, Mapping):
                return False
            op_name = str(operation.get("op") or operation.get("kind") or "").strip()
            if op_name not in _PANEL_APPLY_ALLOWED_OPERATIONS:
                return False
        return True

    def _apply_arguments(self) -> dict[str, Any] | None:
        if not self._approval_ticket_matches_changeset():
            return None
        if not self._changeset_apply_allowed_by_panel():
            return None
        changeset = _mapping(self._last_changeset)
        preview = _mapping(self._last_preview)
        ticket = _mapping(preview.get("approvalTicket"))
        changeset_id = str(changeset.get("changesetId") or "").strip()
        ticket_id = str(ticket.get("ticketId") or "").strip()
        if not changeset_id or not ticket_id:
            return None
        return {"changesetId": changeset_id, "approvalTicketId": ticket_id}

    def _update_controls(self) -> None:
        applying = self._state == "applying"
        service_available = self._service is not None
        candidate_busy = self._candidate_generation_active
        candidate_flow = self._candidate_plan_ready()
        self._refresh_button.setEnabled(not applying)
        self._plan_button.setEnabled(service_available and not applying and not candidate_busy)
        self._generate_candidates_button.setEnabled(
            service_available and not applying and not candidate_busy and candidate_flow
        )
        self._select_candidate_button.setEnabled(
            service_available
            and not applying
            and not candidate_busy
            and bool(_sequence(_mapping(self._candidate_stack).get("candidates")))
            and self._current_candidate_id() is not None
        )
        self._preview_button.setEnabled(
            service_available
            and not applying
            and not candidate_busy
            and self._preview_available()
            and self._last_preview is None
        )
        self._apply_button.setEnabled(
            service_available
            and not applying
            and not candidate_busy
            and self._apply_arguments() is not None
        )
        self._discard_button.setEnabled(
            not applying
            and not candidate_busy
            and bool(self._last_changeset or self._last_preview or self._candidate_stack)
        )
        self._undo_button.setEnabled(not applying and self._panel_undo_available())
        self._verify_button.setEnabled(
            service_available and not applying and bool(_mapping(self._last_apply_result))
        )

    def _history_undo_count(self) -> int | None:
        canvas = self._history_canvas()
        if canvas is None:
            return None
        method = getattr(canvas, "history_undo_count", None)
        if not callable(method):
            return None
        try:
            return int(method())
        except Exception:
            return None

    def _history_undo_callable(self) -> Any | None:
        canvas = self._history_canvas()
        if canvas is None:
            return None
        undo = getattr(canvas, "history_undo", None)
        return undo if callable(undo) else None

    def _history_canvas(self) -> Any | None:
        service = self._service
        if service is None:
            return None
        canvas = getattr(service, "canvas_service", None)
        return canvas if canvas is not None else getattr(service, "canvas", None)

    def _panel_undo_available(self) -> bool:
        if not self._latest_panel_apply_id:
            return False
        before = self._latest_panel_apply_undo_count_before
        after = self._latest_panel_apply_undo_count_after
        current = self._history_undo_count()
        if before is None or after is None or current is None:
            return False
        return after == before + 1 and current == after

    def _sync_canvas_observer(self) -> None:
        canvas = self._history_canvas()
        self._preview_presenter.set_canvas(canvas)
        if canvas is self._observed_canvas:
            return
        self._disconnect_canvas_observer()
        signal = getattr(canvas, "composited_changed", None)
        connect = getattr(signal, "connect", None)
        if callable(connect):
            with suppress(Exception):
                connect(self._on_canvas_maybe_changed)
                self._observed_canvas = canvas

    def _disconnect_canvas_observer(self) -> None:
        canvas = self._observed_canvas
        self._observed_canvas = None
        signal = getattr(canvas, "composited_changed", None)
        disconnect = getattr(signal, "disconnect", None)
        if callable(disconnect):
            with suppress(Exception):
                disconnect(self._on_canvas_maybe_changed)

    def _on_canvas_maybe_changed(self, *_args: Any) -> None:
        self._update_controls()

    def _present_visual_preview(self, preview: Mapping[str, Any]) -> None:
        if preview.get("blocked"):
            self._preview_presenter.clear(reason="preview_blocked")
            return
        self._preview_presenter.set_canvas(self._history_canvas())
        self._preview_presenter.present(_mapping(preview.get("visualPreview")))


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _overlay_summary(
    *,
    ghost_count: int,
    target_count: int,
    selection_count: int,
) -> str:
    return f"ghost {ghost_count}, target {target_count}, selection {selection_count}"


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    return text if text else None


def _plan_intent_kind(plan: Any) -> str:
    intent = _mapping(_mapping(plan).get("intent"))
    return str(intent.get("kind") or "").strip()


def _plan_has_unresolved_clarification(plan: Any) -> bool:
    plan_mapping = _mapping(plan)
    clarification = _mapping(plan_mapping.get("clarification"))
    if clarification:
        status = str(clarification.get("status") or "").strip()
        if status not in {"resolved", "discarded"}:
            return True
    return bool(_sequence(plan_mapping.get("clarificationQuestions")))


def _target_resolution_summary(target_resolution: Mapping[str, Any]) -> str:
    if not target_resolution:
        return "-"
    status = _text(target_resolution.get("status") or "-")
    reason = _text(target_resolution.get("reason") or "-")
    target_count = len(_sequence(target_resolution.get("candidateTargets")))
    protected_count = len(_sequence(target_resolution.get("protectedTargets")))
    missing = [
        _text(item)
        for item in _sequence(target_resolution.get("missingRequirements"))
        if _text(item)
    ]
    missing_text = ",".join(missing) if missing else "-"
    return (
        f"{status}; reason {reason}; targets {target_count}; "
        f"protected {protected_count}; missing {missing_text}"
    )


def _reason_code(exc: BaseException) -> str | None:
    reason = getattr(exc, "reason_code", None)
    if reason is None:
        return None
    text = str(reason)
    return text if text else None


def _log_panel_failure(action: str, exc: BaseException, *, reason: str | None = None) -> None:
    if reason:
        logger.warning("Canvas Copilot %s failed: %s", action, reason)
    else:
        logger.warning("Canvas Copilot %s failed: %s", action, type(exc).__name__)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value
