"""Autosave and crash recovery module.

Implements automatic saving and crash recovery functionality
following Master.md specifications.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QTimer, Signal

from loomstroke.core.event_bus import EventBus
from loomstroke.core.events import Events
from loomstroke.core.history_failure_reporting import history_failure_event_is_current
from loomstroke.core.save_outcome_claim import (
    UNMATCHABLE_CLAIM,
    PendingSaveOutcomeClaim,
    SaveOutcomeAnnouncement,
    claim_from_pending_payload,
    classify_save_outcome_announcement,
    evaluate_final_save_outcome,
)
from loomstroke.host_api import DocumentService, SaveOptions
from loomstroke.io.atomic_export_writer import write_atomically
from loomstroke.io.autosave_attempt import (
    AutosaveAttempt,
    RecoveryDocumentAttempt,
    autosave_attempt_for,
    load_recovery_attempt,
    recovery_original_path,
    release_autosave_attempt,
)
from loomstroke.io.autosave_capture_guard import capture_is_blocked, consume_deferred_autosave
from loomstroke.io.autosave_revision_tracker import AutosaveRevisionTracker, AutosaveToken
from loomstroke.io.recovery_source_pins import (
    RecoverySourcePins,
    preserve_cleanup_metadata,
    release_proven_unused_sources,
)
from loomstroke.platform.settings_manager import SettingsManager

if TYPE_CHECKING:
    from loomstroke.core.canvas import Canvas

logger = logging.getLogger(__name__)


@dataclass
class AutosaveState:
    """Autosave state information."""

    enabled: bool = True
    interval_minutes: int = 5
    keep_versions: int = 10
    last_save: datetime | None = None
    save_count: int = 0
    current_file: Path | None = None


@dataclass
class RecoveryInfo:
    """Crash recovery information."""

    session_id: str
    timestamp: datetime
    file_path: Path
    original_path: Path | None
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "session_id": self.session_id,
            "timestamp": self.timestamp.isoformat(),
            "file_path": str(self.file_path),
            "original_path": str(self.original_path) if self.original_path else None,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecoveryInfo:
        """Create from dictionary."""
        return cls(
            session_id=data["session_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            file_path=Path(data["file_path"]),
            original_path=Path(data["original_path"]) if data.get("original_path") else None,
            metadata=data.get("metadata", {}),
        )


class AutosaveManager(QObject):
    """Manages autosave and crash recovery.

    Provides automatic background saving and crash recovery
    capabilities following Master.md architecture.
    """

    autosave_started = Signal()
    autosave_completed = Signal(str)  # save_path
    autosave_failed = Signal(str)  # error_message
    recovery_available = Signal(list)  # list[RecoveryInfo]
    recovery_completed = Signal()

    def __init__(
        self,
        settings: SettingsManager | None = None,
        document_service: DocumentService | None = None,
        *,
        canvas: Canvas | None = None,
        event_bus: EventBus | None = None,
        parent: QObject | None = None,
    ) -> None:
        """Initialize autosave manager.

        Args:
            settings: Settings manager to use (optional)
            document_service: Document service used for save/load
            canvas: Canvas reference (optional, for legacy compatibility)
            event_bus: Event bus for notifications
            parent: Optional parent QObject
        """
        super().__init__(parent)

        self._canvas = canvas
        self._document_service = document_service
        self._event_bus = event_bus
        from loomstroke.platform.settings_manager import get_settings_manager  # noqa: PLC0415

        self._settings = settings or get_settings_manager()

        self._state = AutosaveState()
        # AUTOSAVE-01B: dirty/epoch state machine (pure Python) plus the
        # single strong reference to an in-flight async autosave task.
        self._revision_tracker = AutosaveRevisionTracker()
        # SAVE-ASYNC-REV-01 review-fix 2: claim recorded from a pending
        # DOC_SAVED; only a strictly matching DOC_SAVE_OUTCOME may drive
        # manual-save bookkeeping (fail-closed otherwise).
        self._pending_manual_save_claim: PendingSaveOutcomeClaim | None = None
        self._recovery_source_pins = RecoverySourcePins()
        self._autosave_task: asyncio.Task[bool] | None = None
        self._autosave_attempt: AutosaveAttempt | None = None
        self._recovery_document_attempt: RecoveryDocumentAttempt | None = None
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._perform_autosave)
        self._session_id = self._generate_session_id()
        self._autosave_dir = self._get_autosave_directory()
        self._recovery_file = self._autosave_dir / "recovery.json"

        self._load_settings()
        self._ensure_directories()

        if event_bus is not None:
            # Bug-fix (2026-04-26): the EventBus stores handlers as
            # ``weakref.ref(handler)`` (event_bus.py:73). Passing a
            # bound method like ``self._on_settings_changed`` creates a
            # fresh bound-method object on the fly with no strong
            # reference holder — Python GCs it the moment ``subscribe``
            # returns and the bus loses every handler. The user-
            # visible symptom: toggling autosave in Preferences had
            # zero effect because the SETTINGS_CHANGED subscription
            # was already dead. Same root cause as commit a25b832
            # (AI Effect EventBus). Cache the bound methods on
            # ``self`` so the references survive.
            self._handler_doc_changed = self._on_document_modified
            self._handler_doc_saved = self._on_document_saved
            self._handler_doc_save_outcome = self._on_document_save_outcome
            self._handler_settings_changed = self._on_settings_changed
            self._handler_document_reset = self._on_document_reset
            self._handler_document_loaded = self._on_document_loaded
            event_bus.subscribe(Events.DOC_CHANGED, self._handler_doc_changed)
            event_bus.subscribe(Events.DOC_SAVED, self._handler_doc_saved)
            event_bus.subscribe(Events.DOC_SAVE_OUTCOME, self._handler_doc_save_outcome)
            event_bus.subscribe(Events.SETTINGS_CHANGED, self._handler_settings_changed)
            # New document epoch boundaries (AUTOSAVE-01B).  DOC_LOADED is
            # the canonical load event; DOC_OPENED follows it on the same
            # path and is deliberately NOT subscribed (double reset).
            event_bus.subscribe(Events.DOC_CREATED, self._handler_document_reset)
            event_bus.subscribe(Events.DOC_LOADED, self._handler_document_loaded)
            event_bus.subscribe(Events.DOC_CLOSED, self._handler_document_reset)

    # ------------------------------------------------------------------
    # Public API
    def set_canvas(self, canvas: Canvas) -> None:
        """Set canvas reference."""
        self._canvas = canvas

    def set_document_service(self, document_service: DocumentService) -> None:
        """Attach the document service used for autosave operations."""
        if self._document_service is not document_service:
            self._on_document_reset(None)
        self._document_service = document_service

    def start(self) -> None:
        """Arm the periodic autosave timer (AUTOSAVE-01B).

        The timer is a fixed cadence: document edits advance the dirty
        revision but never postpone the next tick.  Re-arming with an
        unchanged cadence is a no-op and logs nothing.
        """
        if not self._state.enabled:
            return
        interval_ms = max(1, self._state.interval_minutes) * 60 * 1000
        if self._timer.isActive() and self._timer.interval() == interval_ms:
            return
        self._timer.start(interval_ms)
        logger.info("Autosave timer armed (interval: %d min)", self._state.interval_minutes)

    def stop(self) -> None:
        """Stop autosave timer."""
        if self._timer.isActive():
            self._timer.stop()
            logger.info("Autosave stopped")

    def set_enabled(self, enabled: bool) -> None:
        """Enable or disable autosave."""
        self._state.enabled = enabled
        self._settings.set("io/autosave_enabled", enabled)
        if enabled:
            self.start()
        else:
            self.stop()

    def set_interval(self, minutes: int) -> None:
        """Set autosave interval in minutes."""
        self._state.interval_minutes = max(1, minutes)
        self._settings.set("io/autosave_interval", self._state.interval_minutes * 60)
        if self._timer.isActive():
            self.stop()
            self.start()

    def force_save(self) -> bool:
        """Force one immediate autosave even when the document is clean.

        Refused (``False``) while another autosave is in flight; forced
        requests are never queued.  Returns the save result on the sync
        path and scheduling success on the async path (existing contract).
        """
        return self._perform_autosave(force=True)

    # ------------------------------------------------------------------
    # Internal operations
    def _perform_autosave(self, *, force: bool = False) -> bool:
        """Run a single autosave, preferring the async path when possible.

        This method is the QTimer callback and the body of
        :meth:`force_save`. It performs the common early exits
        (enabled / service availability) and then dispatches to either:

        - **Async path** (§12-C #6 Phase C, 2026-04-11): when the
          configured :class:`DocumentService` exposes ``save_async`` and
          a qasync event loop is running, schedule the save as a
          background task. The expensive ZIP encode stage is offloaded
          to a worker thread via :func:`asyncio.to_thread`, keeping the
          Qt main thread free to repaint during autosave. The final
          success/failure result is surfaced through the
          ``autosave_completed`` / ``autosave_failed`` signals, not the
          return value (which only reports scheduling success).
        - **Sync path** (legacy, retained for non-qasync callers /
          DocumentService implementations lacking Phase A / unit tests
          without a running event loop): block inline on
          :meth:`DocumentService.save`.

        ``autosave_started`` is emitted once, regardless of which path
        is chosen, before the actual save begins.

        AUTOSAVE-01B: this is the periodic tick body.  A clean document
        skips without building artifacts or snapshots, an in-flight run
        blocks a second one (dirty state is kept for the next tick), and
        ``force=True`` (``force_save``) runs even when clean but still
        respects the single-flight rule.
        """
        if not self._state.enabled:
            logger.debug("Autosave skipped because it is disabled")
            return False
        if self._document_service is None or capture_is_blocked(self._document_service):
            if self._document_service is None:
                logger.warning("Document service not set; autosave skipped")
            else:
                logger.debug("Autosave deferred until the current edit is finished or cancelled")
            return False
        token = self._claim_autosave_token(force=force)
        if token is None:
            return False

        # From here on the token is claimed: every failure exit must
        # release the flight so the next tick (or force_save) can retry.
        # A capture deferred after this check releases it quietly.
        try:
            original_path = self._state.current_file
            service = self._document_service
            save_path, options = self._prepare_autosave_artifacts()
            self._autosave_attempt = AutosaveAttempt(
                token, original_path, service, save_path, options
            )
        except Exception as exc:
            return self._fail_autosave(token, str(exc))
        logger.info("Autosave write started")

        # Phase C: prefer the async path when both (a) the service
        # implements save_async and (b) a qasync loop is running.
        if callable(getattr(self._document_service, "save_async", None)):
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                return self._schedule_autosave_task(loop, save_path, options, token)

        # Sync fallback.
        self.autosave_started.emit()
        return self._run_autosave_sync(save_path, options, token)

    def _claim_autosave_token(self, *, force: bool) -> AutosaveToken | None:
        """Claim the single autosave flight for this tick, or skip quietly."""
        tracker = self._revision_tracker
        if tracker.is_in_flight:
            logger.debug("Autosave tick skipped: a previous run is still in flight")
            return None
        token = tracker.begin_autosave(force=force)
        if token is None:
            logger.debug("Autosave tick skipped: document is clean")
        return token

    def _schedule_autosave_task(
        self,
        loop: asyncio.AbstractEventLoop,
        save_path: Path,
        options: SaveOptions,
        token: AutosaveToken,
    ) -> bool:
        """Create and register the single async autosave task.

        ``autosave_started`` is emitted only when scheduling succeeded — a
        failed ``create_task`` is not a started save.  The already-created
        coroutine is closed explicitly on failure so asyncio never warns
        about a never-awaited coroutine.
        """
        coroutine = self._run_autosave_async(save_path, options, token)
        attempt = autosave_attempt_for(self, save_path, options, token)
        try:
            task = loop.create_task(coroutine)
        except Exception as exc:
            coroutine.close()
            return self._fail_autosave(token, str(exc), context=" (async)", attempt=attempt)
        self._autosave_task = task
        task.add_done_callback(
            lambda done_task, autosave_token=token: self._on_autosave_task_done(
                done_task, autosave_token
            )
        )
        self.autosave_started.emit()
        return True  # Scheduled — result surfaces via signals.

    def _fail_autosave(
        self,
        token: AutosaveToken,
        message: str,
        *,
        context: str = "",
        attempt: AutosaveAttempt | None = None,
    ) -> bool:
        """Common failure exit after the flight token was claimed.

        Releases the flight for ``token`` (idempotent), reports the error
        exactly once (ERROR log + ``autosave_failed``), and keeps the
        document dirty so the next periodic tick retries.  The revision
        state itself stays in :class:`AutosaveRevisionTracker`.
        """
        self._revision_tracker.complete_failure(token)
        release_autosave_attempt(self, attempt)
        logger.error("Autosave failed%s: %s", context, message)
        self.autosave_failed.emit(message)
        return False

    def _prepare_autosave_artifacts(self) -> tuple[Path, SaveOptions]:
        """Build the per-tick autosave path and :class:`SaveOptions`.

        Extracted from :meth:`_perform_autosave` as part of §12-C #6
        Phase C (2026-04-11) so the sync and async paths can share the
        exact same path/options pair without drift.
        """
        timestamp = datetime.now()
        filename = f"autosave_{self._session_id}_{timestamp.strftime('%Y%m%d_%H%M%S')}"
        save_path = self._autosave_dir / f"{filename}.lstroke"

        options = SaveOptions(
            include_ui_layout=True,
            include_history=False,
            embed_icc=True,
            autosave=True,
            metadata={
                "session_id": self._session_id,
                "save_count": self._state.save_count + 1,
                "timestamp": timestamp.isoformat(),
            },
        )
        return save_path, options

    def _run_autosave_sync(
        self, save_path: Path, options: SaveOptions, token: AutosaveToken
    ) -> bool:
        """Execute a single autosave via the synchronous ``save`` API.

        Used as a fallback by :meth:`_perform_autosave` when no qasync
        loop is running or the DocumentService lacks ``save_async``.
        Every exit releases the revision tracker's flight for ``token``;
        only success advances coverage (failures stay dirty for the next
        periodic tick).
        """
        attempt = autosave_attempt_for(self, save_path, options, token)
        if attempt is None or not attempt.can_start(self):
            self._revision_tracker.complete_failure(token)
            release_autosave_attempt(self, attempt)
            return False
        try:
            success = attempt.service.save(save_path, options)
        except Exception as exc:
            return self._fail_autosave(token, str(exc), attempt=attempt)

        if not success:
            if consume_deferred_autosave(attempt.service, save_path):
                self._revision_tracker.complete_failure(token)
                release_autosave_attempt(self, attempt)
                return False
            return self._fail_autosave(token, "Document service reported failure", attempt=attempt)

        try:
            self._finalize_autosave_success(save_path, options)
        except Exception as exc:
            # The archive may already exist on disk, but the recovery
            # bookkeeping did not complete: stay dirty so the next
            # periodic tick saves (and registers) again.
            return self._fail_autosave(token, str(exc), attempt=attempt)
        self._revision_tracker.complete_success(token)
        release_autosave_attempt(self, attempt)
        logger.debug("Autosave write finished (revision %s covered)", token)
        return True

    async def _run_autosave_async(
        self, save_path: Path, options: SaveOptions, token: AutosaveToken
    ) -> bool:
        """Execute a single autosave via :meth:`DocumentService.save_async`.

        Scheduled by :meth:`_perform_autosave` when a qasync event loop
        is running. The ZIP encode stage is offloaded to a worker
        thread by ``DocumentServiceImpl.save_async`` via
        :func:`asyncio.to_thread`, so the Qt main thread remains
        responsive during autosave. §12-C #6 Phase C (2026-04-11).

        AUTOSAVE-01B: success/failure/exception/cancellation all release
        the revision tracker's flight for ``token``; only success
        advances coverage.  ``CancelledError`` re-raises after cleanup
        so asyncio cancellation semantics stay intact.
        """
        attempt = autosave_attempt_for(self, save_path, options, token)
        if attempt is None or not attempt.can_start(self):
            self._revision_tracker.complete_failure(token)
            release_autosave_attempt(self, attempt)
            return False
        try:
            success = bool(await attempt.service.save_async(save_path, options))
        except asyncio.CancelledError:
            self._revision_tracker.complete_failure(token)
            release_autosave_attempt(self, attempt)
            raise
        except Exception as exc:
            return self._fail_autosave(token, str(exc), context=" (async)", attempt=attempt)

        if not success:
            if consume_deferred_autosave(attempt.service, save_path):
                self._revision_tracker.complete_failure(token)
                release_autosave_attempt(self, attempt)
                return False
            return self._fail_autosave(
                token, "Document service reported failure", context=" (async)", attempt=attempt
            )

        try:
            self._finalize_autosave_success(save_path, options)
        except Exception as exc:
            # Archive written but recovery bookkeeping incomplete: stay
            # dirty so the next periodic tick saves (and registers) again.
            return self._fail_autosave(token, str(exc), context=" (async)", attempt=attempt)
        self._revision_tracker.complete_success(token)
        release_autosave_attempt(self, attempt)
        logger.debug("Autosave write finished (revision %s covered)", token)
        return True

    def _on_autosave_task_done(self, task: asyncio.Task[bool], token: AutosaveToken) -> None:
        """Release the single task reference and never swallow an outcome.

        A task cancelled BEFORE its coroutine ever ran still owns the
        flight, so the cancellation branch releases the token here
        (idempotent when the coroutine already handled its own
        ``CancelledError``); shutdown cancellations are not reported as
        save errors.  An exception that ESCAPED the coroutine's own
        handling (which reports failures itself and returns ``False``)
        would otherwise be a silent failure, so it is reported here
        exactly once — service exceptions handled inside never reach this
        branch, so nothing is double-emitted.
        """
        owns_task = self._autosave_task is task
        attempt = getattr(self, "_autosave_attempt", None) if owns_task else None
        if owns_task:
            self._autosave_task = None
        if task.cancelled():
            if owns_task:
                self._revision_tracker.complete_failure(token)
                release_autosave_attempt(self, attempt)
            return
        escaped = task.exception()
        if escaped is not None:
            if owns_task:
                self._fail_autosave(token, str(escaped), context=" (async)", attempt=attempt)
            else:
                logger.error("An earlier autosave task failed after ownership changed: %s", escaped)

    def _finalize_autosave_success(self, save_path: Path, options: SaveOptions) -> None:
        """Common post-save bookkeeping shared by sync and async paths.

        Updates last-save timestamps, emits ``autosave_completed``,
        refreshes ``recovery.json``, and cleans up stale autosave files.
        Extracted as part of §12-C #6 Phase C (2026-04-11).
        """
        self._state.last_save = datetime.now()
        self._state.save_count += 1
        self._update_recovery_info(save_path, options.metadata)
        self.autosave_completed.emit(str(save_path))
        self._cleanup_old_autosaves()

    def _update_recovery_info(
        self, save_path: Path, document_payload: Mapping[str, Any] | None
    ) -> None:
        recovery_info = RecoveryInfo(
            session_id=self._session_id,
            timestamp=datetime.now(),
            file_path=save_path,
            original_path=recovery_original_path(self, save_path),
            metadata={
                "save_count": self._state.save_count,
                "document": dict(document_payload) if document_payload is not None else {},
            },
        )

        recovery_data: list[dict[str, Any]] = []
        if self._recovery_file.exists():
            try:
                recovery_data = json.loads(self._recovery_file.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("Failed to load recovery data: %s", exc)
                raise
        if not isinstance(recovery_data, list):
            raise ValueError("Invalid recovery metadata list")

        recovery_data.append(recovery_info.to_dict())

        max_entries = self._settings.get_int("autosave.max_recovery_entries", 50)
        if max_entries > 0:
            recovery_data = self._recovery_source_pins.retain_newest(recovery_data, max_entries)

        try:
            payload = json.dumps(recovery_data, indent=2)
            write_atomically(
                self._recovery_file,
                lambda temporary: temporary.write_text(payload, encoding="utf-8"),
            )
        except (OSError, ValueError) as exc:
            logger.error("Failed to update recovery info: %s", exc)
            raise

    def check_recovery(self) -> None:
        """Check for recovery files from previous sessions and emit signal."""
        valid_entries = self._load_recovery_from_json()

        # Fallback: scan autosave directory for .lstroke files not in recovery.json
        valid_entries.extend(self._scan_autosave_files(valid_entries))

        if not valid_entries:
            return

        # Deduplicate by recovery scope. Current beta uses one document/window
        # per session, while future multi-document builds can include
        # metadata.document_id/window_id without changing the archive name.
        latest: dict[tuple[str, ...], RecoveryInfo] = {}
        for entry in valid_entries:
            key = self._recovery_dedupe_key(entry)
            prev = latest.get(key)
            if prev is None or entry.timestamp > prev.timestamp:
                latest[key] = entry
        deduped = sorted(latest.values(), key=lambda e: e.timestamp, reverse=True)

        self.recovery_available.emit(deduped)
        logger.info("Found %d recovery session(s)", len(deduped))

    def _recovery_dedupe_key(self, entry: RecoveryInfo) -> tuple[str, ...]:
        metadata = entry.metadata if isinstance(entry.metadata, dict) else {}
        document_id = metadata.get("document_id")
        window_id = metadata.get("window_id")
        if document_id or window_id:
            return (
                "scoped",
                entry.session_id,
                str(window_id or ""),
                str(document_id or ""),
            )
        return ("session", entry.session_id)

    def _load_recovery_from_json(self) -> list[RecoveryInfo]:
        """Load valid recovery entries from recovery.json."""
        if not self._recovery_file.exists():
            return []
        try:
            recovery_data = json.loads(self._recovery_file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.error("Failed to check recovery files: %s", exc)
            return []

        entries: list[RecoveryInfo] = []
        for raw in recovery_data:
            try:
                info = RecoveryInfo.from_dict(raw)
            except (TypeError, ValueError, KeyError, AttributeError):
                continue
            if info.file_path.exists() and info.session_id != self._session_id:
                entries.append(info)
        return entries

    def _scan_autosave_files(self, existing: list[RecoveryInfo]) -> list[RecoveryInfo]:
        """Scan autosave directory for .lstroke files not already tracked."""
        known_paths = {e.file_path for e in existing}
        entries: list[RecoveryInfo] = []
        try:
            for lstroke in sorted(self._autosave_dir.glob("autosave_*.lstroke")):
                if lstroke in known_paths:
                    continue
                parts = lstroke.stem.split("_", 2)
                if len(parts) < 3 or parts[1] == self._session_id:
                    continue
                stat = lstroke.stat()
                entries.append(
                    RecoveryInfo(
                        session_id=parts[1],
                        timestamp=datetime.fromtimestamp(stat.st_mtime),
                        file_path=lstroke,
                        original_path=None,
                        metadata={},
                    )
                )
        except Exception as exc:
            logger.debug("Fallback autosave scan failed: %s", exc)
        return entries

    def recover(self, recovery_info: RecoveryInfo) -> bool:
        if not recovery_info.file_path.exists():
            logger.error("Recovery file not found: %s", recovery_info.file_path)
            return False
        if self._document_service is None:
            logger.error("Document service not set; cannot recover")
            return False

        try:
            success = load_recovery_attempt(self, recovery_info)
        except Exception as exc:
            logger.error("Recovery failed: %s", exc)
            return False
        if not success:
            logger.error(
                "Recovery load failed or the document changed: %s", recovery_info.file_path
            )
            return False
        self._recovery_source_pins.mark_loaded(
            recovery_info.file_path, owner=self._document_service
        )
        logger.info("Recovered document from %s", recovery_info.file_path)
        return True

    # ------------------------------------------------------------------
    # Utility methods
    def _cleanup_old_autosaves(self) -> None:
        """Keep only the latest `keep_versions` files per session, across all sessions."""
        if self._state.keep_versions <= 0:
            return
        try:
            all_files = sorted(self._autosave_dir.glob("autosave_*.lstroke"))
            # Group by session_id
            by_session: dict[str, list[Path]] = {}
            for f in all_files:
                parts = f.stem.split("_", 2)
                if len(parts) >= 3:
                    by_session.setdefault(parts[1], []).append(f)

            for files in by_session.values():
                if len(files) > self._state.keep_versions:
                    for old in files[: -self._state.keep_versions]:
                        if not self._recovery_source_pins.is_protected(old):
                            old.unlink(missing_ok=True)
        except Exception as exc:
            logger.warning("Failed to cleanup old autosaves: %s", exc)

    def cleanup_session(self) -> None:
        """Remove all autosave files for the current session (called after manual save)."""
        try:
            pattern = f"autosave_{self._session_id}_*.lstroke"
            for file in self._autosave_dir.glob(pattern):
                if not self._recovery_source_pins.is_protected(file):
                    file.unlink(missing_ok=True)
            logger.info("Cleaned up session autosave files")
        except Exception as exc:
            logger.error("Failed to cleanup session files: %s", exc)

    def cleanup_all_old_sessions(self) -> None:
        """Remove all autosave files from previous sessions and clear recovery.json."""
        try:
            deletions = []
            for lstroke in self._autosave_dir.glob("autosave_*.lstroke"):
                parts = lstroke.stem.split("_", 2)
                if (
                    len(parts) >= 3
                    and parts[1] != self._session_id
                    and not self._recovery_source_pins.is_protected(lstroke)
                ):
                    deletions.append(lstroke)
            preserve_cleanup_metadata(self._recovery_file, deletions)
            for lstroke in deletions:
                lstroke.unlink(missing_ok=True)
            logger.info("Cleaned up all old session autosave files")
        except Exception as exc:
            logger.error("Failed to cleanup old sessions: %s", exc)

    def _get_autosave_directory(self) -> Path:
        import sys  # noqa: PLC0415

        if sys.platform == "win32":
            base = Path.home() / "AppData" / "Local" / "LoomStroke"
        elif sys.platform == "darwin":
            base = Path.home() / "Library" / "Application Support" / "LoomStroke"
        else:
            base = Path.home() / ".local" / "share" / "LoomStroke"
        return base / "autosave"

    def _ensure_directories(self) -> None:
        self._autosave_dir.mkdir(parents=True, exist_ok=True)

    def _generate_session_id(self) -> str:
        import uuid  # noqa: PLC0415

        return str(uuid.uuid4())[:8]

    def _load_settings(self) -> None:
        # Read from schema-canonical keys (io/autosave_*) with fallback
        # to legacy keys (autosave.*) for backwards compatibility.
        self._state.enabled = self._settings.get_bool(
            "io/autosave_enabled",
            self._settings.get_bool("autosave.enabled", True),
        )
        interval_sec = self._settings.get_int(
            "io/autosave_interval",
            self._settings.get_int("autosave.interval_minutes", 5) * 60,
        )
        self._state.interval_minutes = max(1, interval_sec // 60)
        self._state.keep_versions = self._settings.get_int("autosave.keep_versions", 10)

    def _on_settings_changed(self, _payload: Any) -> None:
        """Reload autosave settings when preferences are changed."""
        was_enabled = self._state.enabled
        old_interval = self._state.interval_minutes
        self._load_settings()

        if self._state.enabled != was_enabled:
            if self._state.enabled:
                self.start()
            else:
                self.stop()
                logger.info("Autosave disabled via settings")
        elif self._state.enabled and self._state.interval_minutes != old_interval:
            # Restart timer with new interval
            self.stop()
            self.start()

    def _on_document_modified(self, _payload: Any) -> None:
        """Record the edit in the dirty revision; never touch the timer.

        AUTOSAVE-01B: the periodic cadence must not be postponed by
        editing (the old stop/start debounce starved autosave during
        long sessions and logged a misleading INFO per edit).
        """
        if not history_failure_event_is_current(_payload):
            return
        token = self._revision_tracker.mark_modified()
        logger.debug("Autosave dirty revision advanced to %s", token)

    def _on_document_reset(self, _payload: Any) -> None:
        """Enter a new document epoch on create / close."""
        self._revision_tracker.reset_document()
        self._state.current_file = None
        self._pending_manual_save_claim = None
        release_proven_unused_sources(self)
        logger.debug(
            "Autosave revision tracking reset (document epoch %d)",
            self._revision_tracker.document_epoch,
        )

    def _on_document_loaded(self, payload: Any) -> None:
        data = self._coerce_payload(payload)
        source = data.get("path")
        try:
            loaded_path = Path(source) if isinstance(source, (str, Path)) and source else None
            loaded_source = loaded_path.resolve() if loaded_path is not None else None
        except (OSError, ValueError):
            loaded_path = None
            loaded_source = None
        attempt = self._recovery_document_attempt
        context = self._recovery_source_pins.current
        if (
            context is not None
            and attempt is not None
            and context is attempt.context
            and loaded_source == context.source
        ):
            if self._revision_tracker.document_epoch != attempt.epoch:
                # A nested boundary already replaced this recovery before its
                # remaining DOC_LOADED listeners were delivered.
                return
            self._on_document_reset(payload)
            self._state.current_file = context.original_path
            attempt.loaded_epoch = self._revision_tracker.document_epoch
            return
        self._on_document_reset(payload)
        self._state.current_file = loaded_path

    def _on_document_saved(self, payload: Any) -> None:
        data = self._coerce_payload(payload)
        if data.get("autosave"):
            # Recovery bookkeeping has a single owner: this manager's own
            # completion path (_finalize_autosave_success).  Also reacting
            # to the DOC_SAVED broadcast here registered every successful
            # autosave twice in recovery.json (AUTOSAVE-01A).  Coverage is
            # likewise NOT advanced here — only the completion path knows
            # which revision token the run actually captured.
            return
        # Review-fix 4: classify the announcement BEFORE any path
        # validation.  A pending/malformed announcement must be recorded
        # even when its path is missing/empty/mistyped — the old path
        # precheck silently dropped such announcements, leaving a stale
        # prior claim that a late DOC_SAVE_OUTCOME could still settle.
        # Only the legacy branch interprets the path as a save target.
        announcement = classify_save_outcome_announcement(data)
        if announcement is not SaveOutcomeAnnouncement.LEGACY:
            # SAVE-ASYNC-REV-01 review-fix: this DOC_SAVED only announces
            # the archive write; its classification fields are preliminary
            # (another DOC_SAVED handler may still edit or switch the
            # document synchronously).  The authoritative outcome arrives
            # via DOC_SAVE_OUTCOME after the broadcast — bookkeeping waits
            # for it so subscription order can never matter.  A new
            # announcement replaces any previous claim; if no valid final
            # ever arrives the save stays unsettled fail-closed (coverage
            # keeps the edits protected).
            if announcement is SaveOutcomeAnnouncement.PENDING:
                self._pending_manual_save_claim = claim_from_pending_payload(data)
            else:
                # Present-but-not-True marker (review-fix 3): a malformed
                # announcement never takes the legacy fallback; record a
                # claim no final can match so bookkeeping stays untouched.
                logger.warning(
                    "Malformed save_outcome_pending marker on DOC_SAVED; "
                    "autosave bookkeeping stays fail-closed"
                )
                self._pending_manual_save_claim = UNMATCHABLE_CLAIM
            return
        # Custom DocumentService implementations emit neither the pending
        # flag nor a DOC_SAVE_OUTCOME stage: apply their (field-less)
        # payload directly with the historical current-save defaults.
        # The path is interpreted as a save target only here; an unusable
        # legacy path is ignored without touching bookkeeping and without
        # consuming an existing claim.
        path_value = data.get("path")
        if not path_value:
            return
        try:
            save_path = Path(path_value)
        except (TypeError, ValueError, OSError):
            logger.warning(
                "Ignoring legacy DOC_SAVED with an unusable path value "
                "(type %s); autosave bookkeeping unchanged",
                type(path_value).__name__,
            )
            return
        self._apply_manual_save_outcome(
            save_path,
            same_document=bool(data.get("save_same_document", True)),
            covers_latest=bool(data.get("save_covers_latest", True)),
        )

    def _on_document_save_outcome(self, payload: Any) -> None:
        """Apply the authoritative final save outcome (SAVE-ASYNC-REV-01).

        Emitted by the persistence layer after the DOC_SAVED broadcast;
        the same outcome gates the core baseline confirmation.  Handlers
        of this event must not mutate or switch the document (undefined
        behaviour).

        Review-fix 2: the payload is validated strictly and matched
        against the pending claim (normalized path + captured epoch +
        captured revision).  Unsolicited, malformed, mismatched, or
        duplicated finals are ignored fail-closed — no coverage advance,
        no current_file change, no session cleanup.
        """
        data = self._coerce_payload(payload)
        decision = evaluate_final_save_outcome(data, self._pending_manual_save_claim)
        if not decision.accepted:
            logger.warning(
                "Ignoring DOC_SAVE_OUTCOME for autosave bookkeeping (%s)",
                decision.reason,
            )
            return
        self._pending_manual_save_claim = None
        assert decision.path is not None  # accepted implies a valid path
        self._apply_manual_save_outcome(
            Path(decision.path),
            same_document=decision.same_document,
            covers_latest=decision.covers_latest,
        )

    def _apply_manual_save_outcome(
        self,
        save_path: Path,
        *,
        same_document: bool,
        covers_latest: bool,
    ) -> None:
        """Manual-save bookkeeping shared by the legacy and outcome paths.

        A completion may describe a save of an OLDER state (edits during
        the worker encode / DOC_SAVED broadcast) or even a save of a
        PREVIOUS document (switched while saving).
        """
        # Exclusive state machine (review-fix 3): applying an outcome —
        # a validated final OR a legacy DOC_SAVED — consumes any pending
        # claim so an old DOC_SAVE_OUTCOME can never drive the
        # bookkeeping a second time.
        self._pending_manual_save_claim = None
        if not same_document:
            # A different document's completion must not retarget this
            # document's autosave bookkeeping.
            return
        if covers_latest:
            # A successful covering save covers everything up to now
            # (AUTOSAVE-01B): idle periodic ticks after a manual save skip.
            # A stale same-document save keeps autosave dirty so the edits
            # missing from the archive stay protected.
            self._revision_tracker.mark_manual_save()
        self._state.current_file = save_path
        if covers_latest:
            release_proven_unused_sources(self)
            self.cleanup_session()

    def _coerce_payload(self, payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        if hasattr(payload, "__dict__"):
            return {
                key: getattr(payload, key)
                for key in dir(payload)
                if not key.startswith("_") and not callable(getattr(payload, key))
            }
        return {}


AutoSaveManager = AutosaveManager

__all__ = ["AutoSaveManager", "AutosaveManager", "AutosaveState", "RecoveryInfo"]
