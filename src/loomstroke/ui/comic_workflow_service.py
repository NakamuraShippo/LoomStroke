"""Comic Production Workspace coordinator (CT-22): local plan, validation, page plan, apply.

Everything here is local and deterministic: book plan from the comic project,
single-page plan through the existing ``plan_comic_page``, apply preview through
``build_comic_changeset_operations`` and one ``ComicApplyCommand`` per page
(revision guarded, explicit confirmation).  Generation stays with the
ProductionRecipe runner (not started here).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from loomstroke.core.comic_workflow import (
    ComicApplyPlan,
    ComicBookPlan,
    ComicBookPlanner,
    ComicBookPlanValidator,
    ComicProductionCheckpoint,
    ComicWorkflowIssue,
)

__all__ = ["ComicWorkflowService"]


class ComicWorkflowService:
    def __init__(self, host: Any, project_service: Any) -> None:
        self._host = host
        self._projects = project_service
        self._planner = ComicBookPlanner()
        self._validator = ComicBookPlanValidator()
        self._checkpoints: dict[str, ComicProductionCheckpoint] = {}
        self._plans: dict[str, ComicBookPlan] = {}

    # -- projects / plan ---------------------------------------------------------------

    def list_projects(self) -> tuple[tuple[str, str], ...]:
        try:
            return tuple(self._projects.list_projects())
        except Exception:
            return ()

    def snapshot(self, project_id: str) -> Any | None:
        try:
            return self._projects.open(project_id)
        except Exception:
            return None

    def plan_book(self, project_id: str) -> ComicBookPlan | None:
        snapshot = self.snapshot(project_id)
        if snapshot is None:
            return None
        plan = self._planner.plan_from_snapshot(snapshot)
        self._plans[project_id] = plan
        checkpoint = self._checkpoints.get(project_id) or ComicProductionCheckpoint(
            f"comic-{project_id}"
        )
        if checkpoint.can_run("script_import"):
            checkpoint = checkpoint.advance("script_import")
        if checkpoint.can_run("book_plan"):
            checkpoint = checkpoint.advance("book_plan")
        self._checkpoints[project_id] = checkpoint
        return plan

    def current_plan(self, project_id: str) -> ComicBookPlan | None:
        return self._plans.get(project_id)

    def validate(self, project_id: str) -> tuple[ComicWorkflowIssue, ...]:
        plan = self._plans.get(project_id)
        if plan is None:
            return (ComicWorkflowIssue("book_plan_missing", "plan"),)
        issues = self._validator.validate(plan)
        checkpoint = self._checkpoints.get(project_id)
        if checkpoint is not None and checkpoint.can_run("plan_validation"):
            if self._validator.is_blocked(issues):
                checkpoint = checkpoint.fail("plan_validation", retryable=True)
            else:
                checkpoint = checkpoint.advance("plan_validation")
            self._checkpoints[project_id] = checkpoint
        return issues

    def checkpoint(self, project_id: str) -> ComicProductionCheckpoint | None:
        return self._checkpoints.get(project_id)

    # -- per page ----------------------------------------------------------------------

    def page_script(self, project_id: str, entry_id: str) -> str:
        snapshot = self.snapshot(project_id)
        return self._planner.page_script(snapshot, entry_id) if snapshot is not None else ""

    def plan_page(
        self, project_id: str, entry_id: str, *, page: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        """Local deterministic single-page plan (no generation, no external calls)."""
        from loomstroke.mcp.comic_plan import plan_comic_page  # noqa: PLC0415

        script = self.page_script(project_id, entry_id)
        if not script:
            return {"status": "blocked", "blockedReasonCodes": ["page_script_empty"], "plan": None}
        arguments: dict[str, Any] = {"script": script}
        if page:
            arguments["page"] = dict(page)
        else:
            arguments["page"] = self._default_page()
        try:
            result = plan_comic_page(arguments)
        except Exception as error:
            return {
                "status": "failed",
                "blockedReasonCodes": ["page_plan_failed"],
                "errorType": type(error).__name__,
                "plan": None,
            }
        return {
            "status": "planned",
            "plan": result.get("plan"),
            "warnings": list(result.get("warnings") or []),
            "validation": result.get("validation"),
        }

    def plan_apply(
        self, project_id: str, entry_id: str, page_plan: Mapping[str, Any] | None
    ) -> ComicApplyPlan:
        from loomstroke.mcp.comic_changeset import build_comic_changeset_operations  # noqa: PLC0415

        book = self._plans.get(project_id)
        page = book.page(entry_id) if book is not None else None
        revision = self.document_revision()
        if page_plan is None:
            return ComicApplyPlan(
                entry_id,
                page.page_id if page else None,
                revision,
                "",
                {},
                0,
                (),
                ("page_plan_missing",),
            )
        changeset = build_comic_changeset_operations(page_plan)
        operations = list(changeset.get("operations") or [])
        digest = hashlib.sha256(
            json.dumps(operations, sort_keys=True, default=str).encode("utf-8")
        ).hexdigest()[:16]
        blocked: list[str] = []
        if page is not None and not page.is_linked:
            blocked.append("page_document_unlinked")
        if page is not None and page.page_id and page.page_id != self._active_page_id():
            blocked.append("page_not_active")
        return ComicApplyPlan(
            entry_id=entry_id,
            page_id=page.page_id if page else None,
            base_document_revision=revision,
            changeset_id=digest,
            operation_summary=dict(changeset.get("summary") or {}),
            operation_count=len(operations),
            warnings=tuple(changeset.get("warnings") or ()),
            blocked_reason_codes=tuple(blocked),
        )

    def apply_page(
        self,
        project_id: str,
        plan: ComicApplyPlan,
        page_plan: Mapping[str, Any],
        *,
        confirmed: bool,
    ) -> dict[str, Any]:
        """One ``ComicApplyCommand`` (= one undo) after explicit confirmation + revision check."""
        from loomstroke.mcp.comic_changeset import (  # noqa: PLC0415
            ComicApplyCommand,
            build_comic_changeset_operations,
        )

        if not confirmed:
            return {"status": "blocked", "blockedReasonCodes": ["confirmation_required"]}
        if not plan.is_ready:
            return {"status": "blocked", "blockedReasonCodes": list(plan.blocked_reason_codes)}
        if not plan.revision_matches(self.document_revision()):
            return {"status": "blocked", "blockedReasonCodes": ["document_revision_mismatch"]}
        canvas = getattr(self._host, "canvas", None)
        if canvas is None:
            return {"status": "blocked", "blockedReasonCodes": ["canvas_missing"]}
        operations = list(build_comic_changeset_operations(page_plan).get("operations") or [])
        command = ComicApplyCommand(operations)
        begin = getattr(canvas, "begin_transaction", None)
        if callable(begin):
            begin("Apply Comic Page")
        try:
            ok = bool(canvas.submit(command))
        finally:
            end = getattr(canvas, "end_transaction", None)
            if callable(end):
                end()
        if not ok:
            return {"status": "failed", "blockedReasonCodes": ["comic_apply_rejected"]}
        book = self._plans.get(project_id)
        if book is not None:
            page = book.page(plan.entry_id)
            if page is not None:
                self._plans[project_id] = book.with_page(page.with_status("applied"))
        return {
            "status": "applied",
            "changesetId": plan.changeset_id,
            "createdLayerIds": list(command.created_layer_ids),
            "vectorObjectIds": list(command.vector_object_ids),
            "operationCount": len(operations),
        }

    # -- host helpers ------------------------------------------------------------------

    def document_revision(self) -> str:
        document = getattr(self._host, "document", None)
        for name in ("revision_tracker", "_revision_tracker"):
            tracker = getattr(document, name, None)
            current = getattr(tracker, "current_revision", None)
            if callable(current):
                try:
                    return str(current())
                except Exception:
                    break
        return "unknown"

    def _active_page_id(self) -> str | None:
        pages = getattr(self._host, "pages", None)
        getter = getattr(pages, "get_active_page_id", None)
        try:
            value = getter() if callable(getter) else None
        except Exception:
            value = None
        return str(value) if value else None

    def _default_page(self) -> dict[str, Any]:
        canvas = getattr(self._host, "canvas", None)
        width = int(getattr(canvas, "_width", 0) or 0) or 1654
        height = int(getattr(canvas, "_height", 0) or 0) or 2339
        return {"width": width, "height": height}
