"""Persistence ownership for :mod:`loomstroke.core.document_service`."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

from ..host_api import LoadOptions, ProgressCallback, SaveOptions
from ..io.file_io import FileIO
from ..io.layer_io import DocumentMetadata, DocumentSnapshot, LayerIO, PageSnapshot
from .document_revision_tracker import DocumentRevisionOutcome, DocumentRevisionToken
from .document_snapshot_builder import DocumentSnapshotBuilder, DocumentSnapshotBuildResult
from .events import (
    DocumentIoProgressEvent,
    Events,
    build_error_payload,
    classify_error_reason,
)

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

        self._save_in_progress = True
        opts = options or SaveOptions()
        target, temp_path = self.compute_save_paths(path)
        metadata = owner._prepare_save_metadata(opts)  # noqa: SLF001

        try:
            self.emit_progress(0.0, "gather", target, progress)
            built = DocumentSnapshotBuilder(
                owner._canvas,  # noqa: SLF001
                owner._prepare_save_pages,  # noqa: SLF001
            ).build(metadata)
            snapshot = built.snapshot
            # SAVE-ASYNC-REV-01: freeze the revision the snapshot covers
            # (right after build, before any worker handoff).
            token = owner._capture_document_revision()  # noqa: SLF001

            self.commit_save_snapshot(temp_path, target, snapshot)
            self.emit_progress(1.0, "done", target, progress)
            outcome = owner._classify_document_revision(token)  # noqa: SLF001
            self.emit_save_success(target, opts, metadata, outcome)
            self._finalize_save_outcome(target, opts, metadata, built, token)
            return True

        except Exception as exc:
            owner._logger.exception("Document save failed: %s", exc)  # noqa: SLF001
            self.cleanup_save_temporary_files(temp_path)
            self.emit_save_failure(target, opts, metadata, exc)
            return False

        finally:
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

        self._save_in_progress = True
        opts = options or SaveOptions()
        target, temp_path = self.compute_save_paths(path)
        metadata = owner._prepare_save_metadata(opts)  # noqa: SLF001

        try:
            self.emit_progress(0.0, "gather", target, progress)
            built = DocumentSnapshotBuilder(
                owner._canvas,  # noqa: SLF001
                owner._prepare_save_pages,  # noqa: SLF001
            ).build(metadata)
            snapshot = built.snapshot
            # SAVE-ASYNC-REV-01: freeze the revision the snapshot covers
            # (right after build, before the worker encode starts).  Edits
            # or document switches during the await advance the tracker
            # past this token.
            token = owner._capture_document_revision()  # noqa: SLF001

            self.emit_progress(0.3, "encode", target, progress)
            await asyncio.to_thread(self.commit_save_snapshot, temp_path, target, snapshot)
            self.emit_progress(1.0, "done", target, progress)
            outcome = owner._classify_document_revision(token)  # noqa: SLF001
            self.emit_save_success(target, opts, metadata, outcome)
            self._finalize_save_outcome(target, opts, metadata, built, token)
            return True

        except Exception as exc:
            owner._logger.exception("Document save_async failed: %s", exc)  # noqa: SLF001
            self.cleanup_save_temporary_files(temp_path)
            self.emit_save_failure(target, opts, metadata, exc)
            return False

        finally:
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

        The token (size + mtime) freezes at open; a source file replaced
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
        stat = source.stat()
        token = (stat.st_size, stat.st_mtime_ns)
        layer_io = self._layer_io
        strict = bool(opts.strict)

        def _hydrate(page_id: str) -> PageSnapshot | None:
            current = source.stat()
            if (current.st_size, current.st_mtime_ns) != token:
                raise RuntimeError(f"source document changed on disk since open: {source.name}")
            return layer_io.load_single_page_snapshot(source, page_id, strict=strict)

        bind(_hydrate)

    def _finalize_save_outcome(
        self,
        target: Path,
        options: SaveOptions,
        metadata: DocumentMetadata,
        built: DocumentSnapshotBuildResult,
        token: DocumentRevisionToken,
    ) -> None:
        """Single owner of the authoritative final save outcome.

        DOC_SAVED is the archive-write notification: its handlers may
        synchronously edit or even switch the document, so the outcome
        carried in that payload is only *preliminary*.  The token is
        re-classified exactly ONCE here — after the DOC_SAVED broadcast
        has fully returned — and that one outcome drives BOTH the core
        baseline gate and the DOC_SAVE_OUTCOME state-confirmation event.
        Core, MainWindow and AutosaveManager therefore always agree,
        independent of EventBus subscription order.

        Mutating or switching the document from a DOC_SAVE_OUTCOME
        handler is undefined behaviour (forbidden): the outcome is final
        by definition and will not be recomputed again.
        """

        if options.autosave:
            # AUTOSAVE-01A: autosave never confirms state and gets no
            # outcome stage (save_outcome_pending is False on its payload).
            return
        final = self._owner._classify_document_revision(token)  # noqa: SLF001
        self._commit_saved_document_state(options, metadata, built, final)
        self.emit_save_outcome(target, options, final)

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
        mark-all-clean + metadata baseline, all three together) is
        confirmed only when the AUTHORITATIVE final outcome (classified
        once, post-broadcast, by ``_finalize_save_outcome``) still covers
        the live document.  A stale or cross-document completion is a
        successful archive write that confirms nothing.
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
    ) -> None:
        # P-05 fix: LayerIO reports success/failure via its bool return, not
        # exceptions — that return value MUST gate the atomic replace below.
        # A pre-existing ``target`` is never evidence that *this* write
        # succeeded (it may simply be the prior save left untouched by a
        # failed encode), so it is never used as a fallback "written" file.
        if not self._layer_io.save_document_snapshot(temp_path, snapshot):
            raise OSError("Failed to write document snapshot to a temporary file")

        written_path: Path | None = None
        for candidate in (temp_path, temp_path.with_suffix(".lstroke")):
            if candidate.exists():
                written_path = candidate
                break

        if written_path is None:
            # Fail closed: a True return with no output file on disk is a
            # contract violation we must not silently treat as success.
            raise OSError("Document snapshot write reported success but produced no output file")

        if written_path != target:
            os.replace(written_path, target)

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

    def cleanup_save_temporary_files(self, temp_path: Path) -> None:
        for candidate in (temp_path, temp_path.with_suffix(".lstroke")):
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
        target: Path,
        options: SaveOptions,
        metadata: DocumentMetadata,
        error: Exception,
    ) -> None:
        error_payload = build_error_payload(
            path=str(target),
            autosave=bool(options.autosave),
            document=self._owner._metadata_to_payload(metadata),  # noqa: SLF001
            reason_code=classify_error_reason("save_failed", error),
            user_visible_message="Failed to save document",
            error=error,
            debug_details_without_secret={
                "path": str(target),
                "autosave": bool(options.autosave),
                "exception_class": type(error).__name__,
                "format_version": metadata.format_version,
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
