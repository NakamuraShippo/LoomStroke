"""Strict, scoped preparation before master facade snapshots or command replay."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from inspect import getattr_static
from typing import Any

from .canvas_history_clear_continuation import _HistoryClearContinuation
from .canvas_service_simple import SimpleCanvasService
from .canvas_service_simple_image_api import _DEFAULT_UPDATE_CANVAS_CONFIG
from .canvas_service_simple_layer_api import _DEFAULT_REPLACE_LAYERS
from .document_capture_guard import is_document_capture_blocked
from .page_mutation_preflight import PageMutationPreflight

logger = logging.getLogger(__name__)


def _supports_standard_master(service: Any) -> bool:
    # Local imports keep PageService class setup outside this helper's import graph.
    from .page_service import (  # noqa: PLC0415
        _DEFAULT_MASTER_INSTANCE_SET,
        _DEFAULT_PAGE_PERSIST,
        _DEFAULT_PAGE_PUBLIC_PERSIST,
        PageServiceImpl,
    )

    canvas = getattr(service, "_canvas", None)
    return (
        isinstance(service, PageServiceImpl)
        and isinstance(canvas, SimpleCanvasService)
        and getattr_static(service, "_set_master_instance_layer", None)
        is _DEFAULT_MASTER_INSTANCE_SET
        and getattr_static(service, "_persist_active_page", None) is _DEFAULT_PAGE_PERSIST
        and getattr_static(service, "persist_active_page", None) is _DEFAULT_PAGE_PUBLIC_PERSIST
        and getattr_static(canvas, "update_canvas_config", None) is _DEFAULT_UPDATE_CANVAS_CONFIG
        and getattr_static(canvas, "replace_layers", None) is _DEFAULT_REPLACE_LAYERS
    )


class _MasterPreparation:
    """Keep preparation on its original document, page order, active and history owner."""

    def __init__(self, service: Any, page_ids: Sequence[str], completion: Any = None) -> None:
        self.service, self.completion = service, completion
        self.canvas, self.page_set = service._canvas, service._pages
        self.document = getattr(self.canvas, "_doc", None)
        self.order, self.active = tuple(service._pages), service._active_page_id
        self.scope = PageMutationPreflight(service, page_ids)
        self._history = _HistoryClearContinuation(self.canvas)
        self.page_id = page_ids[0] if page_ids else ""

    def document_is_current(self) -> bool:
        return (
            self.service._canvas is self.canvas
            and getattr(self.canvas, "_doc", None) is self.document
            and self.service._pages is self.page_set
        )

    def current(self) -> bool:
        return (
            self.scope.is_current()
            and self.service._active_page_id == self.active
            and len(self.service._pages) == len(self.order)
            and all(a is b for a, b in zip(self.order, self.service._pages, strict=True))
            and self._history.is_current()
            and (self.completion is None or self.completion.allows_commit())
            and _supports_standard_master(self.service)
        )

    def report(self, error: str) -> None:
        if self.document_is_current():
            self.service._emit_page_op_failed("master", self.page_id, error)

    def _capture_ready(self) -> bool:
        # Preparation has not acquired its own capture token. A preview born
        # in hydration/close/finalize must not enter the canonical snapshot.
        return self.current() and not is_document_capture_blocked(self.canvas)

    def run(self) -> bool:
        """Allow legitimate pending-stroke closure, then capture it strictly once."""
        try:
            if not self._capture_ready():
                raise RuntimeError("master preparation changed or an edit is still pending")
            if not self.scope.hydrate() or not self._capture_ready():
                raise RuntimeError("master page content could not be prepared")
            if any(page_id == self.active for page_id, _state in self.scope.targets):
                closed = self.canvas._history_close_pending_group()
                if not self._capture_ready():
                    raise RuntimeError("master page changed while closing the active edit")
                if closed is False:
                    raise RuntimeError("pending stroke could not be closed")
                self.service._persist_active_page(
                    raise_on_error=True, scope_guard=self._capture_ready
                )
                if not self._capture_ready():
                    raise RuntimeError("master page changed during active capture")
                if self.canvas._in_transaction or self.canvas._active_history_group is not None:
                    raise RuntimeError("active history is still pending")
            if not self.scope.is_ready() or not self._capture_ready():
                raise RuntimeError("master preparation no longer belongs to this document")
        except Exception as exc:
            logger.exception("Master instance preparation failed")
            self.report(str(exc))
            return False
        return True


def _master_facade_preparation(service: Any, page_ids: Sequence[str]) -> _MasterPreparation | None:
    """Return None for original Legacy/Fake/custom routes, without sending new kwargs.

    A standard facade runs this before building before_layer transitions. The
    later command replay revalidates and captures again: two bounded captures
    are intentional until a separate measured need justifies another cache.
    """
    return _MasterPreparation(service, page_ids) if _supports_standard_master(service) else None
