"""Encode nested PSD groups through the installed psd-tools API."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

from .atomic_export_writer import write_atomically
from .psd_document_data import PsdDocumentData


class PsdDocumentWriter:
    @staticmethod
    def write(path: Path, document: PsdDocumentData) -> None:
        from psd_tools import PSDImage  # noqa: PLC0415
        from psd_tools.constants import BlendMode, Compression  # noqa: PLC0415
        from psd_tools.psd.layer_and_mask import ChannelDataList  # noqa: PLC0415

        # Keep pixel transparency in its own channel so a user mask remains
        # independently editable instead of replacing the layer's alpha.
        psd = PSDImage.new("RGBA", (document.width, document.height))

        def append(nodes, parent):
            for node in nodes:
                blend = getattr(BlendMode, node.blend_mode.upper(), None)
                if blend is None:
                    raise ValueError(f"Unsupported PSD blend mode: {node.blend_mode}")
                opacity = round(min(1.0, max(0.0, node.opacity)) * 255)
                if node.group:
                    layer = psd.create_group(name=node.name, opacity=opacity, blend_mode=blend)
                    if parent is not psd:
                        layer.move_to_group(parent)
                    append(node.children, layer)
                else:
                    if node.image is None:
                        raise ValueError(f"PSD layer has no export pixels: {node.name}")
                    layer = psd.create_pixel_layer(
                        Image.fromarray(node.image),
                        name=node.name,
                        left=node.offset[0],
                        top=node.offset[1],
                        opacity=opacity,
                        blend_mode=blend,
                        compression=Compression.RLE,
                    )
                    if parent is not psd:
                        layer.move_to_group(parent)
                if node.mask is not None:
                    if node.group:
                        # psd-tools 1.15 shares the group's channel list with
                        # its closing record. Appending a mask to that shared
                        # list corrupts the serialized closing record.
                        layer._channels = ChannelDataList(list(layer._channels))
                    layer.create_mask(
                        Image.fromarray(node.mask), left=node.offset[0], top=node.offset[1]
                    )
                    layer.mask.disabled = not node.mask_enabled
                layer.visible = node.visible
                if not node.group or node.clipping:
                    layer.clipping = node.clipping

        append(document.layers, psd)
        psd._update_record()
        write_atomically(path, lambda temporary: psd.save(str(temporary)))
