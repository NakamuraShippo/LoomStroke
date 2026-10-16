"""Atomic cross-page mode conversion on the existing document history timeline."""

from __future__ import annotations

from copy import copy

from loomstroke.core.channels.channel_pixel_policy import ChannelPixelPolicy
from loomstroke.core.document_snapshot_builder import DocumentSnapshotBuilder
from loomstroke.core.page_history_memory import estimate_layer_snapshot_bytes
from loomstroke.io.file_io import ColorProfile

from .buffer import NativeCmykBuffer
from .capabilities import NativeCmykCapabilities
from .color_transform import CmykColorTransform
from .pixels import NativeCmykPixels


class DocumentCmykConvertCommand:
    label_key = "document.convert_cmyk"

    def __init__(self, profile: bytes, *, bits=8, source_profile=None):
        if bits not in (8, 16):
            raise ValueError("Native CMYK requires 8 or 16 bits")
        self.profile = CmykColorTransform.validate_profile(bytes(profile))
        self.bits = bits
        self.source_profile = source_profile
        self.before = None
        self.after = None
        self.affected_page_ids = ()
        self._prepared_token = None
        self._original_profile = None
        self._prepared_buffers = {}
        self._active_page_id = None

    @staticmethod
    def _token(pages):
        return (
            pages.get_active_page_id(),
            tuple(
                (page.id, id(pages._find_page_state(page.id).page.layers))
                for page in pages.list_pages()
            ),
            tuple(
                (layer.id, layer.texture_version, id(layer.image))
                for layer in pages._canvas._flatten_layers()
            ),
            pages._canvas._width,
            pages._canvas._height,
            id(getattr(pages._canvas, "color_profile", None)),
        )

    def prepare(self, pages):
        """Capture immutable RGB arrays on the calling UI thread."""
        if pages._canvas._in_transaction:
            return False
        before = {}
        retained = 0
        for summary in pages.list_pages():
            if summary.id == pages.get_active_page_id():
                snapshots = DocumentSnapshotBuilder.capture_canvas_layers(pages._canvas)
            else:
                page = pages.get_page_snapshot(summary.id)
                if page is None:
                    raise ValueError("A page could not be loaded")
                snapshots = page.layers
            before[summary.id] = snapshots
            retained += self._conversion_bytes(snapshots)
            if retained > 2 * 1024 * 1024 * 1024:
                raise ValueError("CMYK conversion exceeds the document working-memory budget")
        self.before = before
        self._original_profile = getattr(pages._canvas, "color_profile", None)
        self._active_page_id = pages.get_active_page_id()
        self.affected_page_ids = tuple(before)
        self._prepared_token = self._token(pages)
        return bool(before)

    def _conversion_bytes(self, snapshots):
        total = 0
        for snapshot in snapshots:
            if snapshot.layer_type == "raster":
                pixels = snapshot.image.shape[0] * snapshot.image.shape[1]
                native = pixels * 5 * (self.bits // 8)
                if native > 256 * 1024 * 1024:
                    raise ValueError("A CMYK layer exceeds the native pixel memory budget")
                total += 2 * native + 3 * snapshot.image.nbytes
            total += self._conversion_bytes(snapshot.children)
        return total

    def prepare_pixels(self):
        """ICC work consumes snapshot arrays only; safe for a worker thread."""
        self.after = {
            identifier: [self._convert(layer) for layer in layers]
            for identifier, layers in self.before.items()
        }
        pending = list(self.after[self._active_page_id])
        while pending:
            snapshot = pending.pop()
            if snapshot.native_cmyk is not None:
                self._prepared_buffers[snapshot.id] = NativeCmykBuffer(snapshot.native_cmyk)
            pending.extend(snapshot.children)

    def apply(self, pages):
        if pages._canvas._in_transaction:
            return False
        if self.before is None:
            if not self.prepare(pages):
                return False
            self.prepare_pixels()
        if self._prepared_token is not None and self._prepared_token != self._token(pages):
            raise ValueError("The document changed while preparing CMYK conversion")
        result = self.after is not None and self._replace(pages, self.after, ColorProfile.srgb())
        if result:
            self._prepared_token = None
        return result

    def revert(self, pages):
        return self.before is not None and self._replace(pages, self.before, self._original_profile)

    def _convert(self, snapshot):
        NativeCmykCapabilities.validate_snapshot(snapshot)
        if snapshot.native_cmyk is not None:
            raise ValueError("The document is already CMYK")
        result = copy(snapshot)
        result.children = [self._convert(child) for child in snapshot.children]
        if snapshot.layer_type == "raster":
            inks, alpha = CmykColorTransform.from_rgb(
                snapshot.image, self.profile, source_profile=self.source_profile
            )
            if self.bits == 16:
                inks, alpha = inks.astype("uint16") * 257, alpha.astype("uint16") * 257
            result.native_cmyk = NativeCmykPixels(inks, alpha, self.profile)
            result.image = result.native_cmyk.preview()
        return result

    def _replace(self, pages, replacements, profile):
        canvas = pages._canvas
        active_id = pages.get_active_page_id()
        states = {identifier: pages._find_page_state(identifier) for identifier in replacements}
        if any(state is None for state in states.values()):
            return False
        current = {layer.id: layer for layer in canvas._layer_tree().flatten_layers()}
        prepared = []

        def prepare(snapshots):
            for snapshot in snapshots:
                if snapshot.id not in current:
                    raise ValueError("The layer tree changed while converting the document")
                layer = current[snapshot.id]
                image = (
                    ChannelPixelPolicy.image(snapshot.image, straight=True).convertToFormat(
                        layer.image.format()
                    )
                    if layer.image is not None
                    else None
                )
                native = None
                if snapshot.native_cmyk is not None:
                    native = self._prepared_buffers.pop(snapshot.id, None)
                    if native is None:
                        native = NativeCmykBuffer(snapshot.native_cmyk)
                prepared.append((layer, image, native))
                prepare(snapshot.children)

        prepare(replacements[active_id])
        # Keep live layer identities and per-page histories intact.
        for layer, image, native in prepared:
            layer.image, layer.native_cmyk = image, native
            layer.texture_version += 1
        canvas.set_color_profile(profile)
        for identifier, layers in replacements.items():
            state = states[identifier]
            state.page.layers = layers
            state.dirty = True
            pages._emit_page_dirty_changed(identifier, True)
        canvas.channels().reset()
        canvas._invalidate_layer_cache()
        canvas._recompose()
        pages._emit_document_changed("document.color_mode")
        return True

    def estimated_retained_bytes(self, seen):
        return sum(
            estimate_layer_snapshot_bytes(layer, seen=seen)
            for group in (self.before or {}, self.after or {})
            for layers in group.values()
            for layer in layers
        )
