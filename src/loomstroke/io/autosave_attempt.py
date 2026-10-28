"""Runtime document ownership for autosave and recovery attempts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loomstroke.host_api import LoadOptions, SaveOptions

from .autosave_revision_tracker import AutosaveToken
from .recovery_source_pins import RecoveryLoadContext


@dataclass(frozen=True, slots=True)
class AutosaveAttempt:
    token: AutosaveToken
    original_path: Path | None
    service: Any
    save_path: Path
    options: SaveOptions

    def can_start(self, manager: Any) -> bool:
        return (
            manager._revision_tracker.document_epoch == self.token[0]
            and manager._document_service is self.service
        )


@dataclass(slots=True)
class RecoveryDocumentAttempt:
    context: RecoveryLoadContext
    epoch: int
    service: Any
    loaded_epoch: int | None = None

    def is_current(self, manager: Any) -> bool:
        return (
            self.loaded_epoch is not None
            and manager._revision_tracker.document_epoch == self.loaded_epoch
            and manager._document_service is self.service
        )


def autosave_attempt_for(
    manager: Any, save_path: Path, options: SaveOptions, token: AutosaveToken
) -> AutosaveAttempt | None:
    attempt = getattr(manager, "_autosave_attempt", None)
    if (
        isinstance(attempt, AutosaveAttempt)
        and attempt.options is options
        and attempt.token == token
        and attempt.save_path == save_path
    ):
        return attempt
    return None


def release_autosave_attempt(manager: Any, attempt: AutosaveAttempt | None) -> None:
    if attempt is not None and getattr(manager, "_autosave_attempt", None) is attempt:
        manager._autosave_attempt = None


def recovery_original_path(manager: Any, save_path: Path) -> Path | None:
    attempt = getattr(manager, "_autosave_attempt", None)
    if isinstance(attempt, AutosaveAttempt) and attempt.save_path == save_path:
        return attempt.original_path
    # Preserve direct/private callers without an accepted autosave reservation.
    return manager._state.current_file


def load_recovery_attempt(manager: Any, recovery_info: Any) -> bool:
    """Keep recovery ownership valid through loading and completion listeners."""
    service = manager._document_service
    previous = manager._recovery_document_attempt
    try:
        with manager._recovery_source_pins.loading(
            recovery_info.file_path, recovery_info.original_path, owner=service
        ) as context:
            attempt = RecoveryDocumentAttempt(
                context, manager._revision_tracker.document_epoch, service
            )
            manager._recovery_document_attempt = attempt
            success = service.load(
                recovery_info.file_path,
                LoadOptions(apply_ui_layout=True, strict=True, validate_manifest=True),
            )
            if not success or manager._document_service is not service:
                return False
            if manager._revision_tracker.document_epoch == attempt.epoch:
                # Legacy/custom load implementations may emit no lifecycle event.
                manager._on_document_reset(None)
                manager._state.current_file = recovery_info.original_path
                attempt.loaded_epoch = manager._revision_tracker.document_epoch
            if not attempt.is_current(manager):
                return False
            manager.recovery_completed.emit()
            return attempt.is_current(manager)
    finally:
        manager._recovery_document_attempt = previous
