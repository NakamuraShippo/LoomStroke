"""Fill and gradient command submit helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from typing import Any

from .channels.channel_command_capture import ChannelCommandCapture


class CanvasFillGradientSubmitService:
    """Submit fill / gradient commands and preserve canvas post-processing order."""

    def __init__(
        self,
        owner: Any,
        *,
        flood_fill_to_new_layer_type: type[Any] | tuple[type[Any], ...],
        pattern_fill_type: type[Any] | tuple[type[Any], ...],
    ) -> None:
        self._owner = owner
        self._flood_fill_to_new_layer_type = flood_fill_to_new_layer_type
        self._pattern_fill_type = pattern_fill_type

    def submit_flood_fill(self, cmd: Any) -> bool:
        capture = ChannelCommandCapture.for_layer(self._owner)
        if capture is not None and isinstance(cmd, self._flood_fill_to_new_layer_type):
            return False
        saved_alpha: Any | None = None
        alpha_layer: Any | None = None
        if not isinstance(cmd, self._flood_fill_to_new_layer_type):
            alpha_layer, saved_alpha = self._owner._alpha_preservation().save_flood_fill_alpha(
                getattr(cmd, "reference_layer_id", None)
            )

        success = bool(cmd.apply(self._owner))
        if not success:
            return False

        self._owner._alpha_preservation().restore_layer_alpha(alpha_layer, saved_alpha)

        is_pattern = isinstance(cmd, self._pattern_fill_type)
        label = "Pattern Fill" if is_pattern else "Bucket Fill"
        # Placed fills already freeze component-limited source patches; the
        # legacy capture uses offset-only patch coordinates.
        if capture is not None and getattr(cmd, "_placed_edit", None) is None:
            if not capture.record(cmd, label):
                return False
        else:
            self._owner._record_history_command(label, cmd)
        dirty = cmd.dirty_region
        target_layer_id = getattr(cmd, "target_layer_id", None)
        self._owner._command_dirty_region().notify_layer_dirty_region(
            dirty,
            target_layer_id,
            "pattern_fill" if is_pattern else "bucket_fill",
        )
        self._owner._recompose()
        return True

    def submit_gradient(self, cmd: Any) -> bool:
        capture = ChannelCommandCapture.for_layer(self._owner, getattr(cmd, "_layer_id", None))
        alpha_layer, saved_alpha = self._owner._alpha_preservation().save_gradient_alpha(cmd)

        success = bool(cmd.apply(self._owner))
        if not success:
            return False

        self._owner._alpha_preservation().restore_layer_alpha(alpha_layer, saved_alpha)

        if capture is not None:
            if not capture.record(cmd, "Gradient"):
                return False
        else:
            self._owner._record_history_command("Gradient", cmd)
        dirty = cmd.dirty_region
        layer_id = getattr(cmd, "_layer_id", None)
        self._owner._command_dirty_region().notify_layer_dirty_region(
            dirty,
            layer_id,
            "gradient",
        )
        self._owner._recompose()
        return True
