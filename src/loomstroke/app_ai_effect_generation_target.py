"""Observe one normal app generation's original target and mutable draft."""

from __future__ import annotations

import logging
from dataclasses import fields
from typing import Any
from weakref import ref

from .core.canvas_history_clear_continuation import _stored_state
from .plugins.ai_effect.data_model import AIEffectState, SlotRef

logger = logging.getLogger(__name__)

_RUNTIME_FIELDS = frozenset({"state", "generation", "last_result", "last_error"})
_LAYER_SCALARS = ("locked", "alpha_lock", "texture_version", "transform_version")


class _GenerationTargetRejected(RuntimeError):
    """The original tracked target cannot safely receive this result."""


def _freeze(value: Any, visiting: set[int]) -> tuple[Any, ...]:
    """Keep exact builtin values without deepcopy, rounding, or custom equality."""
    kind = type(value)
    if value is None:
        return ("none",)
    if kind is bool or kind is int or kind is str:
        return (kind.__name__, value)
    if kind is float:
        return ("float", value.hex())
    if kind is SlotRef:
        return ("slot", _freeze(value.layer_id, visiting), _freeze(value.exclude_blend, visiting))
    if (kind is not dict and kind is not list and kind is not tuple) or id(value) in visiting:
        raise _GenerationTargetRejected("Unsupported or cyclic generation intent")
    visiting.add(id(value))
    try:
        if kind is dict:
            if any(type(key) is not str for key in value):
                raise _GenerationTargetRejected("Generation intent keys must be strings")
            return ("dict", tuple((key, _freeze(value[key], visiting)) for key in sorted(value)))
        return (kind.__name__, tuple(_freeze(item, visiting) for item in value))
    finally:
        visiting.remove(id(value))


def _intent(state: AIEffectState) -> tuple[Any, ...]:
    # New dataclass fields are conservatively included until explicitly runtime.
    return tuple(
        (field.name, _freeze(getattr(state, field.name), set()))
        for field in fields(AIEffectState)
        if field.name not in _RUNTIME_FIELDS
    )


class _NormalGenerationTarget:
    """Hold only one non-weakref page state, never the page set or history."""

    def __init__(
        self,
        *,
        references: dict[str, Any],
        page_state: Any,
        layer: Any,
        layer_id: str,
        page_id: str,
        generation: int,
        state: AIEffectState,
    ) -> None:
        self._references = references
        self._page_state = page_state
        self.layer_id, self._page_id = layer_id, page_id
        self._generation = generation
        self._layer = ref(layer)
        self._intent = _intent(state)
        self._scalars = tuple(_freeze(getattr(layer, name), set()) for name in _LAYER_SCALARS)

    def _owners_current(self, app: Any) -> bool:
        current = {name: weak() for name, weak in self._references.items()}
        if any(value is None for value in current.values()):
            return False
        canvas, pages, history = current["canvas"], current["pages"], current["history"]
        app_state, canvas_state = _stored_state(app), _stored_state(canvas)
        page_state, history_state = _stored_state(pages), _stored_state(history)
        page_set = page_state.get("_pages")
        return (
            app_state.get("_canvas_service") is canvas
            and app_state.get("_page_service") is pages
            and page_state.get("_canvas") is canvas
            and canvas_state.get("_doc") is current["document"]
            and canvas_state.get("_history_service") is history
            and history_state.get("_restore_owner") is current["owner"]
            and history_state.get("_restore_generation") == self._generation
            and page_state.get("_active_page_id") == self._page_id
            and type(page_set) is list
            and any(item is self._page_state for item in page_set)
        )

    def is_current(self, app: Any) -> bool:
        if not self._owners_current(app):
            return False
        canvas, state = self._references["canvas"](), self._references["state"]()
        layer = self._layer()
        try:
            return (
                layer is not None
                and canvas._find_layer_by_id_tree(self.layer_id) is layer
                and layer.layer_type == "ai_effect"
                and layer.ai_effect is state
                and _intent(state) == self._intent
                and tuple(_freeze(getattr(layer, name), set()) for name in _LAYER_SCALARS)
                == self._scalars
                and self._owners_current(app)
            )
        except Exception:
            # Unknown lookup failures refuse this attempt; no canvas mutation
            # or controller state transition belongs to this observer.
            return False


def _capture_normal_generation_target(  # noqa: PLR0911 - explicit legacy capability boundaries
    app: Any, layer_id: str, state: Any
) -> _NormalGenerationTarget | None:
    """Preserve the old call shape for standalone or unknown host capabilities."""
    if type(state) is not AIEffectState:
        return None
    app_state = _stored_state(app)
    canvas, pages = app_state.get("_canvas_service"), app_state.get("_page_service")
    canvas_state, page_state = _stored_state(canvas), _stored_state(pages)
    document, history = canvas_state.get("_doc"), canvas_state.get("_history_service")
    page_set, page_id = page_state.get("_pages"), page_state.get("_active_page_id")
    finder = getattr(canvas, "_find_layer_by_id_tree", None)
    if (
        document is None
        or history is None
        or page_state.get("_canvas") is not canvas
        or type(page_set) is not list
        or type(page_id) is not str
        or not callable(finder)
    ):
        return None
    from .core.page_service import _PageState  # noqa: PLC0415

    target_state = next(
        (item for item in page_set if type(item) is _PageState and item.page.id == page_id), None
    )
    history_state = _stored_state(history)
    owner = history_state.get("_restore_owner")
    generation = history_state.get("_restore_generation")
    if target_state is None or owner is None or type(generation) is not int:
        return None
    try:
        references = {
            name: ref(value)
            for name, value in (
                ("canvas", canvas),
                ("document", document),
                ("pages", pages),
                ("history", history),
                ("owner", owner),
                ("state", state),
            )
        }
    except TypeError:
        return None
    try:
        layer = finder(layer_id)
    except Exception as exc:
        raise _GenerationTargetRejected("AI Effect target lookup unavailable") from exc
    if layer is None:
        raise _GenerationTargetRejected("AI Effect target no longer matches")
    layer_state = _stored_state(layer)
    if not {"layer_type", "ai_effect", *_LAYER_SCALARS}.issubset(layer_state):
        return None
    if layer_state["layer_type"] != "ai_effect" or layer_state["ai_effect"] is not state:
        raise _GenerationTargetRejected("AI Effect target no longer matches")
    try:
        ref(layer)
    except TypeError:
        return None
    return _NormalGenerationTarget(
        references=references,
        page_state=target_state,
        layer=layer,
        layer_id=layer_id,
        page_id=page_id,
        generation=generation,
        state=state,
    )


def _normal_generation_target_is_current(target: _NormalGenerationTarget | None, app: Any) -> bool:
    if target is None or target.is_current(app):
        return True
    logger.warning("AI Effect generation target changed; output skipped for %s", target.layer_id)
    return False
