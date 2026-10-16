"""Page structure commands for the runtime-only book history (CT-05 H3).

These commands are a core-internal abstraction: they operate on
``PageServiceImpl`` private mutation helpers, not on the canvas, and are
deliberately NOT the generic ``core.command_types.Command`` (whose
``apply(canvas)`` contract targets canvas content).  They are never
exposed through HostAPI / the ``PageService`` Protocol and are never
persisted.

Redo determinism: every id, snapshot, and anchor is fixed when the
command object is built or first applied — ``apply()`` on redo reuses the
same payload and never regenerates ids, re-evaluates templates, or reads
clocks/randomness.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from loomstroke.core.page_history_memory import (
    estimate_layer_snapshot_bytes,
    estimate_metadata_payload_bytes,
    estimate_page_history_bundle_bytes,
    estimate_page_snapshot_bytes,
)

if TYPE_CHECKING:
    from loomstroke.core.canvas_history_port import PageHistoryBundle
    from loomstroke.core.document_metadata_service import _BookNamespaceSnapshot
    from loomstroke.core.page_service import _PageNamespaceSnapshot
    from loomstroke.io.layer_io import PageSnapshot


class PageCommand(Protocol):
    """Core-internal contract consumed by ``PageCommandService``."""

    label_key: str
    affected_page_ids: tuple[str, ...]

    def apply(self, service: Any) -> bool: ...

    def revert(self, service: Any) -> bool: ...


@dataclass(slots=True)
class AddPageCommand:
    """Insert a pre-built page snapshot and make it active.

    The ``PageSnapshot`` (with its page id and layer ids) is created by
    the ``PageServiceImpl`` facade before the command is executed;
    redo re-inserts the identical snapshot object.
    """

    page: PageSnapshot
    anchor_prev_page_id: str | None
    fallback_index: int
    previous_active_page_id: str | None
    label_key: str = "page.add"
    affected_page_ids: tuple[str, ...] = field(default=())
    # True while the page snapshot lives only on this command (page undone
    # out of the document).  Updated on successful apply/revert only.
    retains_off_document_payload: bool = False
    # CT-05 H6: undoing the add takes custody of the page's content
    # history bundle (single-timeline rule — redo must restore the page
    # *with* its history so the global replay has no holes).
    history_bundle: PageHistoryBundle | None = None

    def __post_init__(self) -> None:
        self.affected_page_ids = (self.page.id,)

    def apply(self, service: Any) -> bool:
        succeeded = bool(
            service._insert_page_for_add(
                self.page,
                anchor_prev_page_id=self.anchor_prev_page_id,
                fallback_index=self.fallback_index,
                command=self,
            )
        )
        if succeeded:
            self.retains_off_document_payload = False
        return succeeded

    def revert(self, service: Any) -> bool:
        succeeded = bool(
            service._remove_page_for_add_undo(
                self.page.id,
                previous_active_page_id=self.previous_active_page_id,
                command=self,
            )
        )
        if succeeded:
            self.retains_off_document_payload = True
        return succeeded

    def estimated_retained_bytes(self, seen: set[int]) -> int:
        if not self.retains_off_document_payload:
            return 0
        total = estimate_page_snapshot_bytes(self.page, seen=seen)
        if self.history_bundle is not None:
            total += estimate_page_history_bundle_bytes(self.history_bundle, seen=seen)
        return total


@dataclass(slots=True)
class DuplicatePageCommand:
    """Insert a pre-built duplicate snapshot next to its source page.

    The duplicated ``PageSnapshot`` (new page id, new layer ids, cloned
    raster buffers, copy name) is fixed once by the ``PageServiceImpl``
    facade; redo re-inserts the identical object without re-running id
    generation or layer cloning.  Undo removes only the duplicate and
    restores the previously active page — unlike the future
    ``DeletePageCommand`` (H4b), nothing of the source page needs saving.
    """

    source_page_id: str
    page: PageSnapshot
    anchor_prev_page_id: str | None
    fallback_index: int
    previous_active_page_id: str | None
    label_key: str = "page.duplicate"
    affected_page_ids: tuple[str, ...] = field(default=())
    # True while the duplicate snapshot lives only on this command.
    retains_off_document_payload: bool = False
    # CT-05 H6: same custody rule as AddPageCommand — undoing the
    # duplicate keeps the page's content history for a hole-free redo.
    history_bundle: PageHistoryBundle | None = None

    def __post_init__(self) -> None:
        self.affected_page_ids = (self.page.id, self.source_page_id)

    def apply(self, service: Any) -> bool:
        succeeded = bool(
            service._insert_page_for_add(
                self.page,
                anchor_prev_page_id=self.anchor_prev_page_id,
                fallback_index=self.fallback_index,
                change_type="pages.duplicate",
                op="duplicate",
                command=self,
            )
        )
        if succeeded:
            self.retains_off_document_payload = False
        return succeeded

    def revert(self, service: Any) -> bool:
        succeeded = bool(
            service._remove_page_for_add_undo(
                self.page.id,
                previous_active_page_id=self.previous_active_page_id,
                op="duplicate_undo",
                command=self,
            )
        )
        if succeeded:
            self.retains_off_document_payload = True
        return succeeded

    def estimated_retained_bytes(self, seen: set[int]) -> int:
        if not self.retains_off_document_payload:
            return 0
        total = estimate_page_snapshot_bytes(self.page, seen=seen)
        if self.history_bundle is not None:
            total += estimate_page_history_bundle_bytes(self.history_bundle, seen=seen)
        return total


@dataclass(slots=True)
class DeletePageCommand:
    """Delete an existing page; undo restores it completely.

    Unlike Add/Duplicate undo (which merely discard an inserted page),
    this command takes custody of everything the page owns at delete
    time: the ``PageSnapshot`` object itself (moved, never deep-copied),
    its dirty flag, runtime thumbnail bytes, and its content history
    bundle.  Every ``apply()`` — the first do and every redo — re-captures
    the page's *current* state through the service helper, so an undo
    after "undo, edit, redo" restores the latest edits, not a stale
    payload.  The payload is runtime-only and never persisted.
    """

    page_id: str
    page: PageSnapshot | None = None
    dirty: bool = False
    index: int = 0
    prev_anchor_page_id: str | None = None
    next_anchor_page_id: str | None = None
    was_active: bool = False
    previous_active_page_id: str | None = None
    successor_active_page_id: str | None = None
    thumbnail_png: bytes | None = None
    history_bundle: PageHistoryBundle | None = None
    label_key: str = "page.delete"
    affected_page_ids: tuple[str, ...] = field(default=())
    # True while this command owns the deleted page's snapshot, thumbnail
    # bytes, and content history bundle (page removed from the document).
    retains_off_document_payload: bool = False

    def __post_init__(self) -> None:
        self.affected_page_ids = (self.page_id,)

    def apply(self, service: Any) -> bool:
        succeeded = bool(service._remove_page_for_delete_command(self))
        if succeeded:
            self.retains_off_document_payload = True
        return succeeded

    def revert(self, service: Any) -> bool:
        succeeded = bool(service._restore_page_for_delete_undo(self))
        if succeeded:
            self.retains_off_document_payload = False
        return succeeded

    def estimated_retained_bytes(self, seen: set[int]) -> int:
        if not self.retains_off_document_payload:
            return 0
        total = 0
        if self.page is not None:
            total += estimate_page_snapshot_bytes(self.page, seen=seen)
        if self.thumbnail_png:
            total += len(self.thumbnail_png)
        if self.history_bundle is not None:
            total += estimate_page_history_bundle_bytes(self.history_bundle, seen=seen)
        return total


@dataclass(slots=True)
class RenamePageCommand:
    """Set a page's normalized name; undo restores the previous value.

    Names are stored as source values (already normalized by the facade)
    — never translated display strings.  The page is addressed by id so
    the command survives later reorders.
    """

    page_id: str
    old_name: str | None
    new_name: str | None
    label_key: str = "page.rename"
    affected_page_ids: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        self.affected_page_ids = (self.page_id,)

    def apply(self, service: Any) -> bool:
        return bool(service._set_page_name(self.page_id, self.new_name))

    def revert(self, service: Any) -> bool:
        return bool(service._set_page_name(self.page_id, self.old_name))


@dataclass(slots=True)
class MovePageCommand:
    """Move a page between anchor pages; undo restores the old anchors.

    Anchors are page ids captured on both sides of the moved page before
    and after the move, so the logical position survives interleaved
    structure changes better than a raw index.  The captured indices are
    clamped fallbacks for when every anchor has disappeared.
    """

    page_id: str
    old_prev_anchor_id: str | None
    old_next_anchor_id: str | None
    old_index: int
    new_prev_anchor_id: str | None
    new_next_anchor_id: str | None
    new_index: int
    label_key: str = "page.move"
    affected_page_ids: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        self.affected_page_ids = (self.page_id,)

    def apply(self, service: Any) -> bool:
        return bool(
            service._move_page_to_anchor(
                self.page_id,
                prev_anchor_id=self.new_prev_anchor_id,
                next_anchor_id=self.new_next_anchor_id,
                fallback_index=self.new_index,
            )
        )

    def revert(self, service: Any) -> bool:
        return bool(
            service._move_page_to_anchor(
                self.page_id,
                prev_anchor_id=self.old_prev_anchor_id,
                next_anchor_id=self.old_next_anchor_id,
                fallback_index=self.old_index,
            )
        )


@dataclass(slots=True)
class MoveSpreadCommand:
    """Move every page of one spread as a single history entry (CT-06 B4-a).

    A spread holds one or two real pages, and moving it has to stay atomic
    from the user's point of view: one Undo puts *both* pages back where they
    were. ``PageCommandService`` executes a single command per history entry
    and there is no grouping layer, so the whole move lives in this one
    command rather than in several chained ``MovePageCommand`` entries.

    Positions are recorded as anchors -- the page ids on either side -- for
    the same reason ``MovePageCommand`` does: an index goes stale as soon as
    another structural edit lands between apply and revert, while the anchors
    still describe where the page belongs.

    ``moves`` is in the spread's own page order, and *both* directions walk it
    forwards. Each page after the first is anchored to the partner ahead of it,
    so that partner has to be placed first -- reversing the walk would anchor
    against a page that has not moved yet and leave the block where it was.
    """

    moves: tuple[MovePageCommand, ...]
    label_key: str = "spread.move"
    affected_page_ids: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        self.affected_page_ids = tuple(move.page_id for move in self.moves)

    def apply(self, service: Any) -> bool:
        if not self.moves:
            return False
        applied: list[MovePageCommand] = []
        for move in self.moves:
            if not move.apply(service):
                # Roll the partial move back so a failure leaves the page
                # order untouched rather than half-applied.
                for done in reversed(applied):
                    done.revert(service)
                return False
            applied.append(move)
        return True

    def revert(self, service: Any) -> bool:
        if not self.moves:
            return False
        reverted: list[MovePageCommand] = []
        for move in self.moves:
            if not move.revert(service):
                for done in reversed(reverted):
                    done.apply(service)
                return False
            reverted.append(move)
        return True


@dataclass(slots=True)
class SetBookLayoutCommand:
    """Document-global book-layout metadata update (CT-06 Phase B3-c2a).

    Unlike the page-structure commands above, this command has no page of
    its own (``affected_page_ids=()``) and never touches
    ``PageServiceImpl``'s page list. It bridges to
    ``DocumentMetadataService`` through a limited, post-construction port
    (``PageServiceImpl._bind_book_metadata_port()``) instead of a direct
    reference, so ``PageServiceImpl`` never imports
    ``DocumentMetadataService``/``DocumentServiceImpl``.

    Exact Undo/Redo is namespace-scoped: only
    ``metadata["plugins"]["loomstroke.comictools.book"]`` is captured and
    restored via ``DocumentMetadataService._restore_book_namespace()`` —
    a sibling plugin namespace or unrelated root key changed after this
    command's own ``apply()`` is never reverted.

    Redo determinism: ``updates`` and both namespace snapshots are fixed
    once, on the first successful ``apply()``. Redo never re-merges
    ``updates`` — it replays the fixed ``after`` snapshot through the
    same restore primitive Undo uses, so a normal save that replaced the
    document metadata *owner object* in between still restores correctly
    (only the book namespace subtree is touched; the save's own changes
    to sibling/root data are preserved).
    """

    updates: Mapping[str, Any]
    before_snapshot: _BookNamespaceSnapshot | None = field(default=None, repr=False)
    after_snapshot: _BookNamespaceSnapshot | None = field(default=None, repr=False)
    label_key: str = "book.layout_changed"
    affected_page_ids: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        # Defensive copy: the command's fixed payload must never alias the
        # caller's dict/nested values (metadata is JSON-like: dict/list/
        # scalar, so plain deepcopy is safe and needs no codec helper).
        self.updates = copy.deepcopy(dict(self.updates))

    def apply(self, service: Any) -> bool:
        if self.after_snapshot is None:
            # First application: merge-based, via the atomic
            # DocumentMetadataService transition. ``None`` covers both a
            # semantic no-op and a missing port (PageServiceImpl's helper
            # returns ``None`` for both) -- either way this command must
            # not enter history. A malformed container raises and
            # propagates to ``PageCommandService``'s existing exception
            # handling, unconverted.
            transition = service._apply_book_layout_transition(self.updates)
            if transition is None:
                return False
            self.before_snapshot = transition.before
            self.after_snapshot = transition.after
            return True
        # Redo: replay the fixed ``after`` snapshot -- never re-merge.
        return bool(service._restore_book_layout_namespace(self.after_snapshot))

    def revert(self, service: Any) -> bool:
        if self.before_snapshot is None:
            return False  # never successfully applied
        return bool(service._restore_book_layout_namespace(self.before_snapshot))

    def estimated_retained_bytes(self, seen: set[int]) -> int:
        # Unlike Add/Duplicate/Delete, this payload is never "on the live
        # document" -- the document only ever holds the *current*
        # namespace value, never past ones -- so it is always counted
        # once applied, with no on/off-document gating flag.
        total = estimate_metadata_payload_bytes(self.updates, seen=seen)
        if self.before_snapshot is not None:
            total += estimate_metadata_payload_bytes(
                self.before_snapshot.namespace_value, seen=seen
            )
        if self.after_snapshot is not None:
            total += estimate_metadata_payload_bytes(self.after_snapshot.namespace_value, seen=seen)
        return total


@dataclass(slots=True)
class SetPageBookAttributesCommand:
    """Page-level book-attribute metadata update (CT-06 Phase B3-c2b).

    Unlike ``SetBookLayoutCommand`` (document-global, no owning page),
    this command targets one specific page (``affected_page_ids=(page_id,)``)
    and bridges directly to ``PageServiceImpl`` -- the page's
    ``PageSnapshot.metadata`` is owned entirely by ``PageServiceImpl``
    itself, so no cross-service port (unlike B3-c2a's
    ``DocumentMetadataService`` bridge) is needed here.

    Exact Undo/Redo is namespace-scoped: only
    ``metadata["plugins"]["loomstroke.comictools.page"]`` is captured and
    restored via ``PageServiceImpl._restore_page_book_attributes_namespace()``
    -- a sibling plugin namespace, unrelated root key, or the page
    metadata root's own None/dict presence is always read from the
    *current* page and carried forward untouched.

    Redo determinism: ``updates`` and both namespace snapshots are fixed
    once, on the first successful ``apply()``. Redo never re-merges
    ``updates`` -- it replays the fixed ``after`` snapshot through the
    same restore primitive Undo uses.
    """

    page_id: str
    updates: Mapping[str, Any]
    before_snapshot: _PageNamespaceSnapshot | None = field(default=None, repr=False)
    after_snapshot: _PageNamespaceSnapshot | None = field(default=None, repr=False)
    label_key: str = "page.book_attributes_changed"
    affected_page_ids: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        # Defensive copy: the command's fixed payload must never alias the
        # caller's dict/nested values.
        self.updates = copy.deepcopy(dict(self.updates))
        self.affected_page_ids = (self.page_id,)

    def apply(self, service: Any) -> bool:
        if self.after_snapshot is None:
            # First application: merge-based, via the atomic
            # PageServiceImpl transition. ``None`` covers both a missing
            # page and a semantic no-op -- either way this command must
            # not enter history. A malformed container raises and
            # propagates to ``PageCommandService``'s existing exception
            # handling, unconverted.
            transition = service._apply_page_book_attributes_transition(self.page_id, self.updates)
            if transition is None:
                return False
            self.before_snapshot = transition.before
            self.after_snapshot = transition.after
            return True
        # Redo: replay the fixed ``after`` snapshot -- never re-merge.
        return bool(
            service._restore_page_book_attributes_namespace(self.page_id, self.after_snapshot)
        )

    def revert(self, service: Any) -> bool:
        if self.before_snapshot is None:
            return False  # never successfully applied
        return bool(
            service._restore_page_book_attributes_namespace(self.page_id, self.before_snapshot)
        )

    def estimated_retained_bytes(self, seen: set[int]) -> int:
        # Always counted once applied -- this payload is never "on the
        # live document" (the document only ever holds the *current*
        # namespace value), so there is no on/off-document gating flag.
        total = estimate_metadata_payload_bytes(self.updates, seen=seen)
        if self.before_snapshot is not None:
            total += estimate_metadata_payload_bytes(
                self.before_snapshot.namespace_value, seen=seen
            )
        if self.after_snapshot is not None:
            total += estimate_metadata_payload_bytes(self.after_snapshot.namespace_value, seen=seen)
        return total


@dataclass(slots=True)
class SetMastersCommand:
    """Document-global master-definition update (CT-07 T3a).

    ``SetBookLayoutCommand``'s exact shape pointed at the
    ``loomstroke.comictools.masters`` namespace: document-global
    (``affected_page_ids=()``), bridged through the masters port
    (``PageServiceImpl._bind_masters_metadata_port()``), namespace-scoped
    exact Undo/Redo with snapshots fixed on the first successful apply.
    One command = one history entry, so a whole masters-list rewrite
    (define / rename / delete a master) is one Undo step.
    """

    updates: Mapping[str, Any]
    before_snapshot: Any | None = field(default=None, repr=False)
    after_snapshot: Any | None = field(default=None, repr=False)
    label_key: str = "book.masters_changed"
    affected_page_ids: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        self.updates = copy.deepcopy(dict(self.updates))

    def apply(self, service: Any) -> bool:
        if self.after_snapshot is None:
            transition = service._apply_masters_transition(self.updates)
            if transition is None:
                return False
            self.before_snapshot = transition.before
            self.after_snapshot = transition.after
            return True
        return bool(service._restore_masters_namespace_snapshot(self.after_snapshot))

    def revert(self, service: Any) -> bool:
        if self.before_snapshot is None:
            return False  # never successfully applied
        return bool(service._restore_masters_namespace_snapshot(self.before_snapshot))

    def estimated_retained_bytes(self, seen: set[int]) -> int:
        total = estimate_metadata_payload_bytes(self.updates, seen=seen)
        for snapshot in (self.before_snapshot, self.after_snapshot):
            if snapshot is not None:
                total += estimate_metadata_payload_bytes(snapshot.namespace_value, seen=seen)
        return total


@dataclass(slots=True)
class MasterInstanceTransition:
    """One page's instance change inside an atomic multi-page command.

    ``before_layer``/``after_layer`` are exact ``LayerSnapshot`` payloads
    (``None`` = no instance on that side); indices are the positions the
    layer sits at in the page's top-level layer list.  Fixed at facade
    time and replayed verbatim — redo never rebuilds anything.
    """

    page_id: str
    before_layer: Any | None
    before_index: int | None
    after_layer: Any | None
    after_index: int | None


@dataclass(slots=True)
class ApplyMasterInstancesCommand:
    """Materialize/replace/remove master instances on many pages atomically
    (CT-07 T3a, spec 07 §9).

    ``MoveSpreadCommand``'s shape for layer payloads: one command holds
    every per-page transition so one Undo restores every page, and a
    mid-apply failure rolls the already-applied pages back before the
    command reports failure (never a half-applied book).
    """

    transitions: tuple[MasterInstanceTransition, ...]
    label_key: str = "master.instances_changed"
    affected_page_ids: tuple[str, ...] = field(default=())

    def __post_init__(self) -> None:
        self.affected_page_ids = tuple(t.page_id for t in self.transitions)

    def apply(self, service: Any) -> bool:
        applied: list[MasterInstanceTransition] = []
        for transition in self.transitions:
            if not service._set_master_instance_layer(
                transition.page_id,
                remove_layer=transition.before_layer,
                insert_layer=transition.after_layer,
                insert_index=transition.after_index,
            ):
                for done in reversed(applied):
                    service._set_master_instance_layer(
                        done.page_id,
                        remove_layer=done.after_layer,
                        insert_layer=done.before_layer,
                        insert_index=done.before_index,
                    )
                return False
            applied.append(transition)
        return bool(applied)

    def revert(self, service: Any) -> bool:
        succeeded = True
        for transition in reversed(self.transitions):
            if not service._set_master_instance_layer(
                transition.page_id,
                remove_layer=transition.after_layer,
                insert_layer=transition.before_layer,
                insert_index=transition.before_index,
            ):
                succeeded = False
        return succeeded

    def estimated_retained_bytes(self, seen: set[int]) -> int:
        total = 0
        for transition in self.transitions:
            for layer in (transition.before_layer, transition.after_layer):
                if layer is not None:
                    total += estimate_layer_snapshot_bytes(layer, seen=seen)
        return total


__all__ = [
    "AddPageCommand",
    "DeletePageCommand",
    "DuplicatePageCommand",
    "MovePageCommand",
    "PageCommand",
    "RenamePageCommand",
    "SetBookLayoutCommand",
    "SetMastersCommand",
    "SetPageBookAttributesCommand",
]
