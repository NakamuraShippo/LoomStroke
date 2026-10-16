"""I/O module for LoomStroke.

Provides file input/output, autosave, and document management
functionality following Master.md specifications.
"""

from __future__ import annotations

import importlib

from .autosave import AutosaveManager, AutosaveState, RecoveryInfo
from .file_io import (
    ColorProfile,
    FileFormat,
    FileIO,
    FileIOProtocol,
    ImageMetadata,
)

_layer_io = importlib.import_module("loomstroke.io.layer_io")
CanvasSpec = getattr(_layer_io, "CanvasSpec", None)
DocumentMetadata = _layer_io.DocumentMetadata
DocumentSnapshot = getattr(_layer_io, "DocumentSnapshot", None)
LayerIO = _layer_io.LayerIO
LayerSnapshot = getattr(_layer_io, "LayerSnapshot", None)
PageSnapshot = getattr(_layer_io, "PageSnapshot", None)

__all__ = [
    # Autosave
    "AutosaveManager",
    "AutosaveState",
    "CanvasSpec",
    "ColorProfile",
    "DocumentMetadata",
    "DocumentSnapshot",
    "FileFormat",
    # File I/O
    "FileIO",
    "FileIOProtocol",
    "ImageMetadata",
    # Layer I/O
    "LayerIO",
    "LayerSnapshot",
    "PageSnapshot",
    "RecoveryInfo",
]

if LayerSnapshot is None:  # pragma: no cover - fallback when export missing
    __all__.remove("LayerSnapshot")
if DocumentSnapshot is None:  # pragma: no cover - fallback when export missing
    __all__.remove("DocumentSnapshot")
if PageSnapshot is None:  # pragma: no cover - fallback when export missing
    __all__.remove("PageSnapshot")
if CanvasSpec is None:  # pragma: no cover - fallback when export missing
    __all__.remove("CanvasSpec")

__version__ = "1.1.0"
