"""Validated PSD document dimensions and detached layer nodes."""

from dataclasses import dataclass

from .psd_layer_node import PsdLayerNode


@dataclass(frozen=True)
class PsdDocumentData:
    width: int
    height: int
    layers: tuple[PsdLayerNode, ...]
