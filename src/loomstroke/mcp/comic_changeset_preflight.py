"""Read-only Comic object-ID admission for the default Simple canvas routing."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from inspect import getattr_static
from typing import Any

from loomstroke.plugins.vector_tools.common import ensure_vector_object_id

_ADD_KINDS = {"add_panel_vector_object", "add_balloon_vector_object", "add_text_object"}


def _text(value: Any) -> bool:
    meta = value.get("meta") if isinstance(value, Mapping) else getattr(value, "meta", None)
    return isinstance(meta, Mapping) and isinstance(meta.get("text_tool"), Mapping)


@dataclass
class _Target:
    name: str
    vector: bool
    kind: str
    ids: set[str]
    has_text: bool = False

    def is_text(self) -> bool:
        return self.vector and (
            self.kind.strip().lower() == "text"
            or self.name.strip().lower().startswith("text layer")
            or self.has_text
        )

    def is_draw(self) -> bool:
        return self.vector and self.kind.strip().lower() == "draw" and not self.is_text()


class _Targets:
    """Simulate only target choice; no canvas state or normalized payload is kept."""

    def __init__(self, canvas: Any, _get_layer: Callable[[Any, str], Any]) -> None:
        tree = canvas.get_layer_tree_snapshot()
        vectors = canvas.get_vector_state_snapshot()
        objects_by_id = {layer.layer_id: layer.vector_objects for layer in vectors.layers}
        self.views: dict[str, _Target] = {}
        for layer in tree.layers:
            objects = objects_by_id.get(layer.id, ())
            self.views[layer.id] = _Target(
                str(layer.name or ""),
                layer.layer_type == "vector",
                str(layer.vector_kind or "draw"),
                {ensure_vector_object_id(obj) for obj in objects},
                any(_text(obj) for obj in objects),
            )
        self.named: dict[str, _Target] = {}
        self.aliases: dict[str, _Target] = {}
        self.roots = [self.views[identifier] for identifier in tree.root_layer_ids]
        self.active = self.views.get(tree.active_layer_id)
        self.can_restore = callable(getattr(canvas, "restore_vector_layer", None))

    def _lookup(self, identifier: str) -> _Target | None:
        if not identifier:
            return None
        return self.named.get(identifier) or self.views.get(identifier)

    def create(self, operation: Mapping[str, Any]) -> None:
        raw = str(operation.get("layerId") or "")
        identifier = raw.strip()
        target = self._lookup(identifier)
        if target is None:
            target = _Target(
                str(operation.get("name") or "Comic Layer"),
                True,
                str(operation.get("vectorKind") or "shape"),
                set(),
            )
            self.roots.append(target)
            self.active = target
            if identifier and self.can_restore:
                self.named[identifier] = target
        # The real Command maps a nonempty raw request (including spaces) to
        # its actual result. An empty request falls back to the active layer.
        if raw:
            self.aliases[raw] = target

    def _draw(self) -> _Target | None:
        if not self.roots:
            return None
        if self.active is not None and self.active.is_draw():
            return self.active
        for target in reversed(self.roots):
            if target is not None and target.is_draw():
                self.active = target
                return target
        target = _Target("Vector Layer", True, "draw", set())
        self.roots.append(target)
        self.active = target
        return target

    def add(self, operation: Mapping[str, Any]) -> bool:
        raw = str(operation.get("layerId") or "")
        target = self.aliases.get(raw) or self._lookup(raw)
        if target is None and self.roots:
            target = self.active
        value = operation.get("object")
        if target is None or not target.vector or (not _text(value) and target.is_text()):
            target = self._draw()
        if target is None:
            # Keep the original missing-target failure path; this is an ID guard.
            return True
        identifier = ensure_vector_object_id(value)
        if identifier in target.ids:
            return False
        target.ids.add(identifier)
        target.has_text = target.has_text or _text(value)
        return True


def _comic_object_ids_available(
    canvas: Any,
    operations: Sequence[Mapping[str, Any]],
    get_layer: Callable[[Any, str], Any],
) -> bool:
    # Import at the call boundary: SimpleCanvasService imports command routers.
    from loomstroke.core.canvas_service_simple import (  # noqa: PLC0415
        _DEFAULT_ADD_VECTOR_OBJECT,
        SimpleCanvasService,
    )

    if not isinstance(canvas, SimpleCanvasService) or (
        getattr_static(canvas, "add_vector_object", None) is not _DEFAULT_ADD_VECTOR_OBJECT
    ):
        # Unknown backends/hooks retain their routing and existing call shape.
        return True
    try:
        targets = _Targets(canvas, get_layer)
        for operation in operations:
            kind = str(operation.get("kind") or operation.get("op") or "")
            if kind == "create_vector_layer":
                targets.create(operation)
            elif kind in _ADD_KINDS and not targets.add(operation):
                return False
    except Exception:
        # No tracking reset, model mutation or inference about unknown values.
        return False
    return True
