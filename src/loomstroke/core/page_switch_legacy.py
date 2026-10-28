"""Call-local recovery for the standard numpy-backed page installer."""

from __future__ import annotations

import logging
import time
from inspect import getattr_static
from typing import Any

from .canvas import _DEFAULT_LEGACY_HISTORY_CLEAR, Canvas
from .events import Events, HistoryResetEvent
from .page_mutation_preflight import PageMutationPreflight
from .page_switch_transaction import _PageSwitchOutcome

logger = logging.getLogger(__name__)


def _supports_legacy_page_switch(service: Any, scope: PageMutationPreflight | None) -> bool:
    """Preserve unknown public clear and private persist/apply hook contracts."""
    from .page_service import _DEFAULT_PAGE_APPLY, _DEFAULT_PAGE_PERSIST  # noqa: PLC0415

    return (
        scope is not None
        and isinstance(service._canvas, Canvas)
        and not service._page_history.supports_page_history()
        and getattr_static(service, "_persist_active_page", None) is _DEFAULT_PAGE_PERSIST
        and getattr_static(service, "_apply_page_to_canvas", None) is _DEFAULT_PAGE_APPLY
        and getattr_static(service._canvas, "history_clear", None) is _DEFAULT_LEGACY_HISTORY_CLEAR
    )


class _LegacyPageReceipt:
    """Keep only references/scalars the installer or history clear can change."""

    def __init__(self, service: Any, source_id: str) -> None:
        self.service, self.canvas, self.source_id = service, service._canvas, source_id
        self.fields = {
            name: self.canvas.__dict__[name]
            for name in ("layers", "_active_index", "_composite_cache")
        }
        self.config = self.canvas.config
        self.geometry = self.config.width, self.config.height, self.config.background_color
        self.history = self.canvas._history
        self.undo, self.redo = self.history._undo_stack, self.history._redo_stack
        # Legacy clear empties these two existing lists in place. Keep their
        # entries by reference; this never copies an image or patch payload.
        self.undo_items, self.redo_items = tuple(self.undo), tuple(self.redo)

    def owns_canvas(self) -> bool:
        return self.canvas.config is self.config and self.canvas._history is self.history

    def restore(self) -> None:
        """Use no conversion, installer, cache callback, or history inverse."""
        self.canvas.__dict__.update(self.fields)
        self.config.width, self.config.height, self.config.background_color = self.geometry
        self.history._undo_stack, self.history._redo_stack = self.undo, self.redo
        self.undo[:] = self.undo_items
        self.redo[:] = self.redo_items
        self.service.__dict__["_active_page_id"] = self.source_id


def _switch_legacy_page(  # noqa: PLR0911
    service: Any,
    scope: PageMutationPreflight,
    source_id: str,
    target: Any,
    *,
    failure_context: tuple[str, str] | None = None,
) -> _PageSwitchOutcome:
    """Run only after CHANGING, for the caller's qualified standard installer.

    The caller must qualify the existing default persist/apply methods before
    passing their optional private arguments. Custom hooks keep the old route.
    History-reset observers run after model settlement and cannot cause an old
    rollback to erase a subsequently accepted document or page change.
    """
    canvas, expected = service._canvas, source_id
    receipt: _LegacyPageReceipt | None = None

    def current() -> bool:
        return (
            scope.is_current()
            and service._active_page_id == expected
            and (receipt is None or receipt.owns_canvas())
        )

    try:
        service._persist_active_page(raise_on_error=True, scope_guard=current)
        if not current():
            return _PageSwitchOutcome.SUPERSEDED
        prepared = service._prepare_page_layers(
            target.page, service._resolve_canvas_spec(target.page)
        )
        if not current():
            return _PageSwitchOutcome.SUPERSEDED
        receipt = _LegacyPageReceipt(service, source_id)
        expected = target.page.id
        service._active_page_id = expected
        service._apply_page_to_canvas(target.page, prepared)
        if not current():
            return _PageSwitchOutcome.SUPERSEDED
        # The old void return is intentional: None is normal success, and no
        # new bool contract is imposed on the public legacy history method.
        canvas.history_clear()
        if not current():
            return _PageSwitchOutcome.SUPERSEDED
    except Exception as exc:
        if not current():
            return _PageSwitchOutcome.SUPERSEDED
        if receipt is not None:
            receipt.restore()
        logger.exception("Failed to switch legacy page %s", target.page.id)
        service._emit_page_op_failed(*(failure_context or ("set_active", target.page.id)), str(exc))
        return _PageSwitchOutcome.REFUSED

    # The model and clear have succeeded. Notification failures or new edits
    # are outside the receipt boundary, just like the standard Simple path.
    try:
        service._emit_event(
            Events.DOC_HISTORY_RESET,
            HistoryResetEvent(
                reason="page_switch", source=service.__class__.__name__, timestamp=time.time()
            ),
        )
    except Exception:
        logger.exception("Legacy page history reset notification failed after settlement")
    return _PageSwitchOutcome.COMMITTED if current() else _PageSwitchOutcome.SUPERSEDED
