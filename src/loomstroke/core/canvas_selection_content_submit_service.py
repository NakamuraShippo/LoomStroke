"""Selection content command submit helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any

from loomstroke.core.commands import Command

from .command_layer_access import _resolve_target_layer
from .raster_history_commit import RasterBackingState, record_raster_history


class CanvasSelectionContentSubmitService:
    """Submit selection commands that mutate active layer content."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def submit(self, cmd: Command, label: str) -> bool:
        target = _resolve_target_layer(self._owner, getattr(cmd, "layer_id", None))
        original = RasterBackingState(target) if target is not None else None
        active = self._owner._get_active_layer()
        active_version = getattr(active, "texture_version", None)
        success = bool(cmd.apply(self._owner))
        if not success:
            return False

        self._owner._bump_active_texture_version()

        def restore() -> None:
            if not cmd.revert(self._owner):
                raise RuntimeError("Could not restore rejected selection edit")
            if original is not None:
                original.restore()
            if active_version is not None:
                active.texture_version = active_version

        grouped_transform = (
            label == "Transform Selection"
            and (
                getattr(cmd, "projective_corners", None) is not None
                or getattr(cmd, "mesh_points", None) is not None
                or getattr(cmd, "liquify_raster", None) is not None
                or getattr(cmd, "_backing_expansion", None) is not None
            )
            and not self._owner._in_transaction
        )

        def record() -> None:
            if grouped_transform:
                self._owner.begin_transaction(label)
            try:
                self._owner._record_selection_command_history(cmd, label=label)
            finally:
                if grouped_transform:
                    self._owner.end_transaction()

        if not record_raster_history(
            self._owner,
            record,
            restore,
        ):
            return False
        if not grouped_transform:
            self._owner._recompose()
        return True
