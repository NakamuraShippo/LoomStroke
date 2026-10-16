"""Vector command submit helpers for ``SimpleCanvasService``."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any, Protocol

from loomstroke.core.events import DocumentChangeEvent

from .canvas_command_commit_guard import CanvasCommandCommitGuard

logger = logging.getLogger(__name__)


class _VectorCommand(Protocol):
    def apply(self, canvas: Any) -> bool: ...


class CanvasVectorCommandSubmitService:
    """Submit vector commands and preserve canvas post-processing order."""

    def __init__(self, owner: Any, *, doc_changed_event: str) -> None:
        self._owner = owner
        self._doc_changed_event = doc_changed_event

    @staticmethod
    def is_vector_command(cmd: Any) -> bool:
        return hasattr(cmd, "vector_object") or hasattr(cmd, "new_object")

    def submit(self, cmd: _VectorCommand) -> bool:
        guard = CanvasCommandCommitGuard(self._owner)
        try:
            target_layer_id = getattr(cmd, "layer_id", None) or getattr(cmd, "_layer_id", None)
            if target_layer_id is None:
                self._owner.ensure_vector_layer()
            applied = cmd.apply(self._owner)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Failed to apply vector command: %s", exc)
            return False
        if not applied:
            logger.debug("Vector command apply returned False: %s", type(cmd).__name__)
            return False

        try:
            return self._commit(cmd)
        except Exception:
            guard.rollback(cmd)
            raise

    def _commit(self, cmd: _VectorCommand) -> bool:

        label = "Vector"
        if hasattr(cmd, "vector_object"):
            label = "Vector Create"
        elif hasattr(cmd, "new_object"):
            label = "Vector Edit"

        target_lid = (
            getattr(cmd, "layer_id", None)
            or getattr(cmd, "_layer_id", None)
            or getattr(self._owner, "_vector_preview_layer_id", None)
        )
        if target_lid:
            vlayer = self._owner._get_layer_by_id(target_lid)
            if vlayer is not None:
                vlayer.texture_version += 1
        else:
            for layer in self._owner._layers:
                if layer.layer_type == "vector":
                    layer.texture_version += 1

        self._owner._record_history_command(label, cmd)
        logger.info(
            "Vector command applied; vectors=%s preview=%s layer=%s",
            sum(
                len(layer.vector_objects)
                for layer in self._owner._layers
                if layer.layer_type == "vector"
            ),
            bool(getattr(self._owner, "_vector_preview", None)),
            getattr(self._owner, "_vector_preview_layer_id", None),
        )
        self._owner._invalidate_layer_cache()
        self._owner._recompose()
        if self._owner._event_bus:
            with suppress(Exception):
                self._owner._event_bus.emit(
                    self._doc_changed_event,
                    DocumentChangeEvent(change_type="vector_command", source="canvas"),
                )
        return True
