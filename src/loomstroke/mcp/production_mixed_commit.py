"""Apply a comic page and its production records as one confirmed operation."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from . import production as pct
from .comic_changeset import ComicApplyCommand, is_comic_changeset_operation
from .editing_readiness import known_document_closed
from .production_changeset_state import ChangesetState
from .production_record_changeset import (
    _write_step,
    prepare_record_operations,
    record_progress,
)
from .production_record_commit import _KINDS, _refuse, _revert_record_prefix, _steps
from .types import McpStructuredError

logger = logging.getLogger(__name__)


def publish_applied_changeset(
    state: ChangesetState, changeset: Mapping[str, Any], applied: list[dict[str, Any]]
) -> dict[str, Any]:
    stamp = datetime.now(UTC).isoformat()
    return state.write(
        {
            **changeset,
            "status": "applied",
            "appliedAt": stamp,
            "updatedAt": stamp,
            "appliedRecords": applied,
        }
    )


def _comic_apply_record(
    command: ComicApplyCommand,
    operations: Sequence[Mapping[str, Any]],
    changeset: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "kind": "comic_page",
        "recordType": "canvas_history_command",
        "recordId": changeset.get("changesetId"),
        "operationCount": len(operations),
        "createdLayerIds": command.created_layer_ids,
        "vectorObjectIds": command.vector_object_ids,
        "operationRecords": command.applied_records,
    }


def apply_comic_changeset(
    owner: Any, state: ChangesetState, changeset: dict[str, Any]
) -> dict[str, Any] | None:
    """Use the standard canvas boundary for comic-only and mixed edits."""
    operations = changeset.get("operations")
    if not isinstance(operations, list):
        return None
    comic = [row for row in operations if is_comic_changeset_operation(row)]
    if not comic:
        return None

    from loomstroke.core.canvas_service_simple import SimpleCanvasService  # noqa: PLC0415

    store, canvas = owner._production_store, owner.canvas_service
    standard_canvas = isinstance(canvas, SimpleCanvasService)
    if not standard_canvas and not any(
        isinstance(row, Mapping) and str(row.get("kind") or row.get("op")) in _KINDS
        for row in operations
    ):
        return None
    guard = state.ensure_current
    receipts = prepare_record_operations(store, operations)
    record_progress(store, receipts, restore=False, limit=0, guard=guard)
    attempted = 0
    submitting = False
    published = None
    legacy_record = None
    try:
        for index, receipt in enumerate(_steps(receipts, False), 1):
            attempted = index
            _write_step(store, receipt, restore=False, guard=guard)
        _, endpoint, total = record_progress(store, receipts, restore=False, guard=guard)
        if endpoint != total:
            _refuse("mixed_record_endpoint_unconfirmed")
        submitting = True
        if owner.canvas_service is not canvas or known_document_closed(owner):
            _refuse("mixed_comic_canvas_changed")
        if standard_canvas:
            published = _submit_standard_comic(owner, canvas, state, changeset, comic, receipts)
        else:
            legacy_record = owner._apply_comic_changeset_operations(comic, changeset)
    except Exception as exc:
        try:
            _revert_record_prefix(state, store, receipts, restore=False, limit=attempted)
        except Exception:
            _refuse("mixed_record_recovery_unconfirmed", pending=True)
        if submitting and isinstance(exc, McpStructuredError):
            raise
        reason = "mixed_comic_apply_failed" if submitting else "mixed_record_write_failed"
        _refuse(reason, pending=True)
    if legacy_record is not None:
        published = publish_applied_changeset(state, changeset, [legacy_record, *receipts])
    assert published is not None
    return pct.pct_response(
        {"changeset": published, "appliedRecords": published["appliedRecords"]},
        requires_confirmation=True,
    )


def _submit_standard_comic(  # noqa: PLR0913 - one prepared comic application
    owner, canvas, state, changeset, operations, receipts
):
    command = ComicApplyCommand(operations)
    published = None

    def finalize() -> None:
        nonlocal published
        applied = [_comic_apply_record(command, operations, changeset), *receipts]
        published = publish_applied_changeset(state, changeset, applied)

    command._finalize_initial_vector_edit = finalize
    try:
        owner._submit_comic_changeset_command(canvas, command, changeset)
    except Exception:
        if published is None:
            raise
        # A display/notification failure cannot undo a confirmed final write.
        logger.exception("Comic changeset notification failed after commitment")
    finally:
        command._finalize_initial_vector_edit = None
    if published is None:
        _refuse("comic_finalization_unconfirmed", pending=True)
    return published
