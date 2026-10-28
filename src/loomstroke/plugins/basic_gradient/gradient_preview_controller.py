"""Coordinate the Gradient tool's existing live preview and pending command."""

from __future__ import annotations

import contextlib
import logging

import numpy as np

from ...core.commands import _is_qimage_layer, _layer_transform
from ...core.commands_layer_lookup import find_layer
from ...core.gradient_preview_owner import GradientPreviewOwner
from ..common.document_capture_guard import (
    begin_document_capture_edit,
    release_document_capture_edit,
)
from .gradient_command import GradientCommand, _get_target_array, _render_gradient

logger = logging.getLogger(__name__)


def _rgba(color):
    return (
        color[0] * 255.0,
        color[1] * 255.0,
        color[2] * 255.0,
        (color[3] if len(color) > 3 else 1.0) * 255.0,
    )


class GradientPreviewController:
    def __init__(self, tool):
        self.tool, self.owner, self.pending = tool, None, None
        self._unwritten_capture = False

    def _canvas(self):
        return getattr(self.tool.host_api, "canvas", None) if self.tool.host_api else None

    def _acquired(self, lease):
        self.tool._preview_capture_lease = lease
        # The factory calls this only after receiving its own new token,
        # before any preview pixels can be written.
        self._unwritten_capture = lease is not None

    def take(self):
        tool = self.tool
        if self.owner is not None and not self.owner.is_closed:
            return False
        if self._unwritten_capture and not self.cancel():
            return False
        tool._preview_layer_id = tool._active_layer_id()
        try:
            owner = GradientPreviewOwner.capture(
                self._canvas(),
                tool._preview_layer_id,
                tool._resolve_preview_layer,
                begin_document_capture_edit,
                release_document_capture_edit,
                tool._preview_capture_lease,
                self._acquired,
                self._closed,
            )
        except Exception:
            logger.exception("Gradient preview could not acquire its original")
            return False
        if owner is None:
            if tool._preview_capture_lease is None:
                tool._preview_layer_id = None
            return False
        self.owner = owner
        self._unwritten_capture = False
        tool._preview_backup = owner.backup
        return True

    def resolve(self):
        tool, canvas = self.tool, self._canvas()
        if self.owner is not None:
            return self.owner.resolve(canvas)
        if canvas is None or tool._preview_layer_id is None:
            return None, None
        layer = find_layer(canvas, tool._preview_layer_id)
        return (layer, _get_target_array(layer)) if layer is not None else (None, None)

    def _command(self, layer_id):
        tool = self.tool
        x0, y0 = tool._line_start
        x1, y1 = tool._line_end
        return GradientCommand(
            layer_id,
            x0,
            y0,
            x1,
            y1,
            _rgba(tool.color_a),
            _rgba(tool.color_b),
            gradient_type=tool.gradient_type,
            dither=tool.dither,
            stops=tool._build_stops_255(),
        )

    def apply(self):
        tool, owner = self.tool, self.owner
        if not tool._line_start or not tool._line_end or tool.host_api is None:
            return
        if owner is None:
            tool._pending_commands.append(self._command(tool._active_layer_id()))
            tool._reset_to_idle()
            return
        if owner.phase == "queued":
            return
        if not owner.can_edit(self._canvas()):
            self.cancel()
            return
        command = self._command(owner.layer_id)
        command.set_pre_applied(owner.backup)
        command._set_document_capture_lease(owner.lease)
        command._bind_gradient_preview_owner(owner)
        owner.bind(command)
        self.pending = command
        tool._pending_commands.append(command)
        # Keep the original and editing state until submit acknowledges success.
        # An unknown submit rejection still leaves Esc/disable able to recover.

    def cancel(self):
        tool, owner = self.tool, self.owner
        if owner is None:
            if self._unwritten_capture and release_document_capture_edit(
                tool._preview_capture_lease
            ):
                self._acquired(None)
                tool._preview_layer_id = None
            return tool._preview_backup is None and tool._preview_capture_lease is None
        if self._canvas() is not owner.canvas:
            return False
        if owner.foreign_document() or owner.phase in {"recovered", "committed"}:
            return owner.cancel()
        layer, target = tool._resolve_preview_layer()
        if layer is not owner.layer or target is not owner.target:
            return False
        return owner.cancel()

    def _closed(self, owner):
        if self.owner is not owner:
            return
        tool = self.tool
        pending, self.pending = self.pending, None
        self.owner = None
        self._unwritten_capture = False
        tool._pending_commands[:] = [cmd for cmd in tool._pending_commands if cmd is not pending]
        tool._preview_backup = tool._preview_layer_id = tool._preview_capture_lease = None
        tool._reset_to_idle()

    def update(self, x0: float, y0: float, x1: float, y1: float) -> None:
        tool, owner = self.tool, self.owner
        if owner is not None and not owner.can_edit(self._canvas()):
            self.cancel()
            return
        if tool._preview_backup is None:
            return
        layer, target = tool._resolve_preview_layer()
        if layer is None or target is None:
            return

        # Restore original pixels so each preview is computed from scratch
        target[:] = tool._preview_backup

        height, width = target.shape[:2]
        mapping = _layer_transform(layer).to_affine()
        if mapping.inverse() is None:
            return
        ca, cb = _rgba(tool.color_a), _rgba(tool.color_b)
        gradient = _render_gradient(
            width,
            height,
            x0,
            y0,
            x1,
            y1,
            ca,
            cb,
            tool.gradient_type,
            tool.dither,
            stops=tool._build_stops_255(),
            pixel_transform=mapping,
        )

        # Source-Over composite (same logic as GradientCommand.apply)
        is_qimage = _is_qimage_layer(layer)
        if is_qimage:
            r, g, b = gradient[:, :, 0], gradient[:, :, 1], gradient[:, :, 2]
            a = gradient[:, :, 3] / 255.0
            src_b = (b * a).astype(np.uint8)
            src_g = (g * a).astype(np.uint8)
            src_r = (r * a).astype(np.uint8)
            dst_a = target[:, :, 3].astype(np.float32) / 255.0
            out_a = a + dst_a * (1.0 - a)
            for ch_src, ch_idx in [(src_b, 0), (src_g, 1), (src_r, 2)]:
                dst_ch = target[:, :, ch_idx].astype(np.float32)
                target[:, :, ch_idx] = np.clip(ch_src + dst_ch * (1.0 - a), 0.0, 255.0).astype(
                    np.uint8
                )
            target[:, :, 3] = np.clip(out_a * 255.0, 0.0, 255.0).astype(np.uint8)
        else:
            src_a = gradient[:, :, 3] / 255.0
            dst_a = target[:, :, 3] / 255.0
            out_a = src_a + dst_a * (1.0 - src_a)
            for ch in range(3):
                safe_a = np.where(out_a > 0, out_a, 1.0)
                target[:, :, ch] = np.where(
                    out_a > 0,
                    (gradient[:, :, ch] * src_a + target[:, :, ch] * dst_a * (1.0 - src_a))
                    / safe_a,
                    0.0,
                )
            target[:, :, 3] = out_a * 255.0

        # Notify GPU pipeline
        tv = getattr(layer, "texture_version", None)
        if tv is not None:
            layer.texture_version = tv + 1
        if owner is not None:
            owner.pixels_written()
        if hasattr(layer, "mark_dirty"):
            with contextlib.suppress(Exception):
                layer.mark_dirty(0, 0, width, height)
        canvas = getattr(tool.host_api, "canvas", None) if tool.host_api else None
        if canvas is not None:
            canvas._recompose()  # noqa: SLF001
