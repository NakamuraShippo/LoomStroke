"""Resolve the native GPU readback port without making the core depend on UI."""

import logging

from .merge_command import NativeCmykMergeCommand


class NativeCmykMergeService:
    @staticmethod
    def apply(canvas, mode, *, source_id=None, target_id=None):
        renderer = getattr(
            getattr(canvas, "_canvas_widget", None), "render_native_cmyk_layers", None
        )
        if renderer is None:
            logging.getLogger(__name__).warning(
                "Native merging requires the canvas GPU readback port"
            )
            return None
        try:
            command = NativeCmykMergeCommand(
                canvas, renderer, mode, source_id=source_id, target_id=target_id
            )
        except ValueError:
            logging.getLogger(__name__).exception("Native CMYK merge rejected")
            return None
        if not command.apply(canvas):
            return None
        canvas._record_history_command("Merge CMYK Layers", command)
        return command.result_id
