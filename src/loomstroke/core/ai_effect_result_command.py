"""Private image-and-placement command; generated drafts remain outside Undo."""

from __future__ import annotations

import logging
from inspect import getattr_static
from typing import Any

from .ai_effect_result_payload import (
    _prepare_result_restore,
    _PreparedResult,
    _ResultImage,
    _standard_result_canvas,
)
from .canvas_layer_model import _Layer
from .command_types import Command
from .commands_layer_lookup import find_layer
from .layer_command_commit_context import _LayerCommandContext

logger = logging.getLogger(__name__)


class _ApplyAIEffectResultCommand(Command):
    """One prepared result on an existing AI layer, with self-owned PNGs."""

    def __init__(self, layer_id: str, png: bytes) -> None:
        super().__init__(merge_key=None)
        self.layer_id, self.png = layer_id, bytes(png)
        self._payload: tuple[_ResultImage, _ResultImage] | None = None
        self._prepared: _PreparedResult | None = None

    @property
    def retained_bytes(self) -> int:
        return (
            sum(item.retained_bytes for item in self._payload)
            if self._payload is not None
            else len(self.png)
        )

    def apply(self, canvas: Any) -> bool:
        return self._restore(canvas, use_after=True)

    def revert(self, canvas: Any) -> bool:
        return self._restore(canvas, use_after=False)

    def _restore(self, canvas: Any, *, use_after: bool) -> bool:
        if (
            self._payload is None
            or not _standard_result_canvas(canvas)
            or (not use_after and not self._executed)
        ):
            return False
        prepared = self._prepared
        if prepared is not None:
            success = use_after and prepared.before.canvas is canvas and prepared.install()
        else:
            success = self._restore_history(canvas, use_after=use_after)
        if success:
            self._executed = use_after
        return success

    def _restore_history(self, canvas: Any, *, use_after: bool) -> bool:
        layer = find_layer(canvas, self.layer_id)
        payload = self._payload
        if (
            payload is None
            or type(layer) is not _Layer
            or layer.ai_effect is None
            or layer.layer_type != "ai_effect"
        ):
            return False
        if any(item.native_cmyk is not None for item in canvas._flatten_layers()):
            return False
        scope = _LayerCommandContext(canvas, (layer,))
        prepared = None
        try:
            prepared = _prepare_result_restore(
                canvas, layer, scope, payload[int(use_after)], original_format=not use_after
            )
            return prepared.install()
        except Exception:
            if prepared is not None:
                prepared.rollback()
            logger.exception("AI result history restore failed")
            return False


_RESULT_COMMAND_METHODS = tuple(
    (name, getattr(_ApplyAIEffectResultCommand, name))
    for name in ("apply", "revert", "_restore", "_restore_history")
)


def _is_standard_result_command(command: Any) -> bool:
    return type(command) is _ApplyAIEffectResultCommand and all(
        getattr_static(command, name, None) is original
        for name, original in _RESULT_COMMAND_METHODS
    )
