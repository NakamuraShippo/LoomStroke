"""Command-backed native layer merging with GPU results supplied by the view port."""

from copy import copy
from uuid import uuid4

import numpy as np

from loomstroke.core.command_types import Command
from loomstroke.core.events import Events

from .color_transform import CmykColorTransform
from .pixels import NativeCmykPixels


class NativeCmykMergeCommand(Command):
    def __init__(self, canvas, renderer, mode, *, source_id=None, target_id=None):
        from loomstroke.core.document_snapshot_builder import (  # noqa: PLC0415
            DocumentSnapshotBuilder,
        )

        super().__init__()
        self.before = DocumentSnapshotBuilder.capture_canvas_layers(canvas)
        self.before_active = canvas._active_layer_id
        if mode == "down":
            identifiers = [layer.id for layer in canvas._layers]
            if source_id not in identifiers or target_id not in identifiers:
                raise ValueError("Native Merge Down requires top-level raster layers")
            source, target = canvas._get_layer_by_id(source_id), canvas._get_layer_by_id(target_id)
            if any(
                layer.layer_type != "raster" or layer.locked or layer.clipping
                for layer in (source, target)
            ):
                raise ValueError("Native Merge Down requires unlocked, unclipped raster layers")
            layers = [target, source]
        else:
            layers = canvas._layers
        pixels = renderer(layers)
        if mode == "flatten":
            pixels = self._flatten_pixels(canvas, pixels)
        pending = list(self.before)
        template = None
        while pending:
            candidate = pending.pop(0)
            if candidate.layer_type == "raster" and (mode != "down" or candidate.id == target_id):
                template = candidate
                break
            pending.extend(candidate.children)
        if template is None:
            raise ValueError("No raster layer to merge")
        result = copy(template)
        result.id = target_id if mode == "down" else f"layer-cmyk-{uuid4().hex}"
        result.name = (
            template.name if mode == "down" else ("Flattened" if mode == "flatten" else "Merged")
        )
        result.image, result.native_cmyk = pixels.preview(), pixels
        result.position = (0, 0)
        result.metadata = {}
        result.children = []
        result.layer_mask_image = None
        result.opacity, result.blend_mode = 1.0, "normal"
        result.clipping, result.alpha_lock, result.locked = False, False, False
        result.visible = True
        if mode == "flatten":
            self.after = [result]
        elif mode == "stamp":
            self.after = [*self.before, result]
        else:
            self.after = [
                result if layer.id == target_id else layer
                for layer in self.before
                if layer.id != source_id
            ]
        self.result_id = result.id

    @staticmethod
    def _flatten_pixels(canvas, pixels):
        maximum = np.iinfo(pixels.inks.dtype).max
        paper = canvas._background.getRgb()[:3]
        paper_ink = np.zeros(4, np.uint64)
        if paper != (255, 255, 255):
            rgb = np.array([[(*paper, 255)]], np.uint8)
            ink, _ = CmykColorTransform.from_rgb(rgb, pixels.profile)
            paper_ink = ink[0, 0].astype(np.uint64) * (257 if pixels.bits == 16 else 1)
        alpha = pixels.alpha[:, :, None].astype(np.uint64)
        inks = (
            (pixels.inks.astype(np.uint64) * alpha + paper_ink * (maximum - alpha) + maximum // 2)
            // maximum
        ).astype(pixels.inks.dtype)
        return NativeCmykPixels(inks, np.full_like(pixels.alpha, maximum), pixels.profile)

    @property
    def retained_bytes(self):
        return sum(
            layer.native_cmyk.inks.nbytes + layer.native_cmyk.alpha.nbytes
            for layer in [*self.before, *self.after]
            if layer.native_cmyk is not None
        )

    def apply(self, canvas):
        self._replace(canvas, self.after, self.result_id)
        self._executed = True
        return True

    def revert(self, canvas):
        if not self._executed:
            return False
        self._replace(canvas, self.before, self.before_active)
        self._executed = False
        return True

    @staticmethod
    def _replace(canvas, snapshots, active_id):
        from loomstroke.core.document_snapshot_builder import (  # noqa: PLC0415
            DocumentSnapshotBuilder,
        )

        layers = [
            DocumentSnapshotBuilder.snapshot_to_simple_layer(snapshot) for snapshot in snapshots
        ]
        canvas._layers = layers
        canvas._active_layer_id = active_id
        canvas._active_index = next(
            i for i, layer in enumerate(canvas._flatten_layers()) if layer.id == active_id
        )
        canvas._invalidate_layer_cache()
        canvas._recompose()
        canvas._publish_layer_event(
            Events.LAYER_CHANGED, layer=canvas._get_active_layer(), change_type="merged"
        )
