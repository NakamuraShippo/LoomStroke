"""Recover standard vector membership if geometry preparation cannot finish."""

from __future__ import annotations

import logging
from inspect import getattr_static

from .layer_command_commit_context import _LayerCommandContext

logger = logging.getLogger(__name__)


def _same_items(left, right):
    return len(left) == len(right) and all(a is b for a, b in zip(left, right, strict=True))


class VectorApplyRecovery:
    @classmethod
    def capture(cls, canvas):
        from .canvas_service_simple import _DEFAULT_ADD_VECTOR_OBJECT  # noqa: PLC0415

        if getattr_static(canvas, "add_vector_object", None) is not _DEFAULT_ADD_VECTOR_OBJECT:
            return None
        return cls(canvas)

    def __init__(self, canvas):
        self.canvas = canvas
        self.scope = _LayerCommandContext(canvas)
        self.roots = canvas._layers
        self.root_items = tuple(self.roots)
        self.active = canvas._active_layer_id
        # Only object references are held. Raster images and vector metadata
        # are not duplicated for every successful drawing operation.
        self.rows = [
            (layer, layer.vector_objects, tuple(layer.vector_objects), layer.texture_version)
            for layer in canvas._flatten_layers()
        ]

    def recover(self, guard) -> None:
        if not self.scope.current() or self.canvas._layers is not self.roots:
            return
        changed = not _same_items(self.roots, self.root_items) or any(
            layer.vector_objects is not original or not _same_items(original, items)
            for layer, original, items, _version in self.rows
        )
        if not changed:
            return
        try:
            guard.rollback(self)
        except Exception as exc:
            logger.warning("Vector preparation recovery failed (%s)", type(exc).__name__)

    def revert(self, canvas) -> bool:
        for layer, original, items, version in self.rows:
            original[:] = items
            layer.vector_objects = original
            layer.texture_version = version
        canvas._active_layer_id = self.active
        return True
