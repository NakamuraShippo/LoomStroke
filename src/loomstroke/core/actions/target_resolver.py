"""Resolves a ``TargetSelector`` against the live page; ambiguity blocks (CT-20 §14)."""

from __future__ import annotations

from collections.abc import Callable

from .action_context import ActionContext
from .resolved_target import ResolvedTarget
from .target_selector import TargetSelector

__all__ = ["TargetResolver"]

Handler = Callable[[TargetSelector, ActionContext], ResolvedTarget]


class TargetResolver:
    def __init__(self) -> None:
        self._handlers: dict[str, Handler] = {
            "none": lambda _s, _c: ResolvedTarget("none"),
            "document": lambda _s, _c: ResolvedTarget("document", ("document",)),
            "active_page": lambda _s, context: self._page(context.page_id),
            "page_by_id": lambda selector, _c: self._page(selector.value or ""),
            "active_layer": self._active_layer,
            "all_layers": self._all_layers,
            "layer_by_id": self._layer_by_id,
            "layer_by_name": self._layer_by_name,
        }

    def resolve(self, selector: TargetSelector, context: ActionContext) -> ResolvedTarget:
        handler = self._handlers.get(selector.kind)
        if handler is None:
            return ResolvedTarget.blocked(selector.target_kind, "selector_unknown")
        return handler(selector, context)

    # -- handlers ----------------------------------------------------------------------

    @staticmethod
    def _page(page_id: str) -> ResolvedTarget:
        if not page_id:
            return ResolvedTarget.blocked("page", "target_not_found")
        return ResolvedTarget("page", (page_id,))

    @staticmethod
    def _active_layer(_selector: TargetSelector, context: ActionContext) -> ResolvedTarget:
        active = context.active_layer_id()
        if not active:
            return ResolvedTarget.blocked("layer", "target_not_found")
        return ResolvedTarget("layer", (active,))

    @staticmethod
    def _all_layers(_selector: TargetSelector, context: ActionContext) -> ResolvedTarget:
        ids = context.layer_ids()
        if not ids:
            return ResolvedTarget.blocked("layer", "target_not_found")
        return ResolvedTarget("layer", ids)

    @staticmethod
    def _layer_by_id(selector: TargetSelector, context: ActionContext) -> ResolvedTarget:
        wanted = selector.value or ""
        if wanted in context.layer_ids():
            return ResolvedTarget("layer", (wanted,))
        return ResolvedTarget.blocked("layer", "target_not_found")

    @staticmethod
    def _layer_by_name(selector: TargetSelector, context: ActionContext) -> ResolvedTarget:
        name = selector.value or ""
        matches = [
            str(layer.id)
            for layer in context.layers()
            if str(getattr(layer, "name", "") or "") == name
        ]
        if not matches:
            return ResolvedTarget.blocked("layer", "target_not_found")
        if len(matches) > 1:
            return ResolvedTarget.blocked("layer", "selector_ambiguous", tuple(matches))
        return ResolvedTarget("layer", (matches[0],))
