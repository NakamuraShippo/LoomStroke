"""Persistence ownership for :mod:`loomstroke.core.document_service`."""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from ..host_api import LoadOptions, ProgressCallback, SaveOptions
from ..io.file_io import FileIO
from ..io.layer_io import DocumentMetadata, DocumentSnapshot, LayerIO, PageSnapshot
from .document_capture_guard import DocumentCaptureDeferred
from .document_revision_tracker import DocumentRevisionOutcome, DocumentRevisionToken
from .document_snapshot_builder import DocumentSnapshotBuilder, DocumentSnapshotBuildResult
from .events import (
    DocumentIoProgressEvent,
    Events,
    build_error_payload,
    classify_error_reason,
)
from .page_residency import PageResidencyLease
from .save_worker_boundary import complete_save_worker
from .saved_page_source import SavedArchiveReceipt, SavedPageSource, file_identity

if TYPE_CHECKING:
    from .document_service import DocumentServiceImpl


class DocumentPersistenceService:
    """Owns document save/load I/O, persistence dependencies, and save guard."""

    def __init__(
        self,
        owner: DocumentServiceImpl,
        layer_io: LayerIO,
        file_io: FileIO,
    ) -> None:
        self._owner = owner
        self._layer_io = layer_io
        self._file_io = file_io
        self._save_in_progress = False
        self._save_attempt: object | None = None
        self._deferred_autosave: tuple[object, Path, int] | None = None

    def consume_deferred_autosave(self, path: str | Path) -> bool:
        """Consume only the deferred result of this instance's latest save attempt."""
        deferred = self._deferred_autosave
        if deferred is None:
            return False
        attempt, target, epoch = deferred
        if (
            attempt is not self._save_attempt
            or epoch != self._owner._capture_document_revision()[0]
        ):
            self._deferred_autosave = None
            return False
        if Path(path) != target:
            return False
        self._deferred_autosave = None
        return True

    def reset_deferred_autosave(self) -> None:
        self._deferred_autosave = None

    def _defer_save_capture(
        self,
        target: Path | None,
        options: SaveOptions | None,
        metadata: DocumentMetadata | None,
        error: DocumentCaptureDeferred,
    ) -> bool:
        if options is not None and options.autosave and target is not None:
            assert self._save_attempt is not None
            self._deferred_autosave = (
                self._save_attempt,
                target,
                self._owner._capture_document_revision()[0],
            )
        else:
            self.emit_save_failure(target, options, metadata, error)
        return False

    @property
    def layer_io(self) -> LayerIO:
        return self._layer_io

    @layer_io.setter
    def layer_io(self, value: LayerIO) -> None:
        self._layer_io = value

    @property
    def file_io(self) -> FileIO:
        return self._file_io

    @file_io.setter
    def file_io(self, value: FileIO) -> None:
        self._file_io = value

    @property
    def save_in_progress(self) -> bool:
        return self._save_in_progress

    @save_in_progress.setter
    def save_in_progress(self, value: bool) -> None:
        self._save_in_progress = bool(value)

    def save(
        self,
        path: str | Path,
        options: SaveOptions | None = None,
        *,
        progress: ProgressCallback | None = None,
    ) -> bool:
        owner = self._owner
        if self._save_in_progress:
            owner._logger.warning(
                "Document save skipped: another save is already in progress"
            )  # noqa: SLF001
            return False

        opts = options
        target: Path | None = None
        temp_path: Path | None = None
        metadata: DocumentMetadata | None = None
        residency: PageResidencyLease | None = None
        write_started = False
        existing_temporary: set[Path] = set()

        self._save_in_progress = True
        self._save_attempt = object()
        self._deferred_autosave = None
        try:
            opts = opts or SaveOptions()
            target, temp_path = self.compute_save_paths(path)
            existing_temporary = {
                candidate
                for candidate in self.save_temporary_candidates(temp_path)
                if candidate.exists()
            }
            metadata = owner._prepare_save_metadata(opts)  # noqa: SLF001
            self.emit_progress(0.0, "gather", target, progress)
            built = DocumentSnapshotBuilder(
                owner._canvas,  # noqa: SLF001
                owner._prepare_save_pages,  # noqa: SLF001
            ).build(metadata)
            snapshot = built.snapshot
            residency = self._hold_page_residency()
            # SAVE-ASYNC-REV-01: freeze the revision the snapshot covers
            # (right after build, before any worker handoff).
            token = owner._capture_document_revision()  # noqa: SLF001

            write_started = True
            receipt = self.commit_save_snapshot(temp_path, target, snapshot)
            candidate = self._prepare_saved_page_source(receipt, opts)
            self.emit_progress(1.0, "done", target, progress)
            outcome = owner._classify_document_revision(token)  # noqa: SLF001
            self.emit_save_success(target, opts, metadata, outcome)
            return self._finalize_save_outcome(
                target, opts, metadata, built, token, candidate, residency
            )

        except DocumentCaptureDeferred as exc:
            return self._defer_save_capture(target, opts, metadata, exc)
        except Exception as exc:
            owner._logger.exception("Document save failed: %s", exc)  # noqa: SLF001
            if write_started and residency is not None:
                residency.preserve()
            if temp_path is not None and metadata is not None:
                self.cleanup_save_temporary_files(temp_path, preserve=existing_temporary)
            self.emit_save_failure(target, opts, metadata, exc)
            return False

        finally:
            if residency is not None:
                residency.release()
            self._save_in_progress = False

    async def save_async(
        self,
        path: str | Path,
        options: SaveOptions | None = None,
        *,
        progress: ProgressCallback | None = None,
    ) -> bool:
        owner = self._owner
        if self._save_in_progress:
            owner._logger.warning(  # noqa: SLF001
                "Document save_async skipped: another save is already in progress"
            )
            return False

        opts = options
        target: Path | None = None
        temp_path: Path | None = None
        metadata: DocumentMetadata | None = None
        residency: PageResidencyLease | None = None
        write_started = False
        existing_temporary: set[Path] = set()

        self._save_in_progress = True
        self._save_attempt = object()
        self._deferred_autosave = None
        try:
            opts = opts or SaveOptions()
            target, temp_path = self.compute_save_paths(path)
            existing_temporary = {
                candidate
                for candidate in self.save_temporary_candidates(temp_path)
                if candidate.exists()
            }
            metadata = owner._prepare_save_metadata(opts)  # noqa: SLF001
            self.emit_progress(0.0, "gather", target, progress)
            built = DocumentSnapshotBuilder(
                owner._canvas,  # noqa: SLF001
                owner._prepare_save_pages,  # noqa: SLF001
            ).build(metadata)
            snapshot = built.snapshot
            residency = self._hold_page_residency()
            # SAVE-ASYNC-REV-01: freeze the revision the snapshot covers
            # (right after build, before the worker encode starts).  Edits
            # or document switches during the await advance the tracker
            # past this token.
            token = owner._capture_document_revision()  # noqa: SLF001

            self.emit_progress(0.3, "encode", target, progress)
            write_started = True
            receipt = await complete_save_worker(
                self.commit_save_snapshot, temp_path, target, snapshot, logger=owner._logger
            )
            candidate = self._prepare_saved_page_source(receipt, opts)
            self.emit_progress(1.0, "done", target, progress)
            outcome = owner._classify_document_revision(token)  # noqa: SLF001
            self.emit_save_success(target, opts, metadata, outcome)
            return self._finalize_save_outcome(
                target, opts, metadata, built, token, candidate, residency
            )

        except DocumentCaptureDeferred as exc:
            return self._defer_save_capture(target, opts, metadata, exc)
        except asyncio.CancelledError:
            # The worker has terminated before cancellation reaches here.
            # Its output was not confirmed, so keep the old live pages.
            if write_started and residency is not None:
                residency.preserve()
            if temp_path is not None and metadata is not None:
                self.cleanup_save_temporary_files(temp_path, preserve=existing_temporary)
            raise
        except Exception as exc:
            owner._logger.exception("Document save_async failed: %s", exc)  # noqa: SLF001
            if write_started and residency is not None:
                residency.preserve()
            if temp_path is not None and metadata is not None:
                self.cleanup_save_temporary_files(temp_path, preserve=existing_temporary)
            self.emit_save_failure(target, opts, metadata, exc)
            return False

        finally:
            if residency is not None:
                residency.release()
            self._save_in_progress = False

    def load(
        self,
        path: str | Path,
        options: LoadOptions | None = None,
        *,
        progress: ProgressCallback | None = None,
    ) -> bool:
        owner = self._owner
        opts = options or LoadOptions()
        source = Path(path)

        try:
            from .layer_opacity_preview_boundary import _close_opacity_before_edit  # noqa: PLC0415

            if not _close_opacity_before_edit(owner._canvas):
                raise RuntimeError("Opacity preview could not be restored before document load")
            self.emit_progress(0.0, "read", source, progress)
            lazy = None
            if owner._page_service is not None:  # noqa: SLF001
                # CT-08 V2: open with the page directory + only the first
                # page's content; everything else hydrates on demand.  Any
                # inconsistency in the lazy path falls back to the proven
                # eager load (spec 08 §14) — never a silently different
                # document.
                try:
                    lazy = self._assemble_lazy_snapshot(source, opts)
                except Exception:
                    owner._logger.warning(  # noqa: SLF001
                        "lazy document open failed; falling back to eager load",
                        exc_info=True,
                    )
                    lazy = None
            if lazy is not None:
                snapshot, unloaded_page_ids = lazy
            else:
                snapshot = self._layer_io.load_document_snapshot(
                    source,
                    validate_manifest=opts.validate_manifest,
                    strict=opts.strict,
                )
                unloaded_page_ids = ()

            if not snapshot.pages:
                raise ValueError("document contains no pages")

            active_page = snapshot.pages[0]
            self.emit_progress(0.3, "prepare_canvas", source, progress)

            if owner._page_service is not None:  # noqa: SLF001
                owner._page_service.load_document(  # noqa: SLF001
                    snapshot.metadata,
                    snapshot.pages,
                    active_page_id=active_page.id,
                    unloaded_page_ids=unloaded_page_ids,
                )
                self._bind_hydration_port(source, opts, bool(unloaded_page_ids))
                # Saved thumbnails reach the Pages panel immediately;
                # without this every non-active page stays a placeholder
                # until it is first activated (2026-08-04 field report).
                seed = getattr(owner._page_service, "seed_page_thumbnails", None)  # noqa: SLF001
                if callable(seed):
                    seed(snapshot.page_thumbnail_pngs)
                owner._loaded_metadata = snapshot.metadata  # noqa: SLF001
            else:
                page_metadata = owner._metadata_for_page(  # noqa: SLF001
                    snapshot.metadata,
                    active_page,
                )
                owner._apply_to_canvas(active_page.layers, page_metadata)  # noqa: SLF001
                owner._document_state = owner._make_document_state(  # noqa: SLF001
                    snapshot.metadata,
                    list(snapshot.pages),
                    active_page.id,
                )
                owner._reset_history_state(reason="load")  # noqa: SLF001
            # CT-09: the embedded ICC profile becomes the working profile so
            # save round-trips it and export can embed it.
            owner._metadata_service.adopt_loaded_color_profile(snapshot.metadata)  # noqa: SLF001

            self.emit_progress(0.7, "ui_layout", source, progress)
            if opts.apply_ui_layout:
                owner._apply_ui_layout(
                    snapshot.metadata.ui_layout, clear_missing=opts.strict
                )  # noqa: SLF001

            self.emit_progress(1.0, "done", source, progress)
            payload = {
                "path": str(source),
                "document": owner._metadata_to_payload(snapshot.metadata),  # noqa: SLF001
                "strict": bool(opts.strict),
                "validate_manifest": bool(opts.validate_manifest),
            }
            owner._event_bus.emit(Events.DOC_LOADED, payload)  # noqa: SLF001
            owner._event_bus.emit(Events.DOC_OPENED, payload)  # noqa: SLF001
            return True

        except Exception as exc:
            owner._logger.exception("Document load failed: %s", exc)  # noqa: SLF001
            error_payload = build_error_payload(
                path=str(source),
                strict=bool(opts.strict),
                validate_manifest=bool(opts.validate_manifest),
                reason_code=classify_error_reason("document_load_failed", exc),
                user_visible_message="Failed to load document",
                error=exc,
                debug_details_without_secret={
                    "path": str(source),
                    "strict": bool(opts.strict),
                    "validate_manifest": bool(opts.validate_manifest),
                    "exception_class": type(exc).__name__,
                },
            )
            owner._event_bus.emit(Events.DOC_LOAD_FAILED, error_payload)  # noqa: SLF001
            return False

    def _assemble_lazy_snapshot(
        self, source: Path, opts: LoadOptions
    ) -> tuple[DocumentSnapshot, tuple[str, ...]] | None:
        """CT-08 V2: directory + first page only; None means "use eager".

        Placeholders carry every manifest-level field (name, index,
        canvas, metadata, view_state, thumbnail member) so summaries,
        roles, and folio resolution work without hydration; only their
        ``layers`` stay empty until the hydration port fills them.
        """
        metadata, entries = self._layer_io.read_page_directory(
            source, validate_manifest=opts.validate_manifest, strict=opts.strict
        )
        if not entries:
            return None
        active_entry = entries[0]
        active_page = self._layer_io.load_single_page_snapshot(
            source, active_entry.page_id, strict=opts.strict
        )
        if active_page is None:
            return None
        pages: list[PageSnapshot] = []
        unloaded: list[str] = []
        for entry in entries:
            if entry.page_id == active_entry.page_id:
                pages.append(active_page)
                continue
            pages.append(
                PageSnapshot(
                    id=entry.page_id,
                    name=entry.name,
                    index=entry.index,
                    layers=[],
                    canvas=entry.canvas,
                    page_thumbnail=entry.thumbnail_member,
                    metadata=dict(entry.metadata) if entry.metadata else None,
                    view_state=dict(entry.view_state) if entry.view_state else None,
                )
            )
            unloaded.append(entry.page_id)
        thumbnails = self._layer_io.read_page_thumbnail_pngs(source, strict=opts.strict)
        return (
            DocumentSnapshot(metadata=metadata, pages=pages, page_thumbnail_pngs=thumbnails),
            tuple(unloaded),
        )

    def _bind_hydration_port(self, source: Path, opts: LoadOptions, needed: bool) -> None:
        """Bind the single-page loader with an external-change token.

        The token (file identity, size, mtime) freezes at open; a source file replaced
        or edited outside the app makes every later hydration fail loudly
        instead of decoding mismatched content (spec 08 §9.1).
        """
        owner = self._owner
        bind = getattr(owner._page_service, "_bind_page_hydration_port", None)  # noqa: SLF001
        if not callable(bind):
            return
        if not needed:
            bind(None)
            return
        receipt = SavedArchiveReceipt(source.resolve(), file_identity(source))
        bind(SavedPageSource(receipt, self._layer_io, bool(opts.strict)), source=source)

    def _hold_page_residency(self) -> PageResidencyLease | None:
        hold = getattr(self._owner._page_service, "_hold_page_residency", None)  # noqa: SLF001
        lease = hold() if callable(hold) else None
        return lease if isinstance(lease, PageResidencyLease) else None

    def _prepare_saved_page_source(
        self, receipt: SavedArchiveReceipt, options: SaveOptions
    ) -> SavedPageSource | None:
        if options.autosave:
            return None
        receipt.assert_current()
        return SavedPageSource(receipt, self._layer_io)

    def _bind_saved_page_source(
        self, source: SavedPageSource, residency: PageResidencyLease | None
    ) -> None:
        source.receipt.assert_current()
        if residency is None:
            return
        pages = self._owner._page_service  # noqa: SLF001
        if pages._page_residency is not residency.guard:
            raise RuntimeError("The page document changed before its saved source was confirmed")
        old_loader, old_source = pages._page_hydration_loader, pages._page_hydration_source
        try:
            pages._bind_page_hydration_port(source, source=source.path)
        except Exception:
            if pages._page_residency is residency.guard:
                pages._page_hydration_loader = old_loader
                pages._page_hydration_source = old_source
            raise

    def _finalize_save_outcome(
        self,
        target: Path,
        options: SaveOptions,
        metadata: DocumentMetadata,
        built: DocumentSnapshotBuildResult,
        token: DocumentRevisionToken,
        source: SavedPageSource | None,
        residency: PageResidencyLease | None,
    ) -> bool:
        """Single owner of the authoritative final save outcome.

        DOC_SAVED is the archive-write notification: its handlers may
        synchronously edit or even switch the document, so the outcome
        carried in that payload is only *preliminary*.  The token is
        re-classified after that broadcast. A current snapshot can bind
        its new source and confirm the baseline while pages stay resident.
        Baseline notifications can themselves edit/switch the document, so
        the reported final is classified again after those callbacks.

        Mutating or switching the document from a DOC_SAVE_OUTCOME
        handler is undefined behaviour (forbidden): the outcome is final
        by definition and will not be recomputed again.
        """

        if options.autosave:
            # AUTOSAVE-01A: autosave never confirms state and gets no
            # outcome stage (save_outcome_pending is False on its payload).
            paths = self._owner._recovery_sources_in_use()  # noqa: SLF001
            if residency is not None and (paths is None or target.resolve() in paths):
                # An unusual caller may autosave over its own lazy source.
                # Autosave never rebinds or confirms that source.
                residency.preserve()
            return True
        final = self._owner._classify_document_revision(token)  # noqa: SLF001
        if final.covers_latest:
            try:
                assert source is not None
                self._bind_saved_page_source(source, residency)
                eligible = self._owner._classify_document_revision(token)  # noqa: SLF001
                self._commit_saved_document_state(options, metadata, built, eligible)
                final = self._owner._classify_document_revision(token)  # noqa: SLF001
                if final.same_document:
                    source.receipt.assert_current()
            except Exception as exc:
                if residency is not None:
                    residency.preserve()
                final = self._owner._classify_document_revision(token)  # noqa: SLF001
                self.emit_save_outcome(target, options, replace(final, covers_latest=False))
                self.emit_save_failure(target, options, metadata, exc)
                return False
        if residency is not None:
            if final.covers_latest:
                residency.rebound()
                residency.release()
                pages = self._owner._page_service  # noqa: SLF001
                if pages._page_residency is residency.guard:
                    try:
                        pages.evict_pages_to_budget()
                    except Exception:
                        self._owner._logger.warning(  # noqa: SLF001
                            "Saved page source confirmed; page eviction could not resume",
                            exc_info=True,
                        )
            elif final.same_document:
                residency.preserve()
        self.emit_save_outcome(target, options, final)
        return True

    def _commit_saved_document_state(
        self,
        options: SaveOptions,
        metadata: DocumentMetadata,
        built: DocumentSnapshotBuildResult,
        outcome: DocumentRevisionOutcome,
    ) -> None:
        """Confirm the saved-document baseline after a successful save.

        Only explicit user saves confirm the baseline.  Autosave writes a
        crash-recovery snapshot and must never clear Undo/Redo history,
        page dirty flags, or the normal-save baseline (AUTOSAVE-01A) — the
        sync and async paths share this single gate so their contracts
        cannot drift.

        SAVE-ASYNC-REV-01: the baseline (history reset + page
        mark-all-clean + metadata baseline) is eligible only while the
        snapshot still covers the live document. Its callbacks are also
        reentrant, so ``_finalize_save_outcome`` classifies the reported
        final outcome again after confirmation. A stale or cross-document
        completion is a successful archive write that confirms nothing.
        """

        if options.autosave:
            return
        if not outcome.covers_latest:
            self._owner._logger.info(  # noqa: SLF001
                "Save completed for an older document state "
                "(epoch %d rev %d); keeping dirty state and history",
                outcome.captured_epoch,
                outcome.captured_revision,
            )
            return
        self._owner._reset_saved_document_state(  # noqa: SLF001
            metadata, built.pages, built.active_page_id
        )

    def compute_save_paths(self, path: str | Path) -> tuple[Path, Path]:
        target = Path(path)
        if target.suffix.lower() != ".lstroke":
            target = target.with_suffix(".lstroke")
        temp_path = target.with_suffix(target.suffix + ".tmp")
        return target, temp_path

    def commit_save_snapshot(
        self,
        temp_path: Path,
        target: Path,
        snapshot: DocumentSnapshot,
    ) -> SavedArchiveReceipt:
        # P-05 fix: LayerIO reports success/failure via its bool return, not
        # exceptions — that return value MUST gate the atomic replace below.
        # A pre-existing ``target`` is never evidence that *this* write
        # succeeded (it may simply be the prior save left untouched by a
        # failed encode), so it is never used as a fallback "written" file.
        candidates = self.save_temporary_candidates(temp_path)
        if candidates[1].exists():
            # LayerIO normalizes the requested .tmp path to this filename.
            # It may be another document, including the current lazy source.
            raise OSError("Save temporary output already exists; choose a different save path")
        before = {
            candidate: file_identity(candidate) if candidate.exists() else None
            for candidate in candidates
        }
        if not self._layer_io.save_document_snapshot(temp_path, snapshot):
            raise OSError("Failed to write document snapshot to a temporary file")

        written = []
        for candidate in candidates:
            if candidate.exists():
                identity = file_identity(candidate)
                if identity != before[candidate]:
                    written.append((candidate, identity))

        if not written:
            # Fail closed: a True return with no output file on disk is a
            # contract violation we must not silently treat as success.
            raise OSError("Document snapshot write reported success but produced no output file")

        if len(written) != 1:
            raise OSError("Document snapshot write produced ambiguous output files")
        written_path, identity = written[0]
        if written_path != target:
            os.replace(written_path, target)
        receipt = SavedArchiveReceipt(target.resolve(), identity)
        receipt.assert_current()
        return receipt

    def emit_save_success(
        self,
        target: Path,
        options: SaveOptions,
        metadata: DocumentMetadata,
        outcome: DocumentRevisionOutcome,
    ) -> None:
        # SAVE-ASYNC-REV-01: the extra fields are additive runtime event
        # metadata (never part of the .lstroke schema).  Consumers treat a
        # payload without them as a current save for backward
        # compatibility with custom DocumentService implementations.
        # The classification fields here are PRELIMINARY (frozen before
        # this broadcast): when save_outcome_pending is True the
        # authoritative outcome follows as a DOC_SAVE_OUTCOME event and
        # consumers must defer state application to it.
        payload = {
            "path": str(target),
            "autosave": bool(options.autosave),
            "document": self._owner._metadata_to_payload(metadata),  # noqa: SLF001
            "save_same_document": bool(outcome.same_document),
            "save_covers_latest": bool(outcome.covers_latest),
            "captured_document_epoch": int(outcome.captured_epoch),
            "captured_revision": int(outcome.captured_revision),
            "save_outcome_pending": not options.autosave,
        }
        self._owner._event_bus.emit(Events.DOC_SAVED, payload)  # noqa: SLF001

    def emit_save_outcome(
        self,
        target: Path,
        options: SaveOptions,
        outcome: DocumentRevisionOutcome,
    ) -> None:
        """Broadcast the authoritative final outcome (state confirmation).

        Runtime-only additive event: never part of the .lstroke schema,
        manifest, or HostAPI save contract.  Handlers must not mutate or
        switch the document (undefined behaviour) — this stage exists so
        every consumer applies the SAME final outcome.
        """
        payload = {
            "path": str(target),
            "autosave": bool(options.autosave),
            "save_same_document": bool(outcome.same_document),
            "save_covers_latest": bool(outcome.covers_latest),
            "captured_document_epoch": int(outcome.captured_epoch),
            "captured_revision": int(outcome.captured_revision),
        }
        self._owner._event_bus.emit(Events.DOC_SAVE_OUTCOME, payload)  # noqa: SLF001

    @staticmethod
    def save_temporary_candidates(temp_path: Path) -> tuple[Path, Path]:
        return temp_path, temp_path.with_suffix(".lstroke")

    def cleanup_save_temporary_files(
        self, temp_path: Path, *, preserve: set[Path] | None = None
    ) -> None:
        for candidate in self.save_temporary_candidates(temp_path):
            if preserve is not None and candidate in preserve:
                continue
            if candidate.exists():
                try:
                    candidate.unlink()
                except OSError:
                    self._owner._logger.debug(  # noqa: SLF001
                        "Failed to clean temp file: %s",
                        candidate,
                    )

    def emit_save_failure(
        self,
        target: Path | None,
        options: SaveOptions | None,
        metadata: DocumentMetadata | None,
        error: Exception,
    ) -> None:
        # Preparation may fail before the target or document metadata exists.
        path = str(target) if target is not None else ""
        autosave = bool(options.autosave) if options is not None else False
        document = (
            self._owner._metadata_to_payload(metadata)  # noqa: SLF001
            if metadata is not None
            else {}
        )
        error_payload = build_error_payload(
            path=path,
            autosave=autosave,
            document=document,
            reason_code=(
                error.reason_code
                if isinstance(error, DocumentCaptureDeferred)
                else classify_error_reason("save_failed", error)
            ),
            user_visible_message=(
                str(error)
                if isinstance(error, DocumentCaptureDeferred)
                else "Failed to save document"
            ),
            error=error,
            debug_details_without_secret={
                "path": path,
                "autosave": autosave,
                "exception_class": type(error).__name__,
                "format_version": metadata.format_version if metadata is not None else None,
            },
        )
        self._owner._event_bus.emit(Events.DOC_SAVE_FAILED, error_payload)  # noqa: SLF001

    def emit_progress(
        self,
        value: float,
        stage: str,
        path: Path,
        callback: ProgressCallback | None,
    ) -> None:
        event = DocumentIoProgressEvent(progress=float(value), stage=stage, path=str(path))
        owner = self._owner
        owner._event_bus.emit(Events.DOC_IO_PROGRESS, event)  # noqa: SLF001

        if callback:
            try:
                callback(float(value), stage)
            except Exception as exc:
                owner._logger.debug("Progress callback raised: %s", exc)  # noqa: SLF001
