"""Place / Reload / Relink / Embed of resource-backed layers through canvas commands (CT-18).

Standard RGB Place and Embed commit one prepared command. Existing Reload and
Relink retain their transaction-based image/metadata path; source files are
never written. Only consumers on the active page are mutated.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from inspect import getattr_static
from pathlib import Path
from typing import Any

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QImageReader, QPainter

from loomstroke.core.canvas_service_simple import SimpleCanvasService
from loomstroke.core.canvas_service_simple_command_api import _DEFAULT_RESOURCE_METADATA_HOOKS
from loomstroke.core.canvas_service_simple_layer_api import _DEFAULT_PLACEMENT_HOOKS
from loomstroke.core.commands_layer_image_replace import ReplaceLayerImageCommand
from loomstroke.core.commands_layer_lookup import find_layer
from loomstroke.core.commands_layer_resource import SetLayerResourceCommand
from loomstroke.core.document_service import _DEFAULT_IMPORT_LAYER, DocumentServiceImpl
from loomstroke.core.layer_command_commit_context import _LayerCommandContext
from loomstroke.core.linked_assets import (
    PlaceResourcePlan,
    RelinkPlan,
    ResourceConsumer,
    ResourceConsumerIndex,
    ResourceLocatorResolver,
    ResourceMode,
    ResourcePlacePlanner,
    ResourceRecord,
    ResourceRelinkPlanner,
    ResourceStatus,
    ResourceStatusService,
)
from loomstroke.core.resource_embed_submit import (
    _capture_embed_scope,
    _prepare_resource_embed,
    _ResourceEmbedRejected,
)
from loomstroke.core.resource_place_command import PlaceResourceCommand

from .document_resource_reader import DocumentResourceReader

__all__ = ["ResourceLinkCoordinator"]

ImageReader = Callable[[Path], Any | None]
logger = logging.getLogger(__name__)


class ResourceLinkCoordinator:
    def __init__(
        self,
        host: Any,
        *,
        document_dir_provider: Callable[[], Path | None],
        image_reader: ImageReader | None = None,
    ) -> None:
        self._host = host
        self._document_dir = document_dir_provider
        self._image_reader = image_reader or self._default_image_reader

    # -- queries -----------------------------------------------------------------------

    def resolver(self) -> ResourceLocatorResolver:
        return ResourceLocatorResolver(self._document_dir())

    def status_service(self) -> ResourceStatusService:
        return ResourceStatusService(self.resolver())

    def index(self) -> ResourceConsumerIndex:
        return DocumentResourceReader(self._host).index()

    def statuses(
        self, index: ResourceConsumerIndex | None = None, *, refresh_hash: bool = True
    ) -> dict[str, ResourceStatus]:
        index = index or self.index()
        service = self.status_service()
        return {
            record.resource_id: service.inspect(record, refresh_hash=refresh_hash)
            for record in index.records()
        }

    def active_page_id(self) -> str:
        return DocumentResourceReader(self._host).active_page_id()

    # -- place -------------------------------------------------------------------------

    def plan_place(self, path: Path, mode: ResourceMode) -> PlaceResourcePlan:
        width, height = self._probe_size(path)
        return ResourcePlacePlanner(self.resolver()).plan(path, mode, width=width, height=height)

    def place(self, plan: PlaceResourcePlan) -> str | None:
        """Import the file as a new layer and attach the record; returns the layer id."""
        document = getattr(self._host, "document", None)
        importer = getattr(document, "import_layer", None)
        if not callable(importer):
            return None
        canvas = self._host.canvas
        if isinstance(canvas, SimpleCanvasService):
            if any(
                layer.native_cmyk is not None for layer in canvas._layer_tree().flatten_layers()
            ):
                return None
            if (
                isinstance(document, DocumentServiceImpl)
                and getattr_static(document, "import_layer", None) is _DEFAULT_IMPORT_LAYER
                and all(
                    getattr_static(canvas, name, None) is original
                    for name, original in _DEFAULT_PLACEMENT_HOOKS.items()
                )
            ):
                return self._place_prepared(plan, document, canvas)
        self._begin(canvas, "Place Image")
        try:
            layer_id = importer(str(plan.source_path))
            if layer_id:
                canvas.submit(SetLayerResourceCommand(str(layer_id), plan.record))
        finally:
            self._end(canvas)
        return str(layer_id) if layer_id else None

    def _place_prepared(self, plan, document, canvas) -> str | None:
        pages = getattr(self._host, "pages", None)
        original = canvas._doc, getattr(pages, "_pages", None), self.active_page_id()
        active = canvas.current_layer().id if canvas._layers else None

        def same_document():
            return (
                canvas._doc is original[0]
                and getattr(pages, "_pages", None) is original[1]
                and self.active_page_id() == original[2]
            )

        data = document._file_io.read(plan.source_path)
        if data is None:
            return None
        array = document._ensure_rgba(data[0])
        name = document._resolve_import_layer_name(data[1])
        image = self._qimage_from_array(array) if array is not None else None
        if (
            image is None
            or not same_document()
            or (canvas.current_layer().id if canvas._layers else None) != active
        ):
            return None
        command = PlaceResourceCommand(image, plan.record, name=name)
        if not canvas.submit(command) or not same_document():
            return None
        return (
            command.layer_id
            if find_layer(canvas, command.layer_id) is command._created.layer
            else None
        )

    # -- reload / relink / embed -------------------------------------------------------

    def reload(self, resource_id: str) -> tuple[int, str]:
        """Re-read the linked file into every consumer on the active page."""
        index = self.index()
        record = index.record(resource_id)
        if record is None or not record.is_linked:
            return 0, "resource_not_linked"
        status = self.status_service().inspect(record, refresh_hash=True)
        if not status.is_available or status.resolved_path is None:
            return 0, status.reason_code or status.state
        updated = record.with_content(
            locator=record.locator, content_hash=status.current_hash, byte_size=status.byte_size
        )
        return self._refresh_consumers(index, status.resolved_path, updated, "Reload Resource")

    def plan_relink(self, resource_id: str, path: Path) -> RelinkPlan | None:
        record = self.index().record(resource_id)
        if record is None:
            return None
        return ResourceRelinkPlanner(self.resolver()).plan(record, path)

    def relink(self, plan: RelinkPlan) -> tuple[int, str]:
        if not plan.is_readable:
            return 0, "resource_unreadable"
        width, height = self._probe_size(plan.source_path)
        updated = plan.updated_record(width=width or None, height=height or None)
        return self._refresh_consumers(self.index(), plan.source_path, updated, "Relink Resource")

    def embed(self, resource_id: str) -> tuple[int, str]:
        canvas = self._host.canvas
        scope = None
        if isinstance(canvas, SimpleCanvasService):
            candidate = _LayerCommandContext(canvas)
            if candidate.pages is getattr(self._host, "pages", None) and all(
                getattr_static(canvas, name, None) is original
                for name, original in _DEFAULT_RESOURCE_METADATA_HOOKS.items()
            ):
                try:
                    scope = _capture_embed_scope(canvas, resource_id)
                except Exception:
                    logger.exception("Resource Embed scope preparation failed")
                    return 0, "resource_embed_failed"
        index = self.index()
        record = index.record(resource_id)
        if record is None or record.mode == "embedded":
            return 0, "resource_not_linked"
        consumers = index.consumers_on_page(resource_id, self.active_page_id())
        if not consumers:
            return 0, "no_consumer_on_page"
        if scope is not None:
            return self._embed_prepared(canvas, scope, record, consumers)
        count = 0
        self._begin(canvas, "Embed Resource")
        try:
            for consumer in consumers:
                if canvas.submit(SetLayerResourceCommand(consumer.layer_id, record.embedded())):
                    count += 1
        finally:
            self._end(canvas)
        return count, ""

    @staticmethod
    def _embed_prepared(canvas, scope, record, consumers) -> tuple[int, str]:
        try:
            command = _prepare_resource_embed(canvas, scope, record, consumers)
            if not canvas.submit(command):
                return 0, "resource_embed_rejected"
            return (
                (len(command.layer_ids), "") if scope.current() else (0, "resource_scope_changed")
            )
        except _ResourceEmbedRejected as error:
            return 0, error.reason_code
        except Exception:
            logger.exception("Resource Embed preparation failed")
            return 0, "resource_embed_failed"

    # -- navigation --------------------------------------------------------------------

    def go_to_layer(self, consumer: ResourceConsumer) -> bool:
        pages = getattr(self._host, "pages", None)
        if consumer.page_id and consumer.page_id != self.active_page_id():
            setter = getattr(pages, "set_active_page", None)
            if not callable(setter):
                return False
            setter(consumer.page_id)
        activate = getattr(self._host.canvas, "set_active_layer", None)
        if callable(activate):
            activate(consumer.layer_id)
            return True
        return False

    def reveal_path(self, resource_id: str) -> Path | None:
        record = self.index().record(resource_id)
        if record is None or record.locator is None:
            return None
        return self.resolver().resolve(record.locator)

    # -- internals ---------------------------------------------------------------------

    def _refresh_consumers(
        self, index: ResourceConsumerIndex, path: Path, record: ResourceRecord, label: str
    ) -> tuple[int, str]:
        consumers = index.consumers_on_page(record.resource_id, self.active_page_id())
        if not consumers:
            return 0, "no_consumer_on_page"
        array = self._image_reader(path)
        qimage = self._qimage_from_array(array) if array is not None else None
        if qimage is None:
            return 0, "resource_decode_failed"
        canvas = self._host.canvas
        self._begin(canvas, label)
        try:
            for consumer in consumers:
                layer = find_layer(canvas, consumer.layer_id)
                if layer is None:
                    continue
                replacement = self._layer_image_for(layer, qimage)
                canvas.submit(ReplaceLayerImageCommand(consumer.layer_id, replacement))
                canvas.submit(SetLayerResourceCommand(consumer.layer_id, record))
        finally:
            self._end(canvas)
        return len(consumers), ""

    @staticmethod
    def _begin(canvas: Any, label: str) -> None:
        begin = getattr(canvas, "begin_transaction", None)
        if callable(begin):
            begin(label)

    @staticmethod
    def _end(canvas: Any) -> None:
        end = getattr(canvas, "end_transaction", None)
        if callable(end):
            end()

    @staticmethod
    def _probe_size(path: Path) -> tuple[int, int]:
        reader = QImageReader(str(path))
        size = reader.size()
        if not size.isValid():
            return 0, 0
        return max(0, size.width()), max(0, size.height())

    @staticmethod
    def _qimage_from_array(array: Any) -> QImage | None:
        if not isinstance(array, np.ndarray) or array.ndim != 3 or array.shape[2] not in (3, 4):
            return None
        rgba = np.ascontiguousarray(array[:, :, :4].astype(np.uint8, copy=False))
        if rgba.shape[2] == 3:
            alpha = np.full((*rgba.shape[:2], 1), 255, dtype=np.uint8)
            rgba = np.ascontiguousarray(np.concatenate([rgba, alpha], axis=2))
        height, width = rgba.shape[:2]
        image = QImage(rgba.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
        return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    @staticmethod
    def _layer_image_for(layer: Any, qimage: QImage) -> QImage:
        """Same semantics as import: image at the origin, buffer never shrinks."""
        current = getattr(layer, "image", None)
        width = max(qimage.width(), current.width() if isinstance(current, QImage) else 0)
        height = max(qimage.height(), current.height() if isinstance(current, QImage) else 0)
        if width == qimage.width() and height == qimage.height():
            return qimage
        expanded = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        expanded.fill(Qt.GlobalColor.transparent)
        painter = QPainter(expanded)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(0, 0, qimage)
        painter.end()
        return expanded

    def _default_image_reader(self, path: Path) -> Any | None:
        document = getattr(self._host, "document", None)
        file_io = getattr(document, "_file_io", None)
        reader = getattr(file_io, "read", None)
        if not callable(reader):
            return None
        data = reader(Path(path))
        if data is None:
            return None
        image = data[0]
        ensure = getattr(document, "_ensure_rgba", None)
        return ensure(image) if callable(ensure) else image
