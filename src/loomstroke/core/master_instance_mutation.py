"""One prepared master subtree swap that preserves unrelated live layer owners.

The outer master command owns atomic rollback and closes pending edits first.
This module prepares candidates without mutating the page, then installs only
the changed subtree. It never clears or detaches content history.
"""

from __future__ import annotations

from typing import Any

from .canvas_history_clear_continuation import _HistoryClearContinuation
from .canvas_invalidation import GpuResourceInvalidation
from .canvas_service_simple import SimpleCanvasService
from .layer_replacement_versions import advance_replacement_versions
from .layer_tree_service import LayerRemovalService, LayerTreeQueryService, sync_active_markers
from .page_mutation_preflight import PageMutationPreflight


def _subtree(layer: Any):
    if layer is not None:
        yield layer
        for child in layer.children:
            yield from _subtree(child)


def _candidate(layers: Any, remove_layer: Any, insert_layer: Any, index: int | None):
    candidate = list(layers)
    removed = None
    if remove_layer is not None:
        remove_id = getattr(remove_layer, "id", None)
        position = next((i for i, layer in enumerate(candidate) if layer.id == remove_id), None)
        if position is None:
            return None
        removed = candidate.pop(position)
    if insert_layer is not None:
        position = len(candidate) if index is None else max(0, min(index, len(candidate)))
        candidate.insert(position, insert_layer)
    return candidate, removed


class _PreparedMasterInstanceMutation:
    """Candidates and guards for exactly one page; no page-switch responsibilities."""

    def __init__(
        self,
        service: Any,
        scope: PageMutationPreflight,
        active_id: str | None,
        *,
        remove_layer: Any,
        insert_layer: Any,
        insert_index: int | None,
        captured_layers: list[Any] | None = None,
    ) -> None:
        self.service, self.scope, self.active_id = service, scope, active_id
        self.page_id, self.state = scope.targets[0]
        self.page, self.canvas = self.state.page, service._canvas
        self.original = self.page.layers
        self.live = self.canvas._layers if self.page_id == active_id else None
        self.canonical: list[Any] | None = None
        self.incoming: list[Any] | None = None
        self.removed = None
        if self.live is not None:
            self._history = _HistoryClearContinuation(self.canvas)
            self._stacks = tuple(
                (stack, tuple(stack))
                for stack in (self.canvas._undo_stack, self.canvas._redo_stack)
            )
            # Capture committed live pixels without calling a finalizer or
            # replacing the canonical list until all candidates are ready.
            source = service._capture_layers() if captured_layers is None else captured_layers
        else:
            source = self.original
        replacement = _candidate(source, remove_layer, insert_layer, insert_index)
        if replacement is None:
            return
        self.canonical = replacement[0]
        if self.live is not None:
            runtime = (
                service._snapshot_to_layer(insert_layer, service._resolve_canvas_spec(self.page))
                if insert_layer is not None
                else None
            )
            replacement = _candidate(self.live, remove_layer, runtime, insert_index)
            if replacement is None:
                self.canonical = None
                return
            self.incoming, self.removed = replacement
            before = [self.removed] if self.removed is not None else []
            after = [runtime] if runtime is not None else []
            # Only the new subtree needs new texture generations. Existing
            # layer objects (and Commands referring to them) stay untouched.
            advance_replacement_versions(before, after)
            self.changed_ids = frozenset(
                str(node.id) for root in (*before, *after) for node in _subtree(root)
            )
            self._resources = self.canvas._gpu_resource_invalidation.merge(
                GpuResourceInvalidation(layer_ids=self.changed_ids, reason="master_instance")
            )

    def _same_scope(self) -> bool:
        return (
            self.scope.is_ready()
            and self.service._active_page_id == self.active_id
            and self.state.page is self.page
        )

    def _same_history(self) -> bool:
        return self._history.is_current() and all(
            len(stack) == len(groups) and all(a is b for a, b in zip(stack, groups, strict=True))
            for stack, groups in self._stacks
        )

    def install(self) -> bool:
        if (
            self.canonical is None
            or not self._same_scope()
            or self.page.layers is not self.original
        ):
            return False
        if self.live is not None and (
            self.canvas._layers is not self.live or not self._same_history()
        ):
            return False
        self.page.layers = self.canonical
        self.state.dirty = True
        if self.live is not None:
            self.canvas._layers = self.incoming
            self.canvas._max_layers = max(self.canvas._max_layers, len(self.incoming))
            query = LayerTreeQueryService(self.canvas)
            sync_active_markers(self.canvas, query)
            self._clear_removed_references(query)
            for identifier in self.changed_ids:
                self.canvas._vector_raster_cache.pop(identifier, None)
            for node in _subtree(self.removed):
                for vector in node.vector_objects:
                    self.canvas._qpath_cache.pop(id(vector), None)
            self.canvas._gpu_resource_invalidation = self._resources
            self.canvas._invalidate_layer_cache()
            self.canvas._reset_filter_cache_bytes()
            self.canvas._recompose()
            if not self._same_scope() or not self._same_history():
                return False
        # The existing semantic helper participates in deferred completion
        # when the outer checked book command opts into that boundary.
        self.service._emit_page_dirty_changed(self.page_id, True)
        return self._same_scope()

    def _clear_removed_references(self, query: LayerTreeQueryService) -> None:
        cleanup = LayerRemovalService(self.canvas, query)
        removed_ids = {str(node.id) for node in _subtree(self.removed)}
        for node in _subtree(self.removed):
            # The existing delete policy clears vector previews/selections
            # whose runtime object was removed, including nested children.
            cleanup._cleanup_removed_layer(node)
        channels = getattr(self.canvas, "_channels_service", None)
        if channels is None:
            return
        vanished = {
            identifier
            for identifier in removed_ids
            if query.find_layer_by_id_tree(identifier) is None
        }
        if channels._layer_id in vanished:
            channels._layer_id = None
            channels._selected = (0, 1, 2)
        if channels._color_layer_id in vanished:
            channels._color_layer_id = None
        if channels._view is not None and channels._view[0] in vanished:
            channels._view = None


def _prepare_master_instance_mutation(
    service: Any,
    page_id: str,
    *,
    remove_layer: Any | None,
    insert_layer: Any | None,
    insert_index: int | None,
    captured_layers: list[Any] | None = None,
) -> _PreparedMasterInstanceMutation | None:
    """Prepare a swap, optionally reusing the caller's committed active capture.

    The outer transaction prepares every swap BEFORE taking its capture lease.
    It may pass the active canonical layers obtained by strict persist, avoiding
    another capture. ``install`` performs no snapshot capture. This is
    an internal prepared callable, not a new keyword on custom page setters.
    """
    scope = PageMutationPreflight(service, (page_id,))
    active_id = service._active_page_id
    if not scope.hydrate() or service._active_page_id != active_id:
        return None
    if page_id == active_id and (
        not isinstance(service._canvas, SimpleCanvasService)
        or service._canvas._in_transaction
        or service._canvas._active_history_group is not None
    ):
        return None
    return _PreparedMasterInstanceMutation(
        service,
        scope,
        active_id,
        remove_layer=remove_layer,
        insert_layer=insert_layer,
        insert_index=insert_index,
        captured_layers=captured_layers,
    )


def _mutate_master_instance_layer(
    service: Any,
    page_id: str,
    *,
    remove_layer: Any | None,
    insert_layer: Any | None,
    insert_index: int | None,
) -> bool:
    """Standalone standard primitive; the caller already closed pending edits.

    Group-wide atomic execution instead prepares callables before its lease and
    invokes ``install`` directly. Legacy/custom dispatch remains in PageService.
    """
    prepared = _prepare_master_instance_mutation(
        service,
        page_id,
        remove_layer=remove_layer,
        insert_layer=insert_layer,
        insert_index=insert_index,
    )
    return prepared is not None and prepared.install()
