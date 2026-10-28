"""Page management service for LoomStroke."""

# ruff: noqa: I001

from __future__ import annotations

import copy
import logging
import re
import time
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray
from PySide6.QtGui import QColor, QImage

from ..host_api import (
    CanvasService,
)
from ..host_api import CanvasSpec as HostCanvasSpec
from ..host_api import (
    EventBus,
    PageId,
    PageService,
    PageSummary,
    PageTemplateSummary,
)
from ..io.layer_io import (
    PAGE_THUMBNAIL_NAME,
    PAGES_DIR,
    DocumentMetadata,
    LayerSnapshot,
    PageSnapshot,
)
from .book_export_order import export_page_ids
from .canvas import Canvas as LegacyCanvas
from .canvas_service_simple import SimpleCanvasService, _Layer
from .comic_metadata_codec import PAGE_METADATA_NAMESPACE
from .comic_metadata_codec import merge_page_metadata as _codec_merge_page_metadata
from .comic_metadata_codec import read_page_metadata as _codec_read_page_metadata
from .commands import BlendMode
from .document_history_router import (
    DocumentHistoryRouter,
    HistoryPageContext,
    HistoryRouteCandidate,
)
from .document_history_sequence import DocumentHistorySequence
from .document_history_state_notifier import DocumentHistoryStateNotifier
from .document_save_canvas import apply_save_canvas, select_document_canvas
from .document_snapshot_builder import DocumentSnapshotBuilder
from .events import DocumentChangeEvent, Events, HistoryResetEvent, build_error_payload
from .history_failure_reporting import install_history_failure_reporter
from .layer import Layer
from .native_cmyk.page_initializer import NativeCmykPageInitializer
from .page_canvas_geometry import _install_page_canvas_geometry
from .page_canvas_ownership import current_page_canvas_spec, merge_page_canvas
from .page_command_completion import (
    _capture_page_completion_delivery,
    _public_page_switch_completion,
    _refuse_provisional_page_navigation,
    _stage_page_completion,
)
from .page_command_service import PageCommandService
from .page_commands import (
    AddPageCommand,
    ApplyMasterInstancesCommand,
    DeletePageCommand,
    DuplicatePageCommand,
    MasterInstanceTransition,
    MovePageCommand,
    MoveSpreadCommand,
    RenamePageCommand,
    SetBookLayoutCommand,
    SetMastersCommand,
    SetPageBookAttributesCommand,
)
from .page_delete_transaction import _delete_page_checked
from .page_delete_undo import _restore_deleted_page_checked
from .page_history_coordinator import PageHistoryCoordinator
from .page_mutation_preflight import PageMutationPreflight
from .page_residency import PageResidencyGuard, PageResidencyLease
from .page_switch_request import _request_page_switch
from .page_switch_transaction import _supports_page_recovery, _switch_with_recovery
from .page_template_manager import PageTemplateManager
from .page_templates import PageTemplateStore

logger = logging.getLogger(__name__)

_PAGE_ID_PATTERN = re.compile(r"^p_(\d+)$")


#: CT-08 V2b: clean, non-active pages kept resident (LRU beyond this is
#: evicted).  A fixed starting value per spec 08 §12; tune from
#: measurement, and never via a new settings key before its own review.
_RESIDENT_CLEAN_PAGE_BUDGET = 3


@dataclass(slots=True)
class _PageState:
    page: PageSnapshot
    dirty: bool = False
    #: CT-08 V2: False while only the manifest-level summary is loaded
    #: (empty ``page.layers``); content fills in on first demand via the
    #: hydration port.  Runtime-mutable fields (name, metadata,
    #: view_state) live on the placeholder from the start, so hydration
    #: only ever fills layers and never overwrites edits.
    hydrated: bool = True
    #: CT-08 V2b: LRU stamp for clean-page eviction (monotonic counter).
    last_access: int = 0


@dataclass(frozen=True, slots=True)
class _PageNamespaceSnapshot:
    """Opaque, namespace-scoped snapshot of one page's book-attribute comic
    namespace (CT-06 Phase B3-c2b).

    Unlike document-level metadata (always a dict by dataclass default),
    ``PageSnapshot.metadata`` is ``dict[str, Any] | None`` and is
    legitimately ``None`` for most pages -- ``root_present`` captures that
    fourth dimension so restore can distinguish "metadata was absent" from
    "metadata was an empty dict" and reproduce either exactly.
    """

    root_present: bool
    plugins_present: bool
    namespace_present: bool
    namespace_value: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class _PageBookAttributesTransition:
    """Before/after namespace snapshots fixed by one successful do (CT-06
    Phase B3-c2b). ``after`` is never recomputed on redo -- it is replayed
    verbatim via ``PageServiceImpl._restore_page_book_attributes_namespace``.
    """

    before: _PageNamespaceSnapshot
    after: _PageNamespaceSnapshot


class PageServiceImpl(PageService):
    """Core page management service (source of truth for page state)."""

    def __init__(
        self,
        canvas: CanvasService,
        event_bus: EventBus,
        template_store: PageTemplateStore | None = None,
        *,
        history_trim_scheduler: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        self._canvas = canvas
        self._event_bus = event_bus
        self._pages: list[_PageState] = []
        self._active_page_id: str | None = None
        self._document_canvas: HostCanvasSpec | None = None
        self._page_thumbnail_pngs: dict[str, bytes] = {}
        # CT-05 H5a: one document-local sequence clock shared by the
        # active content history and the book history.  Canvases without
        # the injection surface (legacy Canvas) simply keep producing
        # unsequenced groups — never an error.
        self._history_sequence = DocumentHistorySequence()
        set_sequence_source = getattr(canvas, "_history_set_sequence_source", None)
        if callable(set_sequence_source):
            set_sequence_source(self._history_sequence.claim)
        # CT-05 H5b: unified history state notifier.  Listenerless until
        # production binds the router-backed MinimalHistoryService via
        # _bind_document_history_listener(); until then all marks are
        # no-ops and the canvas keeps its direct HISTORY_CHANGED path.
        self._history_state_notifier = DocumentHistoryStateNotifier()
        self._page_history = PageHistoryCoordinator(canvas)
        # CT-05 H4d2: PageCommandService is the single authority for the
        # document-wide history memory budget; the coordinator only owns
        # and evicts bundles.  ``history_trim_scheduler`` is a core-internal
        # backward-compatible keyword (production injects a Qt idle
        # callback; None keeps trims synchronous).
        self._book_history = PageCommandService(
            self,
            coordinator=self._page_history,
            trim_scheduler=history_trim_scheduler,
            sequence_clock=self._history_sequence,
            state_notifier=self._history_state_notifier,
            content_redo_invalidator=self._drop_content_redo_branches,
        )
        # CT-05 H6: a brand-new content operation (canvas push) drops the
        # book redo and every inactive page's redo — one document-wide
        # redoable future (the canvas cleared its own redo already).
        set_new_operation = getattr(canvas, "_history_set_new_operation_callback", None)
        if callable(set_new_operation):
            set_new_operation(self._on_new_content_operation)
        # CT-05 H5a/H6: Undo/Redo target selection over ONE document-wide
        # timeline — active content, book, and inactive pages' content
        # (dispatch auto-activates the owning page).
        self._document_history_router = DocumentHistoryRouter(
            canvas=canvas,
            book_history=self._book_history,
            active_page_id_getter=lambda: self._active_page_id,
            coordinator=self._page_history,
            page_activator=self.set_active_page,
        )
        self._template_manager = PageTemplateManager(template_store)
        self._suppress_doc_changed = 0
        self._doc_changed_token: Any | None = None
        self._doc_changed_handler = self._on_document_changed
        self._subscribe_doc_changed()
        # CT-06 Phase B3-c2a: book-layout metadata port, bound post-
        # construction by app wiring via _bind_book_metadata_port().
        # Unbound (None, None) until then -- SetBookLayoutCommand fails
        # safely with zero side effects in that state.
        self._book_metadata_apply_transition: Callable[[Mapping[str, Any]], Any | None] | None = (
            None
        )
        self._book_metadata_restore_snapshot: Callable[[Any], bool] | None = None
        # CT-07 T3a: masters port, same pairing contract as the book port.
        self._masters_metadata_apply_transition: (
            Callable[[Mapping[str, Any]], Any | None] | None
        ) = None
        self._masters_metadata_restore_snapshot: Callable[[Any], bool] | None = None
        # CT-08 V2: single-page content loader (persistence binds it).
        self._page_hydration_loader: Callable[[str], PageSnapshot | None] | None = None
        self._page_hydration_source: Path | None = None
        self._page_residency = PageResidencyGuard()
        self._page_access_counter: int = 0
        from .japanese_text.document_story import DocumentStoryService  # noqa: PLC0415

        self.document_stories = DocumentStoryService(self)
        canvas._document_story_service = self.document_stories
        self._history_failure_reporter = install_history_failure_reporter(canvas, event_bus, self)

    # ------------------------------------------------------------------
    # Public API (HostAPI)
    def apply_vector_batch(self, replacements, expected):
        from .document_vector_batch import DocumentVectorBatch  # noqa: PLC0415

        return self._book_history.execute(DocumentVectorBatch(replacements, expected))

    def list_pages(self) -> Sequence[PageSummary]:
        summaries: list[PageSummary] = []
        for index, state in enumerate(self._pages):
            page = state.page
            name = self._display_name(page, index)
            canvas_spec = self._current_canvas_spec(page)
            summaries.append(
                PageSummary(
                    id=page.id,
                    index=index,
                    name=name,
                    canvas=canvas_spec,
                    has_canvas_override=page.canvas is not None,
                    dirty=bool(state.dirty),
                    thumbnail_available=bool(page.page_thumbnail)
                    or page.id in self._page_thumbnail_pngs,
                )
            )
        return summaries

    def get_active_page_id(self) -> PageId:
        if self._active_page_id:
            return self._active_page_id
        if self._pages:
            self._active_page_id = self._pages[0].page.id
            return self._active_page_id
        return "p_0001"

    def get_page_snapshot(self, page_id: PageId) -> PageSnapshot | None:
        """The stored snapshot of a page, or ``None`` if there is no such page.

        Read-only, and deliberately *not* part of the ``PageService``
        protocol or HostAPI: the spread viewport (CT-06 B4-c4) needs the
        layers of a page it is not editing.  The active page's snapshot only
        reflects the canvas as of the last persist (page switch / save), so
        callers wanting live pixels must go through the canvas instead.
        """
        state = self._find_page_state(page_id)
        if state is None:
            return None
        if not self._ensure_page_hydrated(state):
            # CT-08 V2c: unavailable content must not masquerade as an
            # empty page — exports would silently produce blank output.
            return None
        return state.page

    def get_page_metadata(self, page_id: PageId) -> dict[str, Any] | None:
        """The page's metadata root WITHOUT hydrating its content.

        CT-08 V2: role/folio/metadata readers (Pages panel, export
        dialogs) must not force a full layer decode of every page just to
        read a namespace — the placeholder carries metadata from the page
        directory.  Duck-typed extra like ``get_page_snapshot``.
        """
        state = self._find_page_state(page_id)
        if state is None:
            return None
        return state.page.metadata

    # ------------------------------------------------------------------
    # CT-08 V2: page content hydration.
    def _bind_page_hydration_port(
        self,
        loader: Callable[[str], PageSnapshot | None] | None,
        *,
        source: Path | None = None,
    ) -> None:
        """Bind (or unbind with ``None``) the single-page content loader.

        The persistence layer owns the source path and its external-change
        token; this service only asks for "the content of page X".
        """
        self._page_hydration_loader = loader
        self._page_hydration_source = (
            Path(source).resolve() if source is not None and loader is not None else None
        )

    def _hold_page_residency(self) -> PageResidencyLease:
        return self._page_residency.acquire()

    def _reset_page_residency(self) -> None:
        self._page_residency = PageResidencyGuard()

    def _recovery_sources_in_use(self) -> set[Path] | None:
        if self._page_hydration_loader is None:
            return set()
        if self._page_hydration_source is None:
            return None
        return {self._page_hydration_source}

    def _ensure_page_hydrated(self, state: _PageState) -> bool:
        """Fill the page's layers from the source archive on first demand.

        Only ``layers`` (and a missing ``canvas``) come from the loader:
        name, metadata, and view_state already live on the placeholder and
        may carry runtime edits made before hydration.  Failure leaves the
        page unhydrated (empty layers) and logs — a broken page must not
        take the document down (spec 08 §14).
        """
        self._page_access_counter += 1
        state.last_access = self._page_access_counter
        if state.hydrated:
            return True
        loader = self._page_hydration_loader
        if loader is None:
            logger.warning("page %s is unhydrated and no hydration port is bound", state.page.id)
            return False
        try:
            loaded = loader(state.page.id)
        except Exception:
            logger.warning("hydration failed for page %s", state.page.id, exc_info=True)
            return False
        if loaded is None:
            logger.warning("hydration returned no content for page %s", state.page.id)
            return False
        state.page.layers = list(loaded.layers)
        if state.page.canvas is None and loaded.canvas is not None:
            state.page.canvas = loaded.canvas
        state.hydrated = True
        return True

    def evict_pages_to_budget(self) -> int:
        """Return clean, non-active, rehydratable pages to placeholders
        (CT-08 V2b).  Bounds resident memory for long books; a page that
        was ever edited (dirty) is never evicted, and nothing is evicted
        when no hydration port is bound (the content would be
        unrecoverable).  Returns the number of pages evicted.
        """
        if not self._page_residency.eviction_allowed or self._page_hydration_loader is None:
            return 0
        candidates = [
            state
            for state in self._pages
            if state.hydrated and not state.dirty and state.page.id != self._active_page_id
        ]
        excess = len(candidates) - _RESIDENT_CLEAN_PAGE_BUDGET
        if excess <= 0:
            return 0
        candidates.sort(key=lambda state: state.last_access)
        for state in candidates[:excess]:
            self._evict_page_state(state)
        return excess

    def _evict_page_state(self, state: _PageState) -> None:
        """Swap in a fresh placeholder; the old snapshot object stays
        intact for any holder (undo commands, in-flight exports)."""
        old_page = state.page
        state.page = PageSnapshot(
            id=old_page.id,
            name=old_page.name,
            index=old_page.index,
            layers=[],
            canvas=old_page.canvas,
            page_thumbnail=old_page.page_thumbnail,
            metadata=old_page.metadata,
            view_state=old_page.view_state,
        )
        state.hydrated = False

    def persist_active_page(self) -> None:
        """Persist the live canvas into the active page's stored snapshot.

        ``get_page_snapshot`` returns the state as of the last persist, so
        read-only consumers that render from stored snapshots (multi-page
        export) call this first.  Mirrors the CT-05 H4a stale-snapshot
        pattern used by ``duplicate_page``: close any pending stroke
        through the H1 history port, then capture the canvas.
        """
        close_pending = getattr(self._canvas, "_history_close_pending_group", None)
        if callable(close_pending):
            try:
                close_pending()
            except Exception:
                logger.exception("Failed to close pending stroke before persist")
        self._persist_active_page()

    def set_active_page(self, page_id: PageId) -> None:
        if _refuse_provisional_page_navigation(self, page_id):
            return
        with _public_page_switch_completion():
            _request_page_switch(self, page_id, recover=_switch_with_recovery)

    def add_page(self, *, after_page_id: PageId | None = None) -> PageId:
        return self.add_page_from_template(
            "builtin.blank_like_current",
            after_page_id=after_page_id,
        )

    def list_page_templates(self) -> Sequence[PageTemplateSummary]:
        return self._template_manager.list_summaries()

    def add_page_from_template(
        self,
        template_id: str,
        *,
        after_page_id: PageId | None = None,
    ) -> PageId:
        self._sync_document_canvas_with_live()
        template = self._template_manager.get(template_id)
        if template is None:
            self._emit_page_op_failed(
                "add_template",
                after_page_id or self.get_active_page_id(),
                f"template not found: {template_id}",
            )
            return self.get_active_page_id()

        insert_index = self._resolve_insert_index(after_page_id)
        base_page = self._pages[insert_index - 1].page if self._pages else None
        try:
            if template.id == "builtin.blank_like_current" and base_page is not None:
                base_state = self._find_page_state(base_page.id)
                if base_state is None or not self._ensure_page_hydrated(base_state):
                    raise RuntimeError("template source page could not be loaded")
                if base_page.id == self._active_page_id:
                    close_pending = getattr(self._canvas, "_history_close_pending_group", None)
                    if callable(close_pending):
                        close_pending()
                    self._persist_active_page(raise_on_error=True)
                base_page = base_state.page
            canvas_spec = self._template_manager.resolve_canvas_spec(
                template,
                document_canvas=self._document_canvas,
                active_canvas_spec=self._current_canvas_spec(base_page),
                fallback_canvas_spec=self._fallback_canvas_spec(base_page),
            )
            new_page = self._template_manager.create_page_snapshot(
                template,
                page_id=self._next_page_id(),
                base_page=base_page,
                canvas_spec=canvas_spec,
                new_layer_id=self._new_layer_id,
            )
            NativeCmykPageInitializer.initialize(self._canvas, new_page)
        except Exception as exc:
            logger.exception("Failed to add page from template %s: %s", template_id, exc)
            self._emit_page_op_failed(
                "add_template",
                after_page_id or self.get_active_page_id(),
                str(exc),
            )
            return self.get_active_page_id()

        anchor_prev_page_id = (
            self._pages[insert_index - 1].page.id if 0 < insert_index <= len(self._pages) else None
        )
        command = AddPageCommand(
            page=new_page,
            anchor_prev_page_id=anchor_prev_page_id,
            fallback_index=insert_index,
            previous_active_page_id=self._active_page_id,
        )
        if not self._book_history.execute(command):
            self._emit_page_op_failed(
                "add_template",
                after_page_id or self.get_active_page_id(),
                "failed to apply add page command",
            )
            return self.get_active_page_id()
        self._apply_template_view_state(new_page.id, template)
        return new_page.id

    def _apply_template_view_state(self, page_id: PageId, template: Any) -> None:
        """Materialize a template's view state (guides etc.) onto the new
        page (CT-07 T1b, spec 07 §10.1: guides land in the existing
        ``page.view_state`` — no schema change).  ``mark_dirty=False``
        because the page addition itself already dirtied the document."""
        metadata = getattr(template, "metadata", None)
        if not isinstance(metadata, Mapping):
            return
        view_state = metadata.get("view_state")
        if isinstance(view_state, Mapping) and view_state:
            self.update_page_view_state(page_id, dict(view_state), mark_dirty=False)

    def save_page_as_template(  # noqa: PLR0911
        self,
        page_id: PageId,
        name: str,
        *,
        options: Any = None,
    ) -> str | None:
        """``options`` is a ``TemplateCaptureOptions`` (CT-07 T2); ``None``
        keeps the historical capture exactly (compat facade, spec 07 §8)."""
        self._sync_document_canvas_with_live()
        state = self._find_page_state(page_id)
        if state is None:
            self._emit_page_op_failed("save_template", page_id, "page not found")
            return None
        preflight = PageMutationPreflight(self, (page_id,))
        if not preflight.hydrate():
            self._emit_page_op_failed(
                "save_template", page_id, "page could not be loaded or changed"
            )
            return None
        state = preflight.targets[0][1]

        try:
            normalized_name = self._template_manager.validate_template_name(name)
        except ValueError as exc:
            self._emit_page_op_failed("save_template", page_id, str(exc))
            return None

        try:
            if self._active_page_id == page_id:
                self._persist_active_page(raise_on_error=True)
            if not preflight.is_ready():
                self._emit_page_op_failed(
                    "save_template", page_id, "source page changed or is unavailable"
                )
                return None
            view_state = None
            if options is not None and getattr(options, "include_guides", False):
                view_state = self.get_page_view_state(page_id)
            canvas_spec = self._current_canvas_spec(state.page)
            if not preflight.is_ready():
                self._emit_page_op_failed(
                    "save_template", page_id, "source page changed or is unavailable"
                )
                return None
            saved = self._template_manager.save_page_as_template(
                state.page,
                normalized_name,
                canvas_spec=canvas_spec,
                options=options,
                page_view_state=view_state,
            )
            return saved.id
        except Exception as exc:
            logger.exception("Failed to save template from page %s: %s", page_id, exc)
            self._emit_page_op_failed("save_template", page_id, str(exc))
            return None

    # CT-07 T2 library facades (duck-typed extras, like set_book_layout):
    # library operations, not document edits — no Command, no undo.
    def rename_page_template(self, template_id: str, new_name: str) -> bool:
        try:
            self._template_manager.template_store.rename_user_template(template_id, new_name)
            return True
        except Exception:
            logger.warning("failed to rename template %s", template_id, exc_info=True)
            return False

    def duplicate_page_template(self, template_id: str) -> str | None:
        try:
            return self._template_manager.template_store.duplicate_template(template_id).id
        except Exception:
            logger.warning("failed to duplicate template %s", template_id, exc_info=True)
            return None

    def delete_page_template(self, template_id: str) -> bool:
        try:
            return self._template_manager.template_store.delete_user_template(template_id)
        except Exception:
            logger.warning("failed to delete template %s", template_id, exc_info=True)
            return False

    def set_page_template_category(self, template_id: str, category: str) -> bool:
        try:
            self._template_manager.template_store.set_user_template_category(template_id, category)
            return True
        except Exception:
            logger.warning("failed to re-file template %s", template_id, exc_info=True)
            return False

    def get_page_template_preview(self, template_id: str) -> bytes | None:
        return self._template_manager.template_preview_png(self._template_manager.get(template_id))

    def estimate_page_template_raster_bytes(self, page_id: PageId) -> int:
        state = self._find_page_state(page_id)
        if state is None:
            return 0
        self._ensure_page_hydrated(state)
        return self._template_manager.estimate_raster_capture_bytes(state.page)

    def duplicate_page(self, page_id: PageId) -> PageId:  # noqa: PLR0911
        self._sync_document_canvas_with_live()
        state = self._find_page_state(page_id)
        if state is None:
            self._emit_page_op_failed("duplicate", page_id, "page not found")
            return page_id
        preflight = PageMutationPreflight(self, (page_id,))
        if not preflight.hydrate():
            self._emit_page_op_failed("duplicate", page_id, "page could not be loaded or changed")
            return page_id
        state = preflight.targets[0][1]
        if self._active_page_id == page_id:
            # The stored snapshot goes stale while its page is active: close
            # any pending stroke through the H1 history port, then persist
            # the live canvas content so the duplicate captures what the
            # user currently sees (CT-05 H4a stale-snapshot fix).
            close_pending = getattr(self._canvas, "_history_close_pending_group", None)
            if callable(close_pending):
                try:
                    if close_pending() is False:
                        self._emit_page_op_failed(
                            "duplicate", page_id, "pending stroke could not be closed"
                        )
                        return page_id
                except Exception as exc:
                    logger.exception(
                        "Failed to close pending stroke before duplicating %s", page_id
                    )
                    self._emit_page_op_failed("duplicate", page_id, str(exc))
                    return page_id
            if not preflight.is_ready():
                self._emit_page_op_failed(
                    "duplicate", page_id, "source page changed or is unavailable"
                )
                return page_id
            try:
                self._persist_active_page(raise_on_error=True)
            except Exception as exc:
                logger.exception("Failed to capture source before duplicating %s", page_id)
                self._emit_page_op_failed("duplicate", page_id, str(exc))
                return page_id
        if not preflight.is_ready():
            self._emit_page_op_failed("duplicate", page_id, "source page changed or is unavailable")
            return page_id
        source = state.page
        try:
            # Everything non-deterministic is fixed exactly once here: page
            # id, layer ids, cloned raster buffers, and the copy name.  Redo
            # reuses this snapshot as-is.
            copy_name = f"{self._display_name(source, self._page_index(source.id))} (copy)"
            new_page = PageSnapshot(
                id=self._next_page_id(),
                name=copy_name,
                index=None,
                layers=self._clone_layers(source.layers, blank=False),
                canvas=source.canvas,
                page_thumbnail=None,
                metadata=(
                    copy.deepcopy(source.metadata) if isinstance(source.metadata, dict) else None
                ),
                view_state=None,
            )
            from .comic_panels.page_copy import remap_copied_page  # noqa: PLC0415

            remap_copied_page(source.layers, new_page.layers, page_id=new_page.id)
        except Exception as exc:
            logger.exception("Failed to clone page %s: %s", page_id, exc)
            self._emit_page_op_failed("duplicate", page_id, str(exc))
            return page_id
        if not preflight.is_ready():
            self._emit_page_op_failed("duplicate", page_id, "source page changed or is unavailable")
            return page_id
        command = DuplicatePageCommand(
            source_page_id=page_id,
            page=new_page,
            anchor_prev_page_id=page_id,
            fallback_index=self._page_index(page_id) + 1,
            previous_active_page_id=self._active_page_id,
        )
        if not self._book_history.execute(command):
            self._emit_page_op_failed(
                "duplicate", page_id, "failed to apply duplicate page command"
            )
            return page_id
        return new_page.id

    def delete_page(self, page_id: PageId) -> None:
        if len(self._pages) <= 1:
            self._emit_page_op_failed("delete", page_id, "cannot delete last page")
            return
        if self._page_index(page_id) < 0:
            self._emit_page_op_failed("delete", page_id, "page not found")
            return
        # CT-08 V2: undo of a delete restores the captured snapshot, so an
        # unhydrated page must become real content before the capture.
        if not PageMutationPreflight(self, (page_id,)).hydrate():
            self._emit_page_op_failed("delete", page_id, "page could not be loaded or changed")
            return
        # CT-05 H4b: the command's apply() captures the page's current
        # runtime state (snapshot, dirty, anchors, thumbnail bytes, content
        # history bundle) and removes it; undo restores everything.
        command = DeletePageCommand(page_id=page_id)
        self._book_history.execute(command)

    def rename_page(self, page_id: PageId, name: str) -> None:
        state = self._find_page_state(page_id)
        if state is None:
            self._emit_page_op_failed("rename", page_id, "page not found")
            return
        normalized = name.strip()
        new_name: str | None
        if not normalized:
            new_name = None
        else:
            if len(normalized) > 64:
                self._emit_page_op_failed("rename", page_id, "name too long (max 64)")
                return
            if any(ord(ch) < 32 for ch in normalized):
                self._emit_page_op_failed("rename", page_id, "name contains control characters")
                return
            new_name = normalized
        if state.page.name == new_name:
            # Same normalized name: no-op, no event, no book history entry
            # (CT-05 H3).
            return
        command = RenamePageCommand(
            page_id=page_id,
            old_name=state.page.name,
            new_name=new_name,
        )
        self._book_history.execute(command)

    def move_page(self, page_id: PageId, new_index: int) -> None:
        current_index = self._page_index(page_id)
        if current_index < 0:
            self._emit_page_op_failed("move", page_id, "page not found")
            return
        clamped = max(0, min(int(new_index), len(self._pages) - 1))
        if clamped == current_index:
            return
        old_prev = self._pages[current_index - 1].page.id if current_index > 0 else None
        old_next = (
            self._pages[current_index + 1].page.id if current_index + 1 < len(self._pages) else None
        )
        order = [entry.page.id for entry in self._pages]
        order.pop(current_index)
        order.insert(clamped, page_id)
        new_prev = order[clamped - 1] if clamped > 0 else None
        new_next = order[clamped + 1] if clamped + 1 < len(order) else None
        command = MovePageCommand(
            page_id=page_id,
            old_prev_anchor_id=old_prev,
            old_next_anchor_id=old_next,
            old_index=current_index,
            new_prev_anchor_id=new_prev,
            new_next_anchor_id=new_next,
            new_index=clamped,
        )
        self._book_history.execute(command)

    def _move_spread(self, spread_page_ids: Sequence[PageId], new_index: int) -> bool:
        """Move a spread's pages together as one history entry (CT-06 B4-a).

        Takes the page ids explicitly rather than resolving the spread itself:
        deriving it here would need the document-level book layout, and this
        service deliberately has no read access to it (the metadata port is
        apply/restore only, B3-c1). The caller already holds the resolved
        spreads -- that is what it draws -- so it passes the pages to move.

        ``new_index`` is the logical index the spread's first page lands on,
        counted among the pages that are *not* part of the spread; the rest
        follow immediately after it, keeping the spread contiguous and every
        other page's relative order intact.

        Returns ``False`` without touching history when the move is a no-op or
        cannot be resolved, so a rejected drag never leaves an empty Undo step.
        """
        requested = [str(pid) for pid in spread_page_ids]
        if not requested:
            return False

        page_ids = [pid for pid in requested if self._page_index(pid) >= 0]
        if len(page_ids) != len(requested):
            missing = next(pid for pid in requested if pid not in page_ids)
            self._emit_page_op_failed("move_spread", missing, "page not found")
            return False

        order = [entry.page.id for entry in self._pages]
        old_positions = {pid: order.index(pid) for pid in page_ids}
        # Keep the spread's own pages in their current relative order.
        page_ids.sort(key=lambda pid: old_positions[pid])

        first_old = old_positions[page_ids[0]]
        last_old = old_positions[page_ids[-1]]
        if last_old - first_old != len(page_ids) - 1:
            # A spread is always a run of consecutive logical pages. Refusing a
            # non-contiguous set keeps revert honest: it restores a block, and
            # a scattered set could not be put back where it came from.
            self._emit_page_op_failed("move_spread", page_ids[0], "pages are not contiguous")
            return False

        remaining = [pid for pid in order if pid not in old_positions]
        target = max(0, min(int(new_index), len(remaining)))
        new_order = remaining[:target] + page_ids + remaining[target:]
        if new_order == order:
            return False

        # Anchor against pages *outside* the spread. Anchoring a page to its
        # own spread partner would resolve against a page that has not moved
        # yet, landing the block back where it started; only the block's first
        # page needs outside anchors, and the rest chain off the partner that
        # was just placed.
        old_outside_prev = order[first_old - 1] if first_old > 0 else None
        old_outside_next = order[last_old + 1] if last_old + 1 < len(order) else None
        new_outside_prev = remaining[target - 1] if target > 0 else None
        new_outside_next = remaining[target] if target < len(remaining) else None

        moves: list[MovePageCommand] = []
        for position, pid in enumerate(page_ids):
            leading = page_ids[position - 1] if position > 0 else None
            moves.append(
                MovePageCommand(
                    page_id=pid,
                    old_prev_anchor_id=leading if leading is not None else old_outside_prev,
                    old_next_anchor_id=old_outside_next,
                    old_index=old_positions[pid],
                    new_prev_anchor_id=leading if leading is not None else new_outside_prev,
                    new_next_anchor_id=new_outside_next,
                    new_index=new_order.index(pid),
                )
            )

        return bool(self._book_history.execute(MoveSpreadCommand(moves=tuple(moves))))

    def request_page_thumbnail(self, page_id: PageId) -> None:
        if self._find_page_state(page_id) is None:
            self._emit_page_op_failed("request_thumbnail", page_id, "page not found")
            return
        thumbnail_png = self._page_thumbnail_pngs.get(page_id)
        payload = {
            "page_id": page_id,
            "thumbnail_png": thumbnail_png,
            "error": None if thumbnail_png else "thumbnail not cached",
        }
        self._emit_event(Events.PAGE_THUMBNAIL_UPDATED, payload)

    def update_active_page_thumbnail(self, thumbnail_png: bytes) -> None:
        """Store and notify the thumbnail for the currently edited page."""
        if not self._active_page_id:
            return
        self.update_page_thumbnail(self._active_page_id, thumbnail_png)

    def update_page_thumbnail(self, page_id: PageId, thumbnail_png: bytes) -> None:
        """Store a runtime thumbnail and emit PAGE_THUMBNAIL_UPDATED.

        Thumbnails are derived UI data. Updating them must not mark the page
        dirty or emit DOC_CHANGED, otherwise every preview refresh would create
        an artificial document edit.
        """
        state = self._find_page_state(page_id)
        if state is None:
            self._emit_page_op_failed("update_thumbnail", page_id, "page not found")
            return
        if not isinstance(thumbnail_png, (bytes, bytearray)) or not thumbnail_png:
            return
        png_bytes = bytes(thumbnail_png)
        if self._page_thumbnail_pngs.get(page_id) == png_bytes:
            return
        self._page_thumbnail_pngs[page_id] = png_bytes
        state.page.page_thumbnail = self._page_thumbnail_path(page_id)
        self._emit_page_thumbnail_updated(page_id, png_bytes)

    def get_page_view_state(self, page_id: PageId) -> dict[str, Any] | None:
        state = self._find_page_state(page_id)
        if state is None or state.page.view_state is None:
            return None
        return dict(state.page.view_state)

    def set_page_view_state(self, page_id: PageId, view_state: dict[str, Any] | None) -> None:
        state = self._find_page_state(page_id)
        if state is None:
            self._emit_page_op_failed("set_view_state", page_id, "page not found")
            return
        state.page.view_state = dict(view_state) if isinstance(view_state, dict) else None
        state.dirty = True
        self._emit_document_changed("pages.view_state")
        self._emit_pages_changed()

    def update_page_view_state(
        self,
        page_id: PageId,
        updates: Mapping[str, Any],
        *,
        mark_dirty: bool = False,
    ) -> None:
        """Merge keys into a page's view state, leaving the rest alone.

        ``set_page_view_state`` replaces the whole dict, so two writers of
        the same page (guides/rulers and the viewport) would erase each
        other.  This merges instead, and unknown keys survive.

        ``mark_dirty=False`` (the default) writes silently: where the user
        left the zoom on a page is navigation, not an edit, and dirtying the
        document -- or emitting ``PAGES_CHANGED``, which rebuilds the whole
        Pages panel -- on every page switch would be wrong on both counts.
        The value still rides along the next time the document is saved.

        Not part of the ``PageService`` Protocol or HostAPI: the UI reaches
        it by duck typing, like the other page-service extras.
        """
        state = self._find_page_state(page_id)
        if state is None:
            self._emit_page_op_failed("update_view_state", page_id, "page not found")
            return
        merged = dict(state.page.view_state or {})
        merged.update(dict(updates))
        state.page.view_state = merged
        if not mark_dirty:
            return
        state.dirty = True
        self._emit_document_changed("pages.view_state")
        self._emit_pages_changed()

    def export_page_ids(
        self,
        *,
        include_roles: Collection[str] | None = None,
        exclude_roles: Collection[str] | None = None,
    ) -> tuple[str, ...]:
        """Pages to export, in logical order, filtered by role (CT-06 B5).

        The provisional API from spec §8, backed by the shared
        ``book_export_order`` provider so every exporter picks pages the
        same way.  Binding never reverses this order.

        Not part of the ``PageService`` Protocol or HostAPI: callers reach
        it by duck typing, like the other page-service extras.
        """
        return export_page_ids(
            [state.page.id for state in self._pages],
            {state.page.id: state.page.metadata for state in self._pages},
            include_roles=include_roles,
            exclude_roles=exclude_roles,
        )

    def get_active_canvas_spec(self) -> HostCanvasSpec:
        active_id = self.get_active_page_id()
        state = self._find_page_state(active_id)
        return self._current_canvas_spec(state.page if state else None)

    # ------------------------------------------------------------------
    # Internal API for DocumentService
    def set_document_canvas(self, canvas: HostCanvasSpec | None) -> None:
        self._document_canvas = canvas

    def reset_pages(
        self,
        layers: Sequence[LayerSnapshot],
        document_canvas: HostCanvasSpec,
    ) -> None:
        page = PageSnapshot(
            id="p_0001",
            name="Page 1",
            index=0,
            layers=list(layers),
            canvas=None,
        )
        # Hand the spec to _set_pages rather than assigning it first, so a
        # rejected page-set leaves the current document's spec in place.
        self._set_pages(
            [page],
            active_page_id=page.id,
            apply_active=True,
            dirty=False,
            document_canvas=document_canvas,
        )

    def load_document(
        self,
        metadata: DocumentMetadata,
        pages: Sequence[PageSnapshot],
        *,
        active_page_id: str | None = None,
        unloaded_page_ids: Collection[str] = (),
    ) -> None:
        # The spec is committed by _set_pages only once the incoming document
        # validates, so a rejected load does not leave this service pointing at
        # the failed document's canvas.
        self._set_pages(
            list(pages),
            active_page_id=active_page_id,
            apply_active=True,
            dirty=False,
            document_canvas=self._host_canvas_spec_from_metadata(metadata),
        )
        # CT-08 V2: mark lazily-opened placeholders; their layers fill in
        # through the hydration port on first demand.
        unloaded = set(unloaded_page_ids)
        if unloaded:
            for state in self._pages:
                if state.page.id in unloaded and state.page.id != self._active_page_id:
                    state.hydrated = False

    def seed_page_thumbnails(self, thumbnail_pngs: Mapping[str, bytes]) -> None:
        """Install saved thumbnails for every loaded page (2026-08-04 fix).

        Without this, opening a multi-page document showed placeholders
        until each page was activated — the runtime thumbnail cache only
        ever filled from active-page renders.  Replaces the cache (stale
        thumbnails from a previous document must not leak into the new
        one) and announces each page so the panel repaints immediately.
        Not a document edit: no dirty, no undo.
        """
        self._page_thumbnail_pngs.clear()
        for page_id, payload in thumbnail_pngs.items():
            if not isinstance(payload, (bytes, bytearray)) or not payload:
                continue
            if self._find_page_state(page_id) is None:
                continue
            self._page_thumbnail_pngs[page_id] = bytes(payload)
            self._emit_page_thumbnail_updated(page_id, bytes(payload))

    def build_pages_for_save(
        self,
        active_layers: Sequence[LayerSnapshot],
        metadata: DocumentMetadata,
    ) -> tuple[list[PageSnapshot], str]:
        page_set = self._pages
        active_before = self._active_page_id
        page_order = tuple(map(id, page_set))
        # CT-08 V2: a save must contain every page's real content
        # (incremental unchanged-member copy is V5).
        for state in tuple(page_set):
            if not self._ensure_page_hydrated(state):
                raise OSError(f"Failed to load page {state.page.id} for save")
            if self._pages is not page_set or self._active_page_id != active_before:
                raise OSError("The document or active page changed while preparing the save.")
        if tuple(map(id, self._pages)) != page_order:
            raise OSError("The page list changed while preparing the save.")
        self._sync_document_canvas_with_live()
        canvas_spec = self._document_canvas_for_save()
        apply_save_canvas(metadata, canvas_spec)
        if not self._pages:
            page = PageSnapshot(
                id="p_0001",
                name="Page 1",
                index=0,
                layers=list(active_layers),
                canvas=None,
            )
            self._set_pages(
                [page],
                active_page_id=page.id,
                apply_active=False,
                dirty=True,
                document_canvas=canvas_spec,
            )
            return [page], page.id

        active_id = self.get_active_page_id()
        state = self._find_page_state(active_id)
        if state is not None:
            state.page.layers = list(active_layers)
        self._reindex_pages()
        return [state.page for state in self._pages], active_id

    def mark_all_clean(self) -> None:
        for state in self._pages:
            state.dirty = False
        self._emit_pages_changed()

    # ------------------------------------------------------------------
    # Internal book history surface (CT-05 H3).  Runtime-only; not part of
    # the PageService Protocol / HostAPI.  The Phase H5 document history
    # router is the intended consumer — UI Undo/Redo stays disconnected.
    def _book_history_can_undo(self) -> bool:
        return self._book_history.can_undo()

    def _book_history_can_redo(self) -> bool:
        return self._book_history.can_redo()

    def _book_history_undo(self) -> bool:
        return self._book_history.undo()

    def _book_history_redo(self) -> bool:
        return self._book_history.redo()

    def _book_history_undo_count(self) -> int:
        return self._book_history.undo_count()

    def _book_history_redo_count(self) -> int:
        return self._book_history.redo_count()

    # ------------------------------------------------------------------
    # Internal document history surface (CT-05 H5a).  Runtime-only; not
    # part of the PageService Protocol / HostAPI.  Phase H5b connects
    # MinimalHistoryService here — until then the UI keeps dispatching to
    # the canvas only.
    def _document_history_can_undo(self) -> bool:
        return self._document_history_router.can_undo()

    def _document_history_can_redo(self) -> bool:
        return self._document_history_router.can_redo()

    def _document_history_undo(self) -> bool:
        return self._document_history_router.undo()

    def _document_history_redo(self) -> bool:
        return self._document_history_router.redo()

    def _document_history_undo_count(self) -> int:
        return self._document_history_router.undo_count()

    def _document_history_redo_count(self) -> int:
        return self._document_history_router.redo_count()

    def _document_history_peek_undo(self) -> HistoryRouteCandidate | None:
        return self._document_history_router.peek_undo()

    def _document_history_peek_redo(self) -> HistoryRouteCandidate | None:
        return self._document_history_router.peek_redo()

    def _resolve_history_page_context(self, page_id: str) -> HistoryPageContext | None:
        """Resolve stable page metadata for history UI labels (H5c).

        Live document pages win (current name / current one-based index);
        off-document pages (applied delete, undone add/duplicate) fall
        back to the read-only hint retained by the book command.  Purely
        internal and read-only — never part of the PageService Protocol.
        """
        if not page_id:
            return None
        index = self._page_index(page_id)
        if index >= 0:
            page = self._pages[index].page
            name = page.name.strip() if isinstance(page.name, str) and page.name.strip() else None
            return HistoryPageContext(
                page_id=page_id,
                custom_name=name,
                one_based_index=index + 1,
            )
        hint = self._book_history._off_document_page_context(page_id)
        if hint is not None:
            custom_name, one_based = hint
            return HistoryPageContext(
                page_id=page_id,
                custom_name=custom_name,
                one_based_index=one_based,
            )
        return None

    def _drop_content_redo_branches(self) -> None:
        """Drop the active canvas redo and all inactive redo branches (H6).

        Called by ``PageCommandService`` after a successful *new* book
        command: the document-wide single timeline has exactly one
        redoable future, so content redo branches everywhere die.
        """
        with self._history_state_notifier.batch():
            drop_canvas_redo = getattr(self._canvas, "_history_drop_redo_branch", None)
            if callable(drop_canvas_redo):
                drop_canvas_redo()
            if self._page_history.drop_all_redo_groups() > 0:
                self._history_state_notifier.mark_changed()

    def _on_new_content_operation(self) -> None:
        """New content group entered the active canvas stack (H6).

        The canvas cleared its own redo already; kill the remaining
        document-wide redo branches (book + inactive pages).
        """
        with self._history_state_notifier.batch():
            if self._book_history.redo_count() > 0:
                self._book_history.drop_redo_branch()
                self._history_state_notifier.mark_changed()
            if self._page_history.drop_all_redo_groups() > 0:
                self._history_state_notifier.mark_changed()

    def _bind_document_history_listener(self, listener: Callable[[], None] | None) -> None:
        """Connect (or disconnect with ``None``) the unified history listener.

        Production wiring (CT-05 H5b): the router-backed
        ``MinimalHistoryService`` becomes the single publisher of
        HISTORY_STATE / HISTORY_CHANGED.  While connected, canvas history
        changes are rerouted into the notifier instead of raw EventBus
        emission; disconnecting restores the standalone canvas path.
        Canvases without the internal callback surface (legacy Canvas)
        simply keep their existing behaviour.
        """
        self._history_state_notifier.set_listener(listener)
        set_callback = getattr(self._canvas, "_history_set_changed_callback", None)
        if callable(set_callback):
            set_callback(self._history_state_notifier.mark_changed if listener else None)

    def _bind_history_failure_revision_source(self, capture: Callable[[], tuple[int, int]]) -> None:
        if self._history_failure_reporter is not None:
            self._history_failure_reporter.revision_source = capture

    # ------------------------------------------------------------------
    # Book-layout metadata port (CT-06 Phase B3-c2a).  ``PageServiceImpl``
    # never imports ``DocumentMetadataService``/``DocumentServiceImpl`` --
    # app wiring injects two limited callbacks post-construction (same
    # late-injection pattern as ``_bind_document_history_listener`` above).
    # A standalone/direct-construction ``PageServiceImpl`` (tests, or
    # legacy no-PageService callers) simply never binds this port, and
    # ``SetBookLayoutCommand`` fails safely (``False``, zero side effects).
    def _bind_book_metadata_port(
        self,
        apply_transition: Callable[[Mapping[str, Any]], Any | None] | None,
        restore_snapshot: Callable[[Any], bool] | None,
    ) -> None:
        """Bind (or unbind with ``None, None``) the book-metadata port.

        Both callbacks must be bound or unbound together -- a partial
        bind would let ``SetBookLayoutCommand`` apply through one
        callback while restore silently no-ops (or vice versa), so it is
        rejected outright.
        """
        if (apply_transition is None) != (restore_snapshot is None):
            raise ValueError(
                "book metadata port must be bound or unbound as a pair "
                "(apply_transition and restore_snapshot together)"
            )
        self._book_metadata_apply_transition = apply_transition
        self._book_metadata_restore_snapshot = restore_snapshot

    def _bind_masters_metadata_port(
        self,
        apply_transition: Callable[[Mapping[str, Any]], Any | None] | None,
        restore_snapshot: Callable[[Any], bool] | None,
    ) -> None:
        """CT-07 T3a: bind the masters port (pairing rule as the book port)."""
        if (apply_transition is None) != (restore_snapshot is None):
            raise ValueError(
                "masters metadata port must be bound or unbound as a pair "
                "(apply_transition and restore_snapshot together)"
            )
        self._masters_metadata_apply_transition = apply_transition
        self._masters_metadata_restore_snapshot = restore_snapshot

    def _apply_masters_transition(self, updates: Mapping[str, Any]) -> Any | None:
        """Core-internal bridge used only by ``SetMastersCommand.apply()``."""
        port = self._masters_metadata_apply_transition
        if port is None:
            logger.debug("Masters metadata port not bound; SetMastersCommand cannot apply")
            return None
        return port(updates)

    def _restore_masters_namespace_snapshot(self, snapshot: Any) -> bool:
        """Core-internal bridge for ``SetMastersCommand`` undo/redo."""
        port = self._masters_metadata_restore_snapshot
        if port is None:
            logger.debug("Masters metadata port not bound; SetMastersCommand cannot restore")
            return False
        return bool(port(snapshot))

    def set_masters_namespace(self, updates: Mapping[str, Any]) -> bool:
        """Rewrite the master definitions namespace (CT-07 T3a).

        ``updates`` is the full namespace payload from
        ``master_page_model.masters_namespace_payload()`` — callers read
        the current definitions, edit the list, and write it back whole,
        so one visit (define / rename / delete a master) is one undo
        step.  Duck-typed extra like ``set_book_layout``.
        """
        command = SetMastersCommand(updates=updates)
        return bool(self._book_history.execute(command))

    # ------------------------------------------------------------------
    # CT-07 T3a: master instance materialization.
    def _set_master_instance_layer(
        self,
        page_id: PageId,
        *,
        remove_layer: Any | None,
        insert_layer: Any | None,
        insert_index: int | None,
    ) -> bool:
        """Atomic single-page instance swap (command-internal primitive).

        Removes ``remove_layer`` (matched by layer id — the command holds
        the exact snapshot it inserted/found) and inserts ``insert_layer``
        at ``insert_index`` (clamped; ``None`` = top of the stack).  The
        active page persists the live canvas first and reloads it after,
        so the change is visible immediately and never overwrites live
        edits.
        """
        preflight = PageMutationPreflight(self, (page_id,))
        if not preflight.hydrate():
            return False
        state = preflight.targets[0][1]
        is_active = self._active_page_id == page_id
        if is_active:
            self.persist_active_page()
            if not preflight.is_ready() or self._active_page_id != page_id:
                return False
        layers = state.page.layers
        if remove_layer is not None:
            remove_id = getattr(remove_layer, "id", None)
            index = next(
                (i for i, layer in enumerate(layers) if layer.id == remove_id),
                None,
            )
            if index is None:
                logger.warning("master instance layer %s not found on page %s", remove_id, page_id)
                return False
            layers.pop(index)
        if insert_layer is not None:
            position = len(layers) if insert_index is None else insert_index
            layers.insert(max(0, min(position, len(layers))), insert_layer)
        state.dirty = True
        if is_active:
            self._apply_page_to_canvas(state.page)
        self._emit_page_dirty_changed(page_id, True)
        return True

    def apply_master_to_pages(
        self,
        master_payload: Mapping[str, Any],
        page_ids: Sequence[PageId],
    ) -> bool:
        """Materialize (or refresh) a master on ``page_ids`` (CT-07 T3a).

        ``master_payload`` is one entry of the masters namespace (the
        caller reads definitions via ``DocumentServiceImpl
        .read_masters_metadata()``).  Pages that already carry an
        instance of this master have it replaced in place (slot id kept);
        pages without one get a new locked group on top.  All pages are
        one history entry; a mid-apply failure rolls back (spec 07 §9).
        Duck-typed extra, like ``set_book_layout``.
        """
        from uuid import uuid4  # noqa: PLC0415

        from .master_instance_preparation import _master_facade_preparation  # noqa: PLC0415
        from .master_page_apply import (  # noqa: PLC0415
            build_master_instance_layer,
            find_master_instance,
        )
        from .master_page_model import master_from_payload  # noqa: PLC0415

        master = master_from_payload(master_payload)
        if master is None:
            logger.warning("apply_master_to_pages: malformed master payload")
            return False
        preparation = _master_facade_preparation(self, page_ids)
        if preparation is not None:
            if not preparation.run():
                return False
            preflight = preparation.scope
        else:
            preflight = PageMutationPreflight(self, page_ids)
            if self._active_page_id in set(page_ids):
                self.persist_active_page()
            if not preflight.hydrate():
                logger.warning("apply_master_to_pages: page unavailable or document changed")
                return False

        transitions: list[MasterInstanceTransition] = []
        for page_id, state in preflight.targets:
            existing = find_master_instance(state.page.layers, master.id)
            slot_id = existing[2].slot_id if existing is not None else uuid4().hex
            canvas_spec = self._current_canvas_spec(state.page)
            after_layer = build_master_instance_layer(
                master,
                slot_id=slot_id,
                new_layer_id=self._new_layer_id,
                canvas_width=canvas_spec.width,
                canvas_height=canvas_spec.height,
            )
            before_layer = existing[1] if existing is not None else None
            before_index = existing[0] if existing is not None else None
            after_index = before_index if before_index is not None else len(state.page.layers)
            transitions.append(
                MasterInstanceTransition(
                    page_id=page_id,
                    before_layer=before_layer,
                    before_index=before_index,
                    after_layer=after_layer,
                    after_index=after_index,
                )
            )
        if not preflight.is_ready() or (preparation is not None and not preparation.current()):
            logger.warning("apply_master_to_pages: target ownership changed during preflight")
            if preparation is not None:
                preparation.report("master target changed while building instances")
            return False
        command = ApplyMasterInstancesCommand(transitions=tuple(transitions))
        command._requires_checked_master = preparation is not None
        if not self._book_history.execute(command):
            return False
        if not command._requires_checked_master:
            self._emit_document_changed("pages.master_instances_changed")
            self._emit_pages_changed()
        return True

    def unlink_master_instance(self, page_id: PageId, master_id: str) -> bool:
        """Convert a page's instance into ordinary layers (CT-07 T3a).

        The marker goes away and the lock opens; the content stays
        (spec 07 §6.3).  One history entry.
        """
        from .master_instance_preparation import _master_facade_preparation  # noqa: PLC0415
        from .master_page_apply import (  # noqa: PLC0415
            find_master_instance,
            unlinked_instance_layer,
        )

        preparation = _master_facade_preparation(self, (page_id,))
        if preparation is not None:
            if not preparation.run():
                return False
            state = preparation.scope.targets[0][1]
        else:
            state = self._find_page_state(page_id)
            if state is not None:
                self._ensure_page_hydrated(state)
                if self._active_page_id == page_id:
                    self.persist_active_page()
                    state = self._find_page_state(page_id)
        if state is None:
            return False
        existing = find_master_instance(state.page.layers, master_id)
        if existing is None:
            return False
        index, layer, _ref = existing
        command = ApplyMasterInstancesCommand(
            transitions=(
                MasterInstanceTransition(
                    page_id=page_id,
                    before_layer=layer,
                    before_index=index,
                    after_layer=unlinked_instance_layer(layer),
                    after_index=index,
                ),
            )
        )
        if preparation is not None and not preparation.current():
            preparation.report("master target changed while unlinking instance")
            return False
        command._requires_checked_master = preparation is not None
        if not self._book_history.execute(command):
            return False
        if not command._requires_checked_master:
            self._emit_document_changed("pages.master_instances_changed")
            self._emit_pages_changed()
        return True

    def _apply_book_layout_transition(self, updates: Mapping[str, Any]) -> Any | None:
        """Core-internal bridge used only by ``SetBookLayoutCommand.apply()``.

        Returns ``None`` for both a semantic no-op and a missing port --
        either way the command must not enter history -- and lets a
        malformed-container ``ValueError`` propagate unconverted to
        ``PageCommandService``'s existing exception handling. Never
        emits any event or mutates page/history state itself; that is
        entirely ``DocumentMetadataService``'s responsibility on the
        other side of the port.
        """
        port = self._book_metadata_apply_transition
        if port is None:
            logger.debug("Book metadata port not bound; SetBookLayoutCommand cannot apply")
            return None
        return port(updates)

    def _restore_book_layout_namespace(self, snapshot: Any) -> bool:
        """Core-internal bridge used only by ``SetBookLayoutCommand``'s
        ``apply()`` (redo) and ``revert()`` (undo) via the same primitive.
        """
        port = self._book_metadata_restore_snapshot
        if port is None:
            logger.debug("Book metadata port not bound; SetBookLayoutCommand cannot restore")
            return False
        return bool(port(snapshot))

    def set_book_layout(self, updates: Mapping[str, Any]) -> bool:
        """Change book-level layout settings (binding, body start, folio).

        The caller B3-c2a left undefined: the Pages panel's binding control
        (CT-06). Goes through ``SetBookLayoutCommand`` and the document-wide
        book history, so the change is undoable and lands in the saved
        document -- unlike the Single/Spread view toggle beside it, which is
        UI state (spec §9).

        Returns ``False`` for a semantic no-op and for a document whose
        metadata port is not bound; neither is an error.

        Not part of the ``PageService`` Protocol or HostAPI: plugins reach it
        by duck typing, the same way they reach ``add_page_from_template``.
        """
        return self._set_book_layout(updates)

    def _set_book_layout(self, updates: Mapping[str, Any]) -> bool:
        """Core-internal facade: build and execute a ``SetBookLayoutCommand``.

        Owns no validation or mutation of its own -- ``_book_history.execute()``
        and the bound port handle all of it, exactly like the page-structure
        facades above delegate to their commands.
        """
        command = SetBookLayoutCommand(updates=updates)
        return bool(self._book_history.execute(command))

    # ------------------------------------------------------------------
    # Page-level book-attribute metadata (CT-06 Phase B3-c2b).  Unlike
    # SetBookLayoutCommand (B3-c2a), the owner here is PageServiceImpl
    # itself -- ``_PageState.page.metadata`` -- so no cross-service port
    # is needed; DocumentMetadataService is never involved.
    def _page_namespace_snapshot_from_root(
        self, root: Mapping[str, Any] | None
    ) -> _PageNamespaceSnapshot:
        """Namespace-scoped, read-only snapshot of a page metadata ``root``.

        ``root`` may legitimately be ``None`` (the common case for pages
        with no comic attributes yet) -- ``root_present`` records this so
        restore can reproduce ``None`` exactly rather than collapsing it
        into ``{}``. Fails closed with ``ValueError`` on any malformed
        container (root/``plugins``/page namespace present but not a
        Mapping, including an explicit ``None`` under an existing key)
        rather than silently treating it as absent.
        """
        if root is None:
            return _PageNamespaceSnapshot(
                root_present=False,
                plugins_present=False,
                namespace_present=False,
                namespace_value=None,
            )
        if not isinstance(root, Mapping):
            raise ValueError("page metadata root must be a Mapping or None")
        plugins_present = "plugins" in root
        if plugins_present and not isinstance(root["plugins"], Mapping):
            raise ValueError("metadata['plugins'] must be a Mapping")
        namespace_present = plugins_present and PAGE_METADATA_NAMESPACE in root["plugins"]
        if namespace_present and not isinstance(root["plugins"][PAGE_METADATA_NAMESPACE], Mapping):
            raise ValueError(f"metadata['plugins'][{PAGE_METADATA_NAMESPACE!r}] must be a Mapping")
        namespace_value = _codec_read_page_metadata(root) if namespace_present else None
        return _PageNamespaceSnapshot(
            root_present=True,
            plugins_present=plugins_present,
            namespace_present=namespace_present,
            namespace_value=namespace_value,
        )

    def _apply_page_book_attributes_transition(
        self, page_id: str, updates: Mapping[str, Any]
    ) -> _PageBookAttributesTransition | None:
        """Atomic do-side transition backing ``SetPageBookAttributesCommand``.

        Resolves the page fresh, validates the current root (fail-closed,
        before any mutation), merges via the existing page-namespace
        codec, and compares ``before``/``after`` at the *namespace* level
        (presence + value) rather than raw root equality -- the codec's
        ``None`` -> ``{}`` representational shift on an empty no-op merge
        must never be reported as a real change.

        Returns ``None`` for a missing page (internal failure -- the
        ``_set_page_book_attributes`` facade already reports this to the
        caller via ``PAGE_OP_FAILED`` before a command is ever built) or a
        semantic no-op. Raises ``ValueError`` for a malformed container.
        """
        state = self._find_page_state(page_id)
        if state is None:
            return None

        root = state.page.metadata
        before = self._page_namespace_snapshot_from_root(root)
        new_root = _codec_merge_page_metadata(root, updates)
        after = self._page_namespace_snapshot_from_root(new_root)

        if (
            before.namespace_present == after.namespace_present
            and before.namespace_value == after.namespace_value
        ):
            return None

        state.page.metadata = new_root
        state.dirty = True
        self._emit_document_changed("pages.book_attributes_changed")
        self._emit_page_dirty_changed(page_id, True)
        self._emit_pages_changed()
        return _PageBookAttributesTransition(before=before, after=after)

    def _restore_page_book_attributes_namespace(
        self, page_id: str, snapshot: _PageNamespaceSnapshot
    ) -> bool:
        """Namespace-scoped Undo/Redo restore for page book attributes.

        Always re-resolves the page by ``page_id`` (no index fallback)
        and re-reads its *current* ``metadata`` -- never a cached
        reference -- so any sibling plugin namespace or unrelated root
        key added since ``snapshot`` was captured is carried forward
        untouched. Returns ``False`` for a missing page (internal
        failure; the entry's custody stays with ``PageCommandService``,
        no ``PAGE_OP_FAILED``). Fails closed (``ValueError``, no
        mutation) on a malformed current root/``plugins``/page
        namespace -- a malformed *current* value is never popped or
        overwritten to silently "repair" it.
        """
        state = self._find_page_state(page_id)
        if state is None:
            return False

        root = state.page.metadata
        new_plugins: dict[str, Any]
        if root is None:
            new_plugins = {}
        else:
            if not isinstance(root, Mapping):
                raise ValueError("current page metadata root must be a Mapping or None")
            plugins_now_present = "plugins" in root
            if plugins_now_present and not isinstance(root["plugins"], Mapping):
                raise ValueError("current metadata['plugins'] must be a Mapping")
            namespace_now_present = (
                plugins_now_present and PAGE_METADATA_NAMESPACE in root["plugins"]
            )
            if namespace_now_present and not isinstance(
                root["plugins"][PAGE_METADATA_NAMESPACE], Mapping
            ):
                raise ValueError("current page namespace must be a Mapping")
            new_plugins = dict(root["plugins"]) if plugins_now_present else {}

        if snapshot.namespace_present:
            new_plugins[PAGE_METADATA_NAMESPACE] = _codec_read_page_metadata(
                {"plugins": {PAGE_METADATA_NAMESPACE: snapshot.namespace_value}}
            )
        else:
            new_plugins.pop(PAGE_METADATA_NAMESPACE, None)

        keep_plugins = bool(new_plugins) or snapshot.plugins_present
        new_root: dict[str, Any] = dict(root) if root is not None else {}
        if keep_plugins:
            new_root["plugins"] = new_plugins
        else:
            new_root.pop("plugins", None)

        keep_root = bool(new_root) or snapshot.root_present
        final: dict[str, Any] | None = new_root if keep_root else None

        if final == root:
            return True  # idempotent: already matches, success without mutation

        state.page.metadata = final
        state.dirty = True
        self._emit_document_changed("pages.book_attributes_changed")
        self._emit_page_dirty_changed(page_id, True)
        self._emit_pages_changed()
        return True

    def set_page_book_attributes(self, page_id: str, updates: Mapping[str, Any]) -> bool:
        """Change a page's comic attributes (role, folio mode).

        The caller B3-c2b left undefined: the Pages panel's role menu
        (CT-06 §6.2).  Goes through ``SetPageBookAttributesCommand`` and the
        document-wide book history, so the change is undoable and lands in
        the saved document.

        Returns ``False`` for a semantic no-op and for a missing page
        (which also reports ``PAGE_OP_FAILED``); neither builds a command.

        Not part of the ``PageService`` Protocol or HostAPI: plugins reach
        it by duck typing, the same way they reach ``set_book_layout``.
        """
        return self._set_page_book_attributes(page_id, updates)

    def _set_page_book_attributes(self, page_id: str, updates: Mapping[str, Any]) -> bool:
        """Core-internal facade: build and execute a
        ``SetPageBookAttributesCommand``.

        Not part of the ``PageService`` Protocol or HostAPI. Owns the
        *only* user-facing existence check: a missing page reports
        ``PAGE_OP_FAILED`` and never builds a command (no history entry,
        no sequence consumption). Once a command exists, any later
        missing-page situation encountered by undo/redo is an internal
        failure handled by the transition/restore helpers above, not by
        this facade.
        """
        if self._find_page_state(page_id) is None:
            self._emit_page_op_failed("book_attributes", page_id, "page not found")
            return False
        command = SetPageBookAttributesCommand(page_id=page_id, updates=updates)
        return bool(self._book_history.execute(command))

    # ------------------------------------------------------------------
    # Private mutation helpers shared by the page commands (CT-05 H3).
    # Commands call these — never the public facades — so do/undo/redo run
    # the exact same validation and event path without recursing into
    # command creation.
    def _insert_page_for_add(
        self,
        page: PageSnapshot,
        *,
        anchor_prev_page_id: str | None,
        fallback_index: int,
        change_type: str = "pages.add",
        op: str = "add",
        command: Any | None = None,
    ) -> bool:
        if self._find_page_state(page.id) is not None:
            self._emit_page_op_failed(op, page.id, "page already exists")
            return False
        anchor_index = (
            self._page_index(anchor_prev_page_id) if anchor_prev_page_id is not None else -1
        )
        if anchor_index >= 0:
            insert_index = anchor_index + 1
        else:
            insert_index = max(0, min(int(fallback_index), len(self._pages)))
        self._pages.insert(insert_index, _PageState(page=page, dirty=True))
        self._reindex_pages()
        # CT-05 H6: a redo re-inserts the page together with the content
        # history bundle the undo took custody of.  Restore it *before*
        # activating the page so the normal switch path attaches it.
        bundle = getattr(command, "history_bundle", None) if command is not None else None
        if bundle is not None and command is not None:
            if self._page_history.restore_bundle(bundle):
                command.history_bundle = None
            else:
                logger.warning(
                    "Re-inserted page %s kept its history bundle on the command",
                    page.id,
                )
        self._emit_document_changed(change_type)
        self._emit_pages_changed()
        self.set_active_page(page.id)
        return True

    def _remove_page_for_add_undo(
        self,
        page_id: str,
        *,
        previous_active_page_id: str | None,
        op: str = "add_undo",
        command: Any | None = None,
    ) -> bool:
        index = self._page_index(page_id)
        if index < 0:
            self._emit_page_op_failed(op, page_id, "page not found")
            return False
        if len(self._pages) <= 1:
            self._emit_page_op_failed(op, page_id, "cannot delete last page")
            return False
        was_active = self._active_page_id == page_id
        if was_active:
            # Keep the command's snapshot current so redo restores the page
            # exactly as it looked when the add was undone.
            self._persist_active_page()
        removed_state = self._pages[index]
        del self._pages[index]
        # Undoing an insert removes the page's runtime-only artefacts too:
        # its thumbnail cache must not resurface when redo re-inserts the
        # snapshot (thumbnails are never inherited).
        removed_state.page.page_thumbnail = None
        self._page_thumbnail_pngs.pop(page_id, None)
        # CT-05 H6: the page's content history moves into the command's
        # custody (single timeline — redo must bring the history back).
        # Without a command (defensive) the pre-H6 drop applies.
        bundle = self._page_history.take_bundle(page_id, is_active=was_active)
        if command is not None:
            command.history_bundle = bundle
        self._page_history.drop_page(page_id)
        self._reindex_pages()
        self._emit_document_changed("pages.delete")
        self._emit_pages_changed()
        if was_active:
            target_id: str | None = None
            if previous_active_page_id and self._find_page_state(previous_active_page_id):
                target_id = previous_active_page_id
            if target_id is None:
                new_index = max(0, min(index, len(self._pages) - 1))
                target_id = self._pages[new_index].page.id
            self.set_active_page(target_id)
        return True

    def _remove_page_for_delete_command(self, command: DeletePageCommand) -> bool:
        """Capture the page's current state into ``command`` and remove it.

        Runs for the first do and for every redo, so the payload always
        reflects the page as it was just before this removal (post-undo
        edits included).  Nothing is deep-copied: the ``PageSnapshot``,
        thumbnail bytes, and content history bundle move into the command.
        """
        checked = _delete_page_checked(self, command)
        if checked is not None:
            return checked
        page_id = command.page_id
        if len(self._pages) <= 1:
            self._emit_page_op_failed("delete", page_id, "cannot delete last page")
            return False
        index = self._page_index(page_id)
        if index < 0:
            self._emit_page_op_failed("delete", page_id, "page not found")
            return False
        was_active = self._active_page_id == page_id
        if was_active:
            # Commit any pending stroke through the H1 port, then persist
            # the live canvas content so the captured snapshot matches what
            # the user last saw.
            close_pending = getattr(self._canvas, "_history_close_pending_group", None)
            if callable(close_pending):
                close_pending()
            self._persist_active_page()
        state = self._pages[index]
        command.page = state.page
        command.dirty = state.dirty
        command.index = index
        command.prev_anchor_page_id = self._pages[index - 1].page.id if index > 0 else None
        command.next_anchor_page_id = (
            self._pages[index + 1].page.id if index + 1 < len(self._pages) else None
        )
        command.was_active = was_active
        command.previous_active_page_id = self._active_page_id
        command.thumbnail_png = self._page_thumbnail_pngs.pop(page_id, None)
        command.history_bundle = self._page_history.take_bundle(page_id, is_active=was_active)
        del self._pages[index]
        self._reindex_pages()
        self._emit_document_changed("pages.delete")
        self._emit_pages_changed()
        if was_active:
            new_index = max(0, min(index, len(self._pages) - 1))
            successor_id = self._pages[new_index].page.id
            command.successor_active_page_id = successor_id
            self.set_active_page(successor_id)
        else:
            command.successor_active_page_id = self._active_page_id
        return True

    def _restore_page_for_delete_undo(self, command: DeletePageCommand) -> bool:
        """Re-insert the deleted page with everything it owned.

        Position is resolved anchor-first (H3 rule): after the previous
        anchor, else before the next anchor, else the captured index
        clamped.  The content history bundle is handed back to the
        coordinator; re-activating the page attaches it through the
        normal page-switch path.
        """
        checked = _restore_deleted_page_checked(self, command)
        if checked is not None:
            return checked
        page_id = command.page_id
        page = command.page
        if page is None:
            self._emit_page_op_failed("delete_undo", page_id, "command was never applied")
            return False
        if self._find_page_state(page_id) is not None:
            self._emit_page_op_failed("delete_undo", page_id, "page already exists")
            return False
        prev_index = (
            self._page_index(command.prev_anchor_page_id)
            if command.prev_anchor_page_id is not None
            else -1
        )
        next_index = (
            self._page_index(command.next_anchor_page_id)
            if command.next_anchor_page_id is not None
            else -1
        )
        if prev_index >= 0:
            target = prev_index + 1
        elif next_index >= 0:
            target = next_index
        else:
            target = int(command.index)
        target = max(0, min(target, len(self._pages)))
        self._pages.insert(target, _PageState(page=page, dirty=command.dirty))
        self._reindex_pages()
        if command.thumbnail_png:
            self._page_thumbnail_pngs[page_id] = command.thumbnail_png
            command.thumbnail_png = None
        bundle = command.history_bundle
        if bundle is not None:
            if self._page_history.restore_bundle(bundle):
                command.history_bundle = None
            else:
                # Keep custody rather than losing history; restore_bundle
                # already logged why it refused.
                logger.warning(
                    "Delete-undo of page %s kept its history bundle on the command",
                    page_id,
                )
        self._emit_document_changed("pages.add")
        self._emit_pages_changed()
        if command.was_active:
            self.set_active_page(page_id)
        return True

    def _set_page_name(self, page_id: str, name: str | None) -> bool:
        state = self._find_page_state(page_id)
        if state is None:
            self._emit_page_op_failed("rename", page_id, "page not found")
            return False
        state.page.name = name
        state.dirty = True
        self._emit_document_changed("pages.rename")
        self._emit_page_dirty_changed(page_id, True)
        self._emit_pages_changed()
        return True

    def _move_page_to_anchor(
        self,
        page_id: str,
        *,
        prev_anchor_id: str | None,
        next_anchor_id: str | None,
        fallback_index: int,
    ) -> bool:
        current_index = self._page_index(page_id)
        if current_index < 0:
            self._emit_page_op_failed("move", page_id, "page not found")
            return False
        state = self._pages.pop(current_index)
        prev_index = self._page_index(prev_anchor_id) if prev_anchor_id is not None else -1
        next_index = self._page_index(next_anchor_id) if next_anchor_id is not None else -1
        if prev_index >= 0:
            target = prev_index + 1
        elif next_index >= 0:
            target = next_index
        else:
            logger.debug(
                "Move anchors for page %s are gone; falling back to index %s",
                page_id,
                fallback_index,
            )
            target = int(fallback_index)
        target = max(0, min(target, len(self._pages)))
        self._pages.insert(target, state)
        if target == current_index:
            return True
        self._reindex_pages()
        self._emit_document_changed("pages.move")
        self._emit_pages_changed()
        return True

    # ------------------------------------------------------------------
    # Internal helpers
    def _set_pages(
        self,
        pages: Sequence[PageSnapshot],
        *,
        active_page_id: str | None,
        apply_active: bool,
        dirty: bool,
        document_canvas: HostCanvasSpec | None = None,
    ) -> None:
        # PHASE 1 -- validate before mutating anything.
        #
        # Converting the incoming active page's layers is the only step here
        # that realistically fails (malformed image data, an unsupported canvas
        # implementation).  It used to run *after* the page list, the active
        # page id and both history stacks had already been replaced, wrapped in
        # a bare ``except`` that only logged -- so a document with one corrupt
        # layer "loaded" successfully while the canvas still held the previous
        # document, and the next save wrote those stale layers into the new
        # page.  Doing the conversion up front means a document that cannot be
        # applied is rejected outright, leaving the open document untouched.
        incoming = list(pages)
        prepared_layers: list[Any] | None = None
        if apply_active and incoming:
            target_id = active_page_id if active_page_id else None
            active_page = next(
                (page for page in incoming if page.id == target_id),
                incoming[0],
            )
            canvas_spec = self._resolve_canvas_spec(active_page, document_canvas=document_canvas)
            prepared_layers = self._prepare_page_layers(active_page, canvas_spec)

        # PHASE 2 -- commit.  Everything below is a plain assignment or a list
        # swap; the work that could fail has already succeeded.
        #
        # Document boundary: runtime page history never survives a page-set
        # replacement (load / reset / new document / save bootstrap).  The
        # boundary is one notifier batch (H5b) so the unified listener sees
        # only the final post-boundary state — never the half-cleared one.
        with self._history_state_notifier.batch():
            if document_canvas is not None:
                self._document_canvas = document_canvas
            self._reset_page_residency()
            self._bind_page_hydration_port(None)
            self._page_history.clear_all(reason="set_pages")
            self._book_history.clear(reason="set_pages")
            self._pages = [_PageState(page=page, dirty=dirty) for page in list(pages)]
            self._reindex_pages()
            self._prune_thumbnail_cache()
            if self._pages:
                if active_page_id and self._find_page_state(active_page_id):
                    self._active_page_id = active_page_id
                else:
                    self._active_page_id = self._pages[0].page.id
            else:
                self._active_page_id = None
            if apply_active and self._active_page_id:
                active_state = self._find_page_state(self._active_page_id)
                if active_state is not None:
                    # Reuses the layers converted in phase 1, so this cannot
                    # fail on the document's content; anything that does raise
                    # here propagates rather than being reported as success.
                    self._apply_page_to_canvas(active_state.page, prepared_layers)
                    self._reset_history_state(reason="page_load")
            self._emit_pages_changed()
            if self._active_page_id:
                self._emit_active_page_changed(self._active_page_id)
            self._history_state_notifier.mark_changed()

    def _page_index(self, page_id: str) -> int:
        for idx, state in enumerate(self._pages):
            if state.page.id == page_id:
                return idx
        return -1

    def _find_page_state(self, page_id: str) -> _PageState | None:
        for state in self._pages:
            if state.page.id == page_id:
                return state
        return None

    def _display_name(self, page: PageSnapshot, index: int) -> str:
        if page.name and page.name.strip():
            return page.name.strip()
        return f"Page {index + 1}"

    def _resolve_insert_index(self, after_page_id: str | None) -> int:
        if not self._pages:
            return 0
        if after_page_id:
            index = self._page_index(after_page_id)
            if index >= 0:
                return index + 1
        active_index = self._page_index(self.get_active_page_id())
        if active_index >= 0:
            return active_index + 1
        return len(self._pages)

    def _reindex_pages(self) -> None:
        for idx, state in enumerate(self._pages):
            state.page.index = idx

    def _next_page_id(self) -> str:
        existing = {state.page.id for state in self._pages}
        max_index = 0
        for page_id in existing:
            match = _PAGE_ID_PATTERN.match(page_id)
            if match:
                try:
                    max_index = max(max_index, int(match.group(1)))
                except ValueError:
                    continue
        for candidate in range(max_index + 1, max_index + 10000):
            page_id = f"p_{candidate:04d}"
            if page_id not in existing:
                return page_id
        while True:
            page_id = f"p_{uuid4().hex[:8]}"
            if page_id not in existing:
                return page_id

    def _new_layer_id(self) -> str:
        return f"layer-{uuid4().hex[:8]}"

    def _clone_layers(self, layers: Sequence[LayerSnapshot], *, blank: bool) -> list[LayerSnapshot]:
        """Clone a page layer tree without sharing mutable content.

        Page duplication is a snapshot operation, so every persisted layer
        field must survive it.  In particular, reconstructing only the
        raster-facing fields silently downgraded comic/vector/group layers
        and dropped their masks and effect payloads.  Layer IDs are the one
        intentional exception: duplicated layers need fresh IDs, including
        nested children, while vector object IDs remain stable within their
        page-local object payloads.
        """

        def clone_image(value: Any, *, clear: bool = False) -> NDArray[np.uint8] | None:
            if value is None:
                return None
            source = np.asarray(value)
            if clear:
                return self._blank_image(int(source.shape[1]), int(source.shape[0]))
            return source.copy()

        def clone_layer(layer: LayerSnapshot) -> LayerSnapshot:
            image = clone_image(layer.image, clear=blank)
            if image is None:
                raise ValueError(f"layer {layer.id!r} has no image data")
            return LayerSnapshot(
                id=self._new_layer_id(),
                name=layer.name,
                image=image,
                visible=layer.visible,
                opacity=float(layer.opacity),
                blend_mode=str(layer.blend_mode),
                locked=layer.locked,
                alpha_lock=layer.alpha_lock,
                position=copy.deepcopy(layer.position),
                metadata=copy.deepcopy(layer.metadata),
                bounds=copy.deepcopy(layer.bounds),
                layer_type=layer.layer_type,
                vector_kind=layer.vector_kind,
                vector_objects=copy.deepcopy(list(layer.vector_objects or [])),
                children=[clone_layer(child) for child in list(layer.children or [])],
                clipping=layer.clipping,
                layer_mask_image=clone_image(layer.layer_mask_image, clear=blank),
                mask_enabled=layer.mask_enabled,
                effect_type=layer.filter_type,
                effect_params=copy.deepcopy(dict(layer.filter_params or {})),
                effect_mask_image=clone_image(layer.filter_mask_image, clear=blank),
                ai_effect=copy.deepcopy(layer.ai_effect),
                origin=copy.deepcopy(layer.origin),
                scene_3d=copy.deepcopy(layer.scene_3d),
                camera_3d=copy.deepcopy(layer.camera_3d),
                lighting_3d=copy.deepcopy(layer.lighting_3d),
                display_mode_3d=layer.display_mode_3d,
                gen_3d=copy.deepcopy(layer.gen_3d),
                # The snapshot is immutable; applying it copies the live ink/alpha buffers.
                native_cmyk=layer.native_cmyk if not blank else None,
            )

        return [clone_layer(layer) for layer in layers]

    def _blank_image(self, width: int, height: int) -> NDArray[np.uint8]:
        return np.zeros((height, width, 4), dtype=np.uint8)

    def _resolve_canvas_spec(
        self,
        page: PageSnapshot | None,
        *,
        document_canvas: HostCanvasSpec | None = None,
    ) -> HostCanvasSpec:
        if page and page.canvas:
            return HostCanvasSpec(
                width=page.canvas.width,
                height=page.canvas.height,
                dpi=page.canvas.dpi,
                background_color=page.canvas.background_color,
            )
        # ``document_canvas`` lets a load validate against the incoming
        # document's spec before that spec has been committed to the service.
        pending = document_canvas if document_canvas is not None else self._document_canvas
        if pending is not None:
            return pending
        return self._fallback_canvas_spec(page)

    def _current_canvas_spec(self, page: PageSnapshot | None) -> HostCanvasSpec:
        """Spec of a page *as it stands now*, for reporting and authoring.

        The active page is the live canvas. Geometry events mark content dirty
        but do not synchronize its canvas spec; direct DPI updates emit no
        event. Reporting and authoring share the same ownership calculation.

        Deliberately *not* used by the page-apply path: when a page is being
        loaded into the canvas the document's spec is the authority, and the
        canvas still holds the previous document's geometry.
        """

        if page is not None and page.canvas:
            return current_page_canvas_spec(
                page.canvas,
                self._live_canvas_spec() if page.id == self._active_page_id else None,
                simple_canvas=isinstance(self._canvas, SimpleCanvasService),
            )
        if page is None or (self._active_page_id and page.id == self._active_page_id):
            live = self._live_canvas_spec()
            if live is not None:
                return live
        return self._resolve_canvas_spec(page)

    def _sync_document_canvas_with_live(self) -> None:
        """Synchronize live geometry with its page or document owner.

        Page-local overrides never become the document default. A page that
        inherits the default still synchronizes explicit resize/DPI changes.

        Called from the authoring entry points only.  Never from the
        load/apply path: there the incoming document is the authority and
        the canvas still holds the previous document's geometry.
        """

        if not self._pages:
            return
        live = self._live_canvas_spec()
        active = self._find_page_state(self._active_page_id or "")
        if active is not None and active.page.canvas is not None:
            active.page.canvas = merge_page_canvas(
                active.page.canvas,
                live,
                simple_canvas=isinstance(self._canvas, SimpleCanvasService),
            )
        elif live is not None:
            self._document_canvas = live

    def _document_canvas_for_save(self) -> HostCanvasSpec:
        active = self._find_page_state(self._active_page_id or "")
        return select_document_canvas(
            self._document_canvas,
            active.page.canvas if active is not None else None,
            self._live_canvas_spec(),
        )

    def _live_canvas_spec(self) -> HostCanvasSpec | None:
        """Canvas geometry as the canvas itself reports it, if it can."""

        size = self._canvas_size()
        if size is None:
            return None
        width, height = size
        if width <= 0 or height <= 0:
            return None
        return HostCanvasSpec(
            width=width,
            height=height,
            dpi=self._canvas_dpi(),
            background_color=self._canvas_background_color(),
        )

    def _canvas_size(self) -> tuple[int, int] | None:
        if isinstance(self._canvas, SimpleCanvasService):
            return (
                int(getattr(self._canvas, "_width", 0)),
                int(getattr(self._canvas, "_height", 0)),
            )
        if isinstance(self._canvas, LegacyCanvas):
            config = self._canvas.config
            return (int(config.width), int(config.height))
        return None

    def _fallback_canvas_spec(self, page: PageSnapshot | None) -> HostCanvasSpec:
        live = self._live_canvas_spec()
        if live is not None:
            return live
        # Last resort only: a layer may legitimately be smaller than the
        # canvas (it carries an offset), so its image size is a guess, not
        # the canvas geometry.  Trusting it produced absurd specs -- an
        # 8x6 "canvas" from a small pasted/AI layer, which then got baked
        # into a saved page template.
        width = 1920
        height = 1080
        if page and page.layers:
            image = page.layers[0].image
            if image is not None and image.ndim >= 2:
                height = int(image.shape[0])
                width = int(image.shape[1])
        return HostCanvasSpec(
            width=width,
            height=height,
            dpi=self._canvas_dpi(),
            background_color=self._canvas_background_color(),
        )

    def _canvas_background_color(self) -> tuple[int, int, int, int]:
        if isinstance(self._canvas, SimpleCanvasService):
            color = getattr(self._canvas, "_background", QColor(255, 255, 255))
            return (color.red(), color.green(), color.blue(), color.alpha())
        if isinstance(self._canvas, LegacyCanvas):
            color_raw = tuple(int(component) for component in self._canvas.config.background_color)
            if len(color_raw) == 3:
                return (*color_raw, 255)
            return color_raw
        return (255, 255, 255, 255)

    def _canvas_dpi(self) -> tuple[float, float]:
        if isinstance(self._canvas, SimpleCanvasService):
            dpi_value = float(getattr(self._canvas, "_dpi", 72))
            return (dpi_value, dpi_value)
        if isinstance(self._canvas, LegacyCanvas):
            raw = getattr(self._canvas.config, "dpi", (72.0, 72.0))
            if isinstance(raw, (int, float)):
                return (float(raw), float(raw))
            try:
                values = [float(item) for item in list(raw)[:2]]
            except Exception:
                values = []
            if not values:
                return (72.0, 72.0)
            if len(values) == 1:
                values.append(values[0])
            return (values[0], values[1])
        return (72.0, 72.0)

    def _host_canvas_spec_from_metadata(self, metadata: DocumentMetadata) -> HostCanvasSpec:
        return HostCanvasSpec(
            width=metadata.width,
            height=metadata.height,
            dpi=metadata.dpi,
            background_color=metadata.background_color,
        )

    @contextmanager
    def _suppress_document_change_events(self) -> Iterator[None]:
        self._suppress_doc_changed += 1
        try:
            yield
        finally:
            self._suppress_doc_changed = max(0, self._suppress_doc_changed - 1)

    def _prepare_page_layers(
        self,
        page: PageSnapshot,
        canvas_spec: HostCanvasSpec,
    ) -> list[Any]:
        """Convert *page*'s layers for the active canvas, mutating nothing.

        This is the only realistically failing part of applying a page --
        malformed image data raises out of the snapshot conversion, and an
        unrecognised canvas implementation raises here too.  Running it before
        any state changes is what lets a bad document be rejected outright
        instead of half-applied (see ``_set_pages``).
        """
        if isinstance(self._canvas, SimpleCanvasService):
            return list(self._snapshots_to_layers(page.layers, canvas_spec))
        if isinstance(self._canvas, LegacyCanvas):
            return list(self._legacy_layers_from_snapshots(page.layers, canvas_spec))
        raise RuntimeError("Unsupported canvas implementation")

    def _apply_page_to_canvas(
        self,
        page: PageSnapshot,
        prepared_layers: list[Any] | None = None,
    ) -> None:
        canvas_spec = self._resolve_canvas_spec(page)
        with self._suppress_document_change_events():
            suspend = getattr(self._canvas, "suspend_doc_events", None)
            if callable(suspend):
                with suspend():
                    self._apply_page_layers(page, canvas_spec, prepared_layers)
            else:
                self._apply_page_layers(page, canvas_spec, prepared_layers)

    def _apply_page_layers(
        self,
        page: PageSnapshot,
        canvas_spec: HostCanvasSpec,
        prepared_layers: list[Any] | None = None,
    ) -> None:
        if isinstance(self._canvas, SimpleCanvasService):
            self._apply_to_simple_canvas(self._canvas, page.layers, canvas_spec, prepared_layers)
        elif isinstance(self._canvas, LegacyCanvas):
            self._apply_to_legacy_canvas(self._canvas, page.layers, canvas_spec, prepared_layers)
        else:
            raise RuntimeError("Unsupported canvas implementation")

    def _persist_active_page(
        self,
        *,
        raise_on_error: bool = False,
        scope_guard: Callable[[], bool] | None = None,
    ) -> None:
        active_id = self._active_page_id
        if not active_id:
            return
        state = self._find_page_state(active_id)
        if state is None:
            return
        filter_pen = getattr(
            self._canvas, "_filter_pen_service", getattr(self._canvas, "_effect_pen_service", None)
        )
        if filter_pen is not None:
            filter_pen.finalize()
        if scope_guard is not None and not scope_guard():
            return
        try:
            layers = self._capture_layers()
            if scope_guard is None or scope_guard():
                state.page.layers = layers
        except Exception as exc:
            if raise_on_error:
                raise
            logger.debug("Failed to capture active page %s: %s", active_id, exc)

    def _apply_to_simple_canvas(
        self,
        canvas: SimpleCanvasService,
        layers: Sequence[LayerSnapshot],
        canvas_spec: HostCanvasSpec,
        prepared_layers: list[Any] | None = None,
    ) -> None:
        # Convert first when the caller has not already done so: the geometry
        # writes below are mutations, and a conversion failure after
        # them leaves the canvas with the new geometry and the old pixels.
        q_layers = (
            list(prepared_layers)
            if prepared_layers is not None
            else self._snapshots_to_layers(layers, canvas_spec)
        )
        _install_page_canvas_geometry(canvas, canvas_spec)
        if not q_layers and _supports_page_recovery(canvas):
            canvas._replace_layers_for_page(q_layers)
        else:
            canvas.replace_layers(q_layers)

    def _snapshots_to_layers(
        self,
        snapshots: Sequence[LayerSnapshot],
        canvas_spec: HostCanvasSpec,
    ) -> list[_Layer]:
        return [self._snapshot_to_layer(snapshot, canvas_spec) for snapshot in snapshots]

    def _snapshot_to_layer(
        self,
        snapshot: LayerSnapshot,
        canvas_spec: HostCanvasSpec,
    ) -> _Layer:
        del canvas_spec
        return DocumentSnapshotBuilder.snapshot_to_simple_layer(snapshot)

    def _legacy_layers_from_snapshots(
        self,
        layers: Sequence[LayerSnapshot],
        canvas_spec: HostCanvasSpec,
    ) -> list[Layer]:
        """Build legacy ``Layer`` objects without touching the canvas."""
        new_layers: list[Layer] = []
        for snapshot in layers:
            layer = Layer(name=snapshot.name, width=canvas_spec.width, height=canvas_spec.height)
            layer.id = snapshot.id
            layer.opacity = float(snapshot.opacity)
            layer.visible = snapshot.visible
            layer.locked = snapshot.locked
            cast(Any, layer).alpha_lock = getattr(snapshot, "alpha_lock", False)
            layer.blend_mode = snapshot.blend_mode
            layer.set_image_data(snapshot.image)
            new_layers.append(layer)
        return new_layers

    def _apply_to_legacy_canvas(
        self,
        canvas: LegacyCanvas,
        layers: Sequence[LayerSnapshot],
        canvas_spec: HostCanvasSpec,
        prepared_layers: list[Any] | None = None,
    ) -> None:
        new_layers: list[Layer] = (
            list(prepared_layers)
            if prepared_layers is not None
            else self._legacy_layers_from_snapshots(layers, canvas_spec)
        )
        canvas.layers = new_layers
        canvas.config.width = canvas_spec.width
        canvas.config.height = canvas_spec.height
        canvas.config.background_color = canvas_spec.background_color
        canvas._active_index = len(new_layers) - 1
        canvas._invalidate_composite()

    def _capture_layers(self) -> list[LayerSnapshot]:
        if isinstance(self._canvas, SimpleCanvasService):
            return self._capture_simple_canvas_layers(self._canvas)
        if isinstance(self._canvas, LegacyCanvas):
            return self._capture_legacy_canvas_layers(self._canvas)
        raise RuntimeError("Unsupported canvas implementation")

    def _capture_simple_canvas_layers(self, canvas: SimpleCanvasService) -> list[LayerSnapshot]:
        capture = getattr(canvas, "get_document_layer_snapshots", None)
        if callable(capture):
            return list(capture())
        return DocumentSnapshotBuilder.capture_simple_canvas_layers(canvas)

    def _capture_layer_list(
        self, layers: list[_Layer], canvas: SimpleCanvasService
    ) -> list[LayerSnapshot]:
        return DocumentSnapshotBuilder.capture_layer_list(layers, canvas)

    def _capture_single_layer(self, layer: _Layer, canvas: SimpleCanvasService) -> LayerSnapshot:
        return DocumentSnapshotBuilder.capture_single_layer(layer, canvas)

    def _capture_legacy_canvas_layers(self, canvas: LegacyCanvas) -> list[LayerSnapshot]:
        return DocumentSnapshotBuilder.capture_legacy_canvas_layers(canvas)

    def _ensure_rgba(self, data: NDArray[Any]) -> NDArray[np.uint8]:
        arr = np.asarray(data)
        if arr.ndim != 3:
            raise ValueError("image data must be HxWxC")
        if arr.shape[2] == 3:
            alpha = np.full((arr.shape[0], arr.shape[1], 1), 255, dtype=arr.dtype)
            arr = np.concatenate([arr, alpha], axis=2)
        if arr.dtype != np.uint8:
            arr = np.clip(arr, 0, 255).astype(np.uint8)
        return arr

    def _array_to_qimage(
        self,
        data: NDArray[np.uint8],
        width: int,
        height: int,
    ) -> QImage:
        arr = self._ensure_rgba(data)
        if arr.shape[0] != height or arr.shape[1] != width:
            arr = np.pad(
                arr,
                (
                    (0, max(0, height - arr.shape[0])),
                    (0, max(0, width - arr.shape[1])),
                    (0, 0),
                ),
                mode="constant",
            )
            arr = arr[:height, :width]
        arr = np.ascontiguousarray(arr)
        qimage = QImage(arr.data, width, height, arr.strides[0], QImage.Format.Format_RGBA8888)
        converted = qimage.copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        return converted

    def _array_to_qimage_native_size(self, data: NDArray[np.uint8]) -> QImage:
        arr = np.ascontiguousarray(self._ensure_rgba(data))
        height, width = arr.shape[:2]
        qimage = QImage(arr.data, width, height, arr.strides[0], QImage.Format.Format_RGBA8888)
        converted = qimage.copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
        return converted

    def _qimage_to_array(self, image: QImage) -> NDArray[np.uint8]:
        converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
        byte_length = converted.bytesPerLine() * converted.height()
        buffer = converted.bits()
        if hasattr(buffer, "setsize"):
            buffer.setsize(byte_length)
            data_view = buffer
        else:
            data_view = buffer if isinstance(buffer, memoryview) else memoryview(buffer)
        arr = np.frombuffer(data_view, dtype=np.uint8, count=byte_length).reshape(
            converted.height(), converted.bytesPerLine()
        )
        arr = arr[:, : converted.width() * 4]
        return arr.reshape(converted.height(), converted.width(), 4).copy()

    def _to_blend_mode(self, name: str) -> BlendMode:
        normalized = str(name).replace(" ", "_").upper()
        try:
            return BlendMode[normalized]
        except KeyError:
            return BlendMode.NORMAL

    def _reset_history_state(self, *, reason: str) -> None:
        clear_fn = getattr(self._canvas, "history_clear", None)
        if callable(clear_fn):
            try:
                clear_fn()
                payload = HistoryResetEvent(
                    reason=reason,
                    source=self.__class__.__name__,
                    timestamp=time.time(),
                )
                self._emit_event(Events.DOC_HISTORY_RESET, payload)
            except Exception as exc:
                logger.debug("history_clear failed: %s", exc)

    def _emit_document_changed(self, change_type: str) -> None:
        if _stage_page_completion(self, "_emit_document_changed", change_type):
            return
        delivery = _capture_page_completion_delivery(self)
        if change_type in {"pages.add", "pages.delete", "pages.duplicate"}:
            self.document_stories.refresh_geometry()
        if delivery is not None and not delivery.is_current():
            return
        payload = DocumentChangeEvent(change_type=change_type)
        self._emit_event(Events.DOC_CHANGED, payload)

    def _emit_pages_changed(self) -> None:
        if _stage_page_completion(self, "_emit_pages_changed"):
            return
        delivery = _capture_page_completion_delivery(self)
        payload = {"pages": list(self.list_pages())}
        self._emit_event(Events.PAGES_CHANGED, payload)
        if delivery is not None and not delivery.is_current():
            return
        self._emit_cached_page_thumbnails()

    def _emit_page_thumbnail_updated(self, page_id: str, thumbnail_png: bytes) -> None:
        if _stage_page_completion(self, "_emit_page_thumbnail_updated", page_id, thumbnail_png):
            return
        payload = {
            "page_id": page_id,
            "thumbnail_png": thumbnail_png,
            "error": None,
        }
        self._emit_event(Events.PAGE_THUMBNAIL_UPDATED, payload)

    def _emit_cached_page_thumbnails(self) -> None:
        delivery = _capture_page_completion_delivery(self)
        for state in tuple(self._pages):
            if delivery is not None and not delivery.is_current():
                break
            thumbnail_png = self._page_thumbnail_pngs.get(state.page.id)
            if thumbnail_png:
                self._emit_page_thumbnail_updated(state.page.id, thumbnail_png)

    def _prune_thumbnail_cache(self) -> None:
        valid_ids = {state.page.id for state in self._pages}
        stale_ids = set(self._page_thumbnail_pngs) - valid_ids
        for page_id in stale_ids:
            del self._page_thumbnail_pngs[page_id]

    def _page_thumbnail_path(self, page_id: str) -> str:
        return f"{PAGES_DIR}/{page_id}/{PAGE_THUMBNAIL_NAME}"

    def _emit_active_page_changed(self, page_id: str) -> None:
        if _stage_page_completion(self, "_emit_active_page_changed", page_id):
            return
        self._emit_event(Events.ACTIVE_PAGE_CHANGED, {"page_id": page_id})

    def _emit_page_dirty_changed(self, page_id: str, dirty: bool) -> None:
        if _stage_page_completion(self, "_emit_page_dirty_changed", page_id, dirty):
            return
        payload = {"page_id": page_id, "dirty": bool(dirty)}
        self._emit_event(Events.PAGE_DIRTY_CHANGED, payload)

    def _emit_page_op_failed(self, op: str, page_id: str, error: str) -> None:
        if _stage_page_completion(self, "_emit_page_op_failed", op, page_id, error, failure=True):
            return
        error_lower = error.lower()
        if "page not found" in error_lower:
            reason_code = "page_not_found"
        elif "cannot delete last page" in error_lower:
            reason_code = "page_last_delete_denied"
        elif "template not found" in error_lower:
            reason_code = "page_template_not_found"
        elif "name" in error_lower:
            reason_code = "page_invalid_name"
        else:
            reason_code = "page_operation_failed"
        payload = build_error_payload(
            op=op,
            page_id=page_id,
            reason_code=reason_code,
            user_visible_message="Page operation failed",
            error=error,
            debug_details_without_secret={"op": op, "page_id": page_id},
        )
        self._emit_event(Events.PAGE_OP_FAILED, payload)

    def _emit_event(self, event: str, payload: Any) -> None:
        publish = getattr(self._event_bus, "publish", None)
        if callable(publish):
            publish(event, payload)
            return
        emit = getattr(self._event_bus, "emit", None)
        if callable(emit):
            emit(event, payload)

    def _subscribe_doc_changed(self) -> None:
        subscribe = getattr(self._event_bus, "on", None)
        if not callable(subscribe):
            subscribe = getattr(self._event_bus, "subscribe", None)
        if callable(subscribe):
            self._doc_changed_token = subscribe(Events.DOC_CHANGED, self._doc_changed_handler)

    def _on_document_changed(self, payload: Any) -> None:
        if self._suppress_doc_changed > 0:
            return
        if isinstance(payload, DocumentChangeEvent):
            if payload.change_type.startswith("pages."):
                return
            if payload.change_type == "new_document":
                return
            if payload.change_type == "book.layout_changed":
                # CT-06 Phase B3-c2a: document-level book-layout metadata
                # is not page content. DOC_CHANGED still advances document
                # dirty/revision (DocumentRevisionTracker subscribes to
                # the same event independently), but it must never mark
                # the active page dirty or emit PAGE_DIRTY_CHANGED. Exact
                # match only -- a future, different book-related
                # change_type is not implicitly covered.
                return
        active_id = self._active_page_id
        if not active_id:
            return
        state = self._find_page_state(active_id)
        if state is None:
            return
        if not state.dirty:
            state.dirty = True
            self._emit_page_dirty_changed(active_id, True)


_DEFAULT_PAGE_PERSIST = PageServiceImpl._persist_active_page
_DEFAULT_PAGE_PUBLIC_PERSIST = PageServiceImpl.persist_active_page
_DEFAULT_PAGE_SET_ACTIVE = PageServiceImpl.set_active_page
_DEFAULT_PAGE_APPLY = PageServiceImpl._apply_page_to_canvas
_DEFAULT_MASTER_INSTANCE_SET = PageServiceImpl._set_master_instance_layer

__all__ = ["PageServiceImpl"]
