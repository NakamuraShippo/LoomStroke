"""Decode and assemble an image document independently of the live document."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtGui import QColor

from .. import __version__
from ..host_api import DocumentRef
from ..io.layer_io import DOCUMENT_FORMAT_VERSION, CanvasSpec, DocumentMetadata, PageSnapshot
from ..io.psd_document_reader import PsdDocumentReader
from .canvas_service_simple import SimpleCanvasService
from .document_snapshot_builder import DocumentSnapshotBuilder
from .psd_import_command import PsdImportCommand


@dataclass(slots=True)
class PreparedImageDocument:
    """Owned staging document; committing it never rereads the input file."""

    source: Path
    canvas: SimpleCanvasService
    metadata: DocumentMetadata
    page: PageSnapshot
    committed: bool = False


def _source_identity(path: Path) -> tuple[int, int, int, int]:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _staging_canvas(live: SimpleCanvasService, width: int, height: int) -> SimpleCanvasService:
    stage = SimpleCanvasService(
        width=width,
        height=height,
        background=QColor(0, 0, 0, 0),
        dpi=live._dpi,
        tile_size=live._tile_size,
        max_layers=live._max_layers,
        max_history_steps=live._history_limit,
    )
    stage._doc = DocumentRef(id=f"doc-{uuid.uuid4().hex}", name="Untitled")
    return stage


def _prepare_raster(owner: Any, live: SimpleCanvasService, file_data: Any) -> SimpleCanvasService:
    if file_data is None:
        raise ValueError("The image could not be decoded")
    image, image_metadata = file_data
    image = owner._ensure_rgba(image)
    height, width = image.shape[:2]
    stage = _staging_canvas(live, width, height)
    identifier = owner._import_into_simple_canvas_inner(stage, image, image_metadata, True, True)
    if identifier is None:
        raise ValueError("The decoded image could not be prepared")
    return stage


def _prepare_psd(owner: Any, live: SimpleCanvasService, path: Path) -> SimpleCanvasService:
    from psd_tools import PSDImage  # noqa: PLC0415

    psd = PSDImage.open(path)
    try:
        data = PsdDocumentReader(owner._file_io._validate_import_image).read_opened(path, psd)
        if not _has_raster_nodes(data.layers):
            raise ValueError("The PSD has no readable layer hierarchy")
        command = PsdImportCommand(data)
        if not command.layers:
            raise ValueError("The PSD has no layer hierarchy")
    except (ImportError, OSError, ValueError):
        return _prepare_raster(owner, live, owner._file_io._read_opened_psd(path, psd))
    stage = _staging_canvas(live, data.width, data.height)
    stage.replace_layers(command.layers)
    stage.set_active_layer(command.created_ids[-1])
    return stage


def _has_raster_nodes(nodes: tuple[Any, ...]) -> bool:
    return any(node.image is not None or _has_raster_nodes(node.children) for node in nodes)


def _validate_layer_images(stage: SimpleCanvasService) -> None:
    for layer in stage._layer_tree().flatten_layers():
        if layer.layer_type != "group" and layer.image is None:
            raise ValueError("A prepared image layer has no pixel buffer")
        for image in (layer.image, layer.layer_mask, layer.filter_mask):
            if image is not None and image.isNull():
                raise ValueError("A prepared image buffer could not be allocated")


def _validate_snapshot_budget(stage: SimpleCanvasService) -> None:
    # SnapshotBuilder represents groups with a full-canvas blank image, even
    # though the PSD hierarchy reader need not allocate group pixel buffers.
    pixels = 0
    for layer in stage._layer_tree().flatten_layers():
        pixels += (
            layer.image.width() * layer.image.height()
            if layer.image is not None
            else stage._width * stage._height
        )
        for mask in (layer.layer_mask, layer.filter_mask):
            if mask is not None:
                pixels += mask.width() * mask.height()
        if pixels > PsdDocumentReader.MAX_PIXELS:
            raise ValueError("Prepared PSD snapshot exceeds the import pixel budget")


def prepare_image_document(
    owner: Any, path: Path, *, layered: bool = False
) -> PreparedImageDocument:
    """Validate and prepare every layer before the live document can be replaced."""
    live = owner._canvas
    if not isinstance(live, SimpleCanvasService):
        raise ValueError("Image document preparation requires a supported canvas")
    source = Path(path)
    identity = _source_identity(source)
    stage = (
        _prepare_psd(owner, live, source)
        if layered
        else _prepare_raster(owner, live, owner._file_io.read(source))
    )
    _validate_layer_images(stage)
    if layered:
        _validate_snapshot_budget(stage)
    stage.history_clear()
    metadata = DocumentMetadata(
        width=stage._width,
        height=stage._height,
        background_color=(0, 0, 0, 0),
        dpi=(float(stage._dpi), float(stage._dpi)),
        color_profile=None,
        app_version=__version__,
        format_version=DOCUMENT_FORMAT_VERSION,
    )
    page = PageSnapshot(
        id="p_0001",
        index=0,
        layers=DocumentSnapshotBuilder.capture_simple_canvas_layers(stage),
        canvas=CanvasSpec(
            width=metadata.width,
            height=metadata.height,
            background_color=metadata.background_color,
            dpi=metadata.dpi,
        ),
    )
    if _source_identity(source) != identity:
        raise ValueError("The source image changed while it was being prepared")
    return PreparedImageDocument(source, stage, metadata, page)
