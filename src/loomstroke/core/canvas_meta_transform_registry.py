"""Plugin-owned metadata transforms for whole-canvas flip/rotation.

Callbacks return a new metadata mapping, or None for unrecognised metadata.
They run before any live canvas mutation, including for empty/group layers.
The registry deliberately knows no plugin namespaces or stored schemas.
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from typing import Any, ClassVar

MetaTransform = Callable[[Mapping[str, Any], str, int, int], "dict[str, Any] | None"]


class CanvasMetaTransformRegistry:
    """Process-wide hooks, registered for the lifetime of a loaded plugin."""

    _shared: ClassVar[CanvasMetaTransformRegistry | None] = None

    def __init__(self) -> None:
        self._transforms: list[MetaTransform] = []
        self._owners: dict[MetaTransform, set[object]] = {}

    @classmethod
    def shared(cls) -> CanvasMetaTransformRegistry:
        if cls._shared is None:
            cls._shared = cls()
        return cls._shared

    def register(self, transform: MetaTransform, *, owner: object | None = None) -> None:
        self._owners.setdefault(transform, set()).add(transform if owner is None else owner)
        if transform not in self._transforms:
            self._transforms.append(transform)

    def unregister(self, transform: MetaTransform, *, owner: object | None = None) -> None:
        owners = self._owners.get(transform)
        if owners is None:
            return
        owners.discard(transform if owner is None else owner)
        if not owners:
            del self._owners[transform]
            self._transforms.remove(transform)

    def transform(
        self, metadata: Any, mode: str, old_width: int, old_height: int
    ) -> dict[str, Any] | None:
        if not self._transforms or not isinstance(metadata, Mapping) or not metadata:
            return None
        # Protect live data even from a callback that mutates then raises.
        current = copy.deepcopy(dict(metadata))
        changed = False
        for transform in tuple(self._transforms):
            result = transform(current, mode, old_width, old_height)
            if result is not None:
                current = result
                changed = True
        return current if changed else None
