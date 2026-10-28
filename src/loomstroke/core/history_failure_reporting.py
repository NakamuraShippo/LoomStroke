"""Report an unconfirmed history attempt only to its original live scope."""

from __future__ import annotations

import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .events import DocumentChangeEvent, Events

RevisionSource = Callable[[], tuple[int, int]]


@dataclass(frozen=True)
class _ScopeIdentity:
    document: int
    pages: int | None
    page_id: str | None
    page_state: int | None
    epoch: int | None


@dataclass(frozen=True)
class _ScopedHistoryFailureChange(DocumentChangeEvent):
    # Subscribers may retain this event. It owns only scalar identities and
    # a weak reporter reference, never Command/page/image payloads or closures.
    _reporter: weakref.ReferenceType[Any] | None = field(default=None, repr=False)
    _identity: _ScopeIdentity | None = field(default=None, repr=False)


def history_failure_event_is_current(payload: Any) -> bool:
    """Ordinary change events keep their existing unconditional contract."""
    if not isinstance(payload, _ScopedHistoryFailureChange):
        return True
    reporter = payload._reporter() if payload._reporter is not None else None
    return (
        reporter is not None
        and payload._identity is not None
        and reporter.matches(payload._identity)
    )


@dataclass
class _FailureScope:
    reporter: HistoryFailureReporter
    identity: _ScopeIdentity
    owner: Any
    # Retained only until this synchronous attempt/report ends. These local
    # references prevent immediate id reuse during callback-driven replacement.
    document: Any
    pages: Any
    page_state: Any

    def report(self, outcome: Any) -> bool:
        return self.reporter.report(self, outcome)


class HistoryFailureReporter:
    """One canvas's existing page/revision ports; no persistent failed payload."""

    def __init__(
        self,
        canvas: Any,
        event_bus: Any,
        pages: Any = None,
        revision_source: RevisionSource | None = None,
    ) -> None:
        self.canvas, self.event_bus, self.pages = canvas, event_bus, pages
        self.revision_source = revision_source

    def _epoch(self) -> int | None:
        return self.revision_source()[0] if self.revision_source is not None else None

    def capture(self, owner: Any) -> _FailureScope:
        document = getattr(self.canvas, "_doc", None)
        page_list = self.pages._pages if self.pages is not None else None
        page_id = self.pages.get_active_page_id() if self.pages is not None else None
        state = self.pages._find_page_state(page_id) if self.pages is not None else None
        identity = _ScopeIdentity(
            id(document),
            id(page_list) if page_list is not None else None,
            page_id,
            id(state) if state is not None else None,
            self._epoch(),
        )
        return _FailureScope(self, identity, owner, document, page_list, state)

    def matches(self, identity: _ScopeIdentity) -> bool:
        if id(getattr(self.canvas, "_doc", None)) != identity.document:
            return False
        if identity.epoch is not None and self._epoch() != identity.epoch:
            return False
        if self.pages is None:
            return identity.pages is None
        if id(self.pages._pages) != identity.pages:
            return False
        if identity.page_state is None:
            return True
        state = self.pages._find_page_state(identity.page_id)
        return state is not None and id(state) == identity.page_state

    def report(self, scope: _FailureScope, outcome: Any) -> bool:
        if outcome.reason is None or not scope.owner.blocked or not self.matches(scope.identity):
            return self.matches(scope.identity)
        state = scope.page_state
        changed_dirty = state is not None and not state.dirty
        if state is not None:
            state.dirty = True
        event = _ScopedHistoryFailureChange(
            change_type=(
                "pages.history_restore_failed"
                if self.pages is not None
                else "history.restore_failed"
            ),
            source="history",
            _reporter=weakref.ref(self),
            _identity=scope.identity,
        )
        # Invalidate the document baseline before publishing the page UI mark.
        # Consumers recheck scope if an earlier subscriber replaces the document.
        emit = getattr(self.event_bus, "emit", None) or getattr(self.event_bus, "publish", None)
        if callable(emit):
            emit(Events.DOC_CHANGED, event)
        if changed_dirty and self.matches(scope.identity):
            self.pages._emit_page_dirty_changed(scope.identity.page_id, True)
        return self.matches(scope.identity)


def install_history_failure_reporter(
    canvas: Any,
    event_bus: Any,
    pages: Any = None,
    revision_source: RevisionSource | None = None,
) -> HistoryFailureReporter | None:
    install = getattr(canvas, "_history_set_failure_scope_factory", None)
    if not callable(install):
        return None
    reporter = HistoryFailureReporter(canvas, event_bus, pages, revision_source)
    install(reporter.capture)
    return reporter


def bind_document_history_failure_source(
    canvas: Any, event_bus: Any, pages: Any, revision_source: RevisionSource
) -> None:
    if pages is None:
        install_history_failure_reporter(canvas, event_bus, revision_source=revision_source)
    else:
        bind = getattr(pages, "_bind_history_failure_revision_source", None)
        if callable(bind):
            bind(revision_source)
