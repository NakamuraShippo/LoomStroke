# ruff: noqa: I001

"""Document service implementation for LoomStroke."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage, QPainter

from ..host_api import (
    CanvasService,
)
from ..host_api import CanvasSpec as HostCanvasSpec
from ..host_api import (
    EventBus,
    LoadOptions,
    ProgressCallback,
    SaveOptions,
    UILayoutService,
    UILayoutSnapshot,
)

if TYPE_CHECKING:

    from .document_service_protocol import _DocumentServiceProtocol

    DocumentServiceBase = _DocumentServiceProtocol

else:
    from ..host_api import DocumentService as DocumentServiceBase

from ..io.file_io import ColorProfile, FileFormat, FileIO, ImageMetadata
from ..io.layer_io import (
    CanvasSpec,
    DocumentMetadata,
    DocumentSnapshot,
    LayerIO,
    LayerSnapshot,
    PageSnapshot,
)
from .book_spread_layout import BookLayout
from .canvas import Canvas as LegacyCanvas
from .canvas_service_simple import SimpleCanvasService, _Layer
from .commands import BlendMode
from .document_capture_guard import is_document_capture_blocked
from .document_close_service import DocumentCloseService
from .document_metadata_service import DocumentMetadataService, _CanvasMetadata, _DocumentState
from .document_persistence_service import DocumentPersistenceService
from .document_revision_tracker import (
    DocumentRevisionOutcome,
    DocumentRevisionToken,
    DocumentRevisionTracker,
)
from .document_snapshot_builder import DocumentSnapshotBuilder
from .events import (
    Events,
    HistoryResetEvent,
)
from .history_failure_reporting import (
    bind_document_history_failure_source,
    history_failure_event_is_current,
)
from .layer import Layer
from .layout_serialization import payload_to_snapshot, snapshot_to_payload
from .page_service import PageServiceImpl

UILayoutPayload = dict[str, Any]


logger = logging.getLogger(__name__)


class DocumentServiceImpl(DocumentServiceBase):
    """Concrete implementation of :class:`DocumentService`."""

    def __init__(
        self,
        canvas: CanvasService,
        layer_io: LayerIO,
        file_io: FileIO,
        event_bus: EventBus,
        page_service: PageServiceImpl | None = None,
        *,
        ui_service_getter: Callable[[], UILayoutService | None] | None = None,
    ) -> None:

        self._canvas = canvas

        self._event_bus = event_bus

        self._page_service = page_service

        self._ui_service_getter = ui_service_getter

        self._logger = logging.getLogger(self.__class__.__name__)

        self._metadata_service = DocumentMetadataService(self)

        self._persistence_service = DocumentPersistenceService(self, layer_io, file_io)

        self._close_service = DocumentCloseService(self)

        # SAVE-ASYNC-REV-01: core-owned revision/epoch tracking so async
        # save completions can tell whether their snapshot still covers
        # the live document.  The EventBus stores handlers as weakrefs, so
        # the bound methods are cached as attributes.  DOC_OPENED follows
        # DOC_LOADED on the same load path and is deliberately not
        # subscribed (double epoch reset).
        self._revision_tracker = DocumentRevisionTracker()
        self._handler_revision_modified = self._on_revision_document_modified
        self._handler_revision_reset = self._on_revision_document_reset
        subscribe = getattr(event_bus, "subscribe", None) or getattr(event_bus, "on", None)
        if callable(subscribe):
            subscribe(Events.DOC_CHANGED, self._handler_revision_modified)
            subscribe(Events.DOC_CREATED, self._handler_revision_reset)
            subscribe(Events.DOC_LOADED, self._handler_revision_reset)
            subscribe(Events.DOC_CLOSED, self._handler_revision_reset)
        bind_document_history_failure_source(
            canvas, event_bus, page_service, self._capture_document_revision
        )

    @property
    def _layer_io(self) -> LayerIO:
        return self._persistence_service.layer_io

    @_layer_io.setter
    def _layer_io(self, value: LayerIO) -> None:
        self._persistence_service.layer_io = value

    @property
    def _file_io(self) -> FileIO:
        return self._persistence_service.file_io

    @_file_io.setter
    def _file_io(self, value: FileIO) -> None:
        self._persistence_service.file_io = value

    @property
    def _document_state(self) -> _DocumentState | None:
        return self._metadata_service.document_state

    @_document_state.setter
    def _document_state(self, value: _DocumentState | None) -> None:
        self._metadata_service.document_state = value

    @property
    def _loaded_metadata(self) -> DocumentMetadata | None:
        return self._metadata_service.loaded_metadata

    @_loaded_metadata.setter
    def _loaded_metadata(self, value: DocumentMetadata | None) -> None:
        self._metadata_service.loaded_metadata = value

    @property
    def _save_in_progress(self) -> bool:
        return self._persistence_service.save_in_progress

    @_save_in_progress.setter
    def _save_in_progress(self, value: bool) -> None:
        self._persistence_service.save_in_progress = value

    def reset_document_state(self) -> None:
        """Clear the cached document state (e.g., after creating a new document)."""
        self._metadata_service.reset_document_state()

    def close_document(self) -> None:
        """Release the current file's state and restore the startup canvas."""
        self._close_service.close_document()

    # ------------------------------------------------------------------
    # Document revision facade (SAVE-ASYNC-REV-01)

    def _on_revision_document_modified(self, _payload: Any) -> None:
        if not history_failure_event_is_current(_payload):
            return
        self._revision_tracker.mark_modified()

    def _on_revision_document_reset(self, _payload: Any) -> None:
        self._revision_tracker.reset_document()
        reset_residency = getattr(self._page_service, "_reset_page_residency", None)
        if callable(reset_residency):
            reset_residency()
        reset = getattr(self._canvas, "_reset_document_capture_gate", None)
        if callable(reset):
            reset()
        self._persistence_service.reset_deferred_autosave()

    def _document_capture_blocked(self) -> bool:
        return is_document_capture_blocked(self._canvas)

    def _recovery_sources_in_use(self) -> set[Path] | None:
        if self._page_service is None:
            return set()
        query = getattr(self._page_service, "_recovery_sources_in_use", None)
        return query() if callable(query) else None

    def _consume_deferred_autosave(self, path: str | Path) -> bool:
        return self._persistence_service.consume_deferred_autosave(path)

    def _capture_document_revision(self) -> DocumentRevisionToken:
        """Freeze the revision state a just-built snapshot covers."""
        return self._revision_tracker.capture_token()

    def _classify_document_revision(self, token: DocumentRevisionToken) -> DocumentRevisionOutcome:
        """Classify a snapshot token against the live document state."""
        return self._revision_tracker.classify(token)

    # ------------------------------------------------------------------

    # Save

    def save(
        self,
        path: str | Path,
        options: SaveOptions | None = None,
        *,
        progress: ProgressCallback | None = None,
    ) -> bool:
        return self._persistence_service.save(path, options, progress=progress)

    async def save_async(
        self,
        path: str | Path,
        options: SaveOptions | None = None,
        *,
        progress: ProgressCallback | None = None,
    ) -> bool:
        """Async save facade preserving the snapshot/worker encode contract.

        The delegated implementation keeps the original three-stage boundary:
        main-thread ``DocumentSnapshot`` capture, worker-thread
        ``asyncio.to_thread(_commit_save_snapshot, ...)`` ZIP encode, then
        main-thread completion notification.  The facade remains source-visible
        for compatibility tests that assert this off-thread encode contract.
        """
        return await self._persistence_service.save_async(path, options, progress=progress)

    def _compute_save_paths(self, path: str | Path) -> tuple[Path, Path]:
        return self._persistence_service.compute_save_paths(path)

    def resolve_book_layout(self) -> BookLayout:
        """Validated book layout of the open document (CT-06 B4-c4).

        The spread viewport reads the binding direction from here rather
        than from the Pages panel's runtime preview, so what is drawn
        matches what is saved and exported.
        """
        return self._metadata_service.resolve_book_layout()

    def read_masters_metadata(self) -> dict[str, Any]:
        """Defensive copy of the master-definitions namespace (CT-07 T3a).

        UI readers parse it with
        ``master_page_model.read_master_definitions`` — this accessor only
        hands out the raw namespace, exactly like ``resolve_book_layout``
        pairs with ``BookLayoutService`` on the book side.
        """
        return self._metadata_service.read_masters_metadata()

    def _prepare_save_metadata(self, opts: SaveOptions) -> DocumentMetadata:
        return self._metadata_service.prepare_save_metadata(opts)

    def _prepare_save_pages(
        self,
        metadata: DocumentMetadata,
        layers: Sequence[LayerSnapshot],
    ) -> tuple[list[PageSnapshot], str]:
        return self._metadata_service.prepare_save_pages(metadata, layers)

    def _commit_save_snapshot(
        self, temp_path: Path, target: Path, snapshot: DocumentSnapshot
    ) -> None:
        self._persistence_service.commit_save_snapshot(temp_path, target, snapshot)

    def _emit_save_success(
        self,
        target: Path,
        options: SaveOptions,
        metadata: DocumentMetadata,
        outcome: DocumentRevisionOutcome | None = None,
    ) -> None:
        if outcome is None:
            outcome = self._classify_document_revision(self._capture_document_revision())
        self._persistence_service.emit_save_success(target, options, metadata, outcome)

    def _reset_saved_document_state(
        self,
        metadata: DocumentMetadata,
        pages: list[PageSnapshot],
        active_page_id: str | None,
    ) -> None:
        self._metadata_service.reset_saved_document_state(metadata, pages, active_page_id)

    def _cleanup_save_temporary_files(self, temp_path: Path) -> None:
        self._persistence_service.cleanup_save_temporary_files(temp_path)

    def _emit_save_failure(
        self,
        target: Path,
        options: SaveOptions,
        metadata: DocumentMetadata,
        error: Exception,
    ) -> None:
        self._persistence_service.emit_save_failure(target, options, metadata, error)

    # ------------------------------------------------------------------

    # Load

    def load(
        self,
        path: str | Path,
        options: LoadOptions | None = None,
        *,
        progress: ProgressCallback | None = None,
    ) -> bool:
        return self._persistence_service.load(path, options, progress=progress)

    # ------------------------------------------------------------------

    # Layer import/export

    def export_layer(
        self,
        layer_id: str,
        path: str | Path,
        *,
        format: str = "PNG",
        metadata: Mapping[str, Any] | None = None,
    ) -> bool:

        layer = self._find_layer_snapshot(layer_id)

        if layer is None:
            self._logger.warning("Layer %s not found for export", layer_id)

            return False

        target = Path(path)

        fmt_name = format.upper()

        try:
            file_format = FileFormat[fmt_name]

        except KeyError:
            self._logger.error("Unsupported export format: %s", format)

            return False

        image = self._ensure_rgba(layer.image)

        meta = ImageMetadata(
            width=image.shape[1],
            height=image.shape[0],
            channels=image.shape[2],
            bit_depth=8,
        )

        if metadata:
            meta.extra = dict(metadata)

        try:
            self._file_io.write(target, image, meta, file_format)

            return True

        except Exception as exc:
            self._logger.exception("Layer export failed: %s", exc)

            return False

    def import_layer(
        self,
        path: str | Path,
        *,
        insert_above: bool = True,
        as_new: bool = True,
        progress: ProgressCallback | None = None,
    ) -> str | None:

        file_data = self._file_io.read(Path(path))

        if file_data is None:
            self._logger.error("Failed to read layer import: %s", path)

            return None

        image, image_meta = file_data

        image = self._ensure_rgba(image)

        if isinstance(self._canvas, SimpleCanvasService):
            return self._import_into_simple_canvas(image, image_meta, insert_above, as_new)

        if isinstance(self._canvas, LegacyCanvas):
            return self._import_into_legacy_canvas(image, image_meta, insert_above, as_new)

        self._logger.error("Unsupported canvas implementation for import")

        return None

    def import_psd_layers(self, path: str | Path) -> list[str] | None:
        """Import a nested PSD hierarchy as one undoable operation."""
        from .canvas_service_simple import SimpleCanvasService  # noqa: PLC0415
        from .psd_import_service import PsdImportService  # noqa: PLC0415

        psd_path = Path(path)
        if isinstance(self._canvas, SimpleCanvasService):
            try:
                return PsdImportService(self._canvas, self._file_io).import_layers(psd_path)
            except (ImportError, OSError, ValueError):
                self._logger.warning(
                    "Layered PSD import failed; trying the composite", exc_info=True
                )
        lid = self.import_layer(psd_path)
        return [lid] if lid else None

    def _prepare_image_document(self, path: str | Path, *, layered: bool = False) -> Any:
        from .image_document_preparation import prepare_image_document  # noqa: PLC0415

        return prepare_image_document(self, Path(path), layered=layered)

    def _commit_image_document(
        self, prepared: Any, *, on_committed: Callable[[], None] | None = None
    ) -> bool:
        from .image_document_commit import commit_image_document  # noqa: PLC0415

        return commit_image_document(self, prepared, on_committed=on_committed)

    # ------------------------------------------------------------------

    # Helpers

    def _build_document_metadata(self, options: SaveOptions) -> DocumentMetadata:
        return self._metadata_service.build_document_metadata(options)

    def _merge_loaded_metadata(
        self,
        current: DocumentMetadata,
        loaded: DocumentMetadata,
        options: SaveOptions,
    ) -> DocumentMetadata:
        return self._metadata_service.merge_loaded_metadata(current, loaded, options)

    def _build_pages_for_save(
        self,
        layers: Sequence[LayerSnapshot],
        metadata: DocumentMetadata,
    ) -> tuple[list[PageSnapshot], str]:
        return self._metadata_service.build_pages_for_save(layers, metadata)

    def _metadata_for_page(
        self,
        metadata: DocumentMetadata,
        page: PageSnapshot,
    ) -> DocumentMetadata:
        return self._metadata_service.metadata_for_page(metadata, page)

    @staticmethod
    def _canvas_spec_from_metadata(metadata: DocumentMetadata) -> CanvasSpec:
        return DocumentMetadataService.canvas_spec_from_metadata(metadata)

    @staticmethod
    def _host_canvas_spec_from_metadata(metadata: DocumentMetadata) -> HostCanvasSpec:
        return DocumentMetadataService.host_canvas_spec_from_metadata(metadata)

    def _resolve_color_profile(self, options: SaveOptions) -> ColorProfile | None:
        return self._metadata_service.resolve_color_profile(options)

    def _metadata_to_payload(self, metadata: DocumentMetadata) -> dict[str, Any]:
        return self._metadata_service.metadata_to_payload(metadata)

    def _make_document_state(
        self,
        metadata: DocumentMetadata,
        pages: list[PageSnapshot],
        active_page_id: str | None,
    ) -> _DocumentState:
        return _DocumentState(
            metadata=metadata,
            pages=pages,
            active_page_id=active_page_id,
        )

    def _capture_layers(self) -> list[LayerSnapshot]:

        return DocumentSnapshotBuilder.capture_canvas_layers(self._canvas)

    def _capture_simple_canvas_layers(
        self,
        canvas: SimpleCanvasService,
    ) -> list[LayerSnapshot]:

        capture = getattr(canvas, "get_document_layer_snapshots", None)
        if callable(capture):
            return list(capture())
        return DocumentSnapshotBuilder.capture_simple_canvas_layers(canvas)

    def _capture_layer_list(
        self,
        layers: list[Any],
        canvas: SimpleCanvasService,
    ) -> list[LayerSnapshot]:

        return DocumentSnapshotBuilder.capture_layer_list(layers, canvas)

    def _capture_single_layer(
        self,
        layer: Any,
        canvas: SimpleCanvasService,
    ) -> LayerSnapshot:

        return DocumentSnapshotBuilder.capture_single_layer(layer, canvas)

    def _capture_legacy_canvas_layers(self, canvas: LegacyCanvas) -> list[LayerSnapshot]:

        return DocumentSnapshotBuilder.capture_legacy_canvas_layers(canvas)

    def _apply_to_canvas(
        self,
        layers: Sequence[LayerSnapshot],
        metadata: DocumentMetadata,
    ) -> None:

        if isinstance(self._canvas, SimpleCanvasService):
            self._apply_to_simple_canvas(self._canvas, layers, metadata)

        elif isinstance(self._canvas, LegacyCanvas):
            self._apply_to_legacy_canvas(self._canvas, layers, metadata)

        else:
            raise RuntimeError("Unsupported canvas implementation")

    def _apply_to_simple_canvas(
        self,
        canvas: SimpleCanvasService,
        layers: Sequence[LayerSnapshot],
        metadata: DocumentMetadata,
    ) -> None:

        canvas.update_canvas_config(width=metadata.width, height=metadata.height)

        background_color = metadata.background_color

        canvas._background = QColor(background_color[0], background_color[1], background_color[2])

        q_layers = self._snapshots_to_layers(layers, metadata)

        canvas.replace_layers(q_layers)

    def _snapshots_to_layers(
        self,
        snapshots: Sequence[LayerSnapshot],
        metadata: DocumentMetadata,
    ) -> list[_Layer]:

        return DocumentSnapshotBuilder.snapshots_to_simple_layers(snapshots)

    def _snapshot_to_layer(
        self,
        snapshot: LayerSnapshot,
        metadata: DocumentMetadata,
    ) -> _Layer:

        del metadata
        return DocumentSnapshotBuilder.snapshot_to_simple_layer(snapshot)

    def _apply_to_legacy_canvas(
        self,
        canvas: LegacyCanvas,
        layers: Sequence[LayerSnapshot],
        metadata: DocumentMetadata,
    ) -> None:

        new_layers: list[Layer] = []

        for snapshot in layers:
            layer = Layer(name=snapshot.name, width=metadata.width, height=metadata.height)

            layer.id = snapshot.id

            layer.opacity = float(snapshot.opacity)

            layer.visible = snapshot.visible

            layer.locked = snapshot.locked
            cast(Any, layer).alpha_lock = getattr(snapshot, "alpha_lock", False)

            layer.blend_mode = snapshot.blend_mode

            layer.set_image_data(snapshot.image)

            new_layers.append(layer)

        canvas.layers = new_layers

        canvas.config.width = metadata.width

        canvas.config.height = metadata.height

        canvas.config.background_color = metadata.background_color

        canvas._active_index = len(new_layers) - 1

        canvas._invalidate_composite()

    def _reset_history_state(self, *, reason: str) -> None:

        clear_fn = getattr(self._canvas, "history_clear", None)

        if callable(clear_fn):
            try:
                clear_fn()

                self._emit_history_reset(reason)

            except Exception as exc:
                self._logger.debug("history_clear failed: %s", exc)

    def _emit_history_reset(self, reason: str) -> None:

        payload = HistoryResetEvent(
            reason=reason,
            source=self.__class__.__name__,
            timestamp=time.time(),
        )

        self._event_bus.emit(Events.DOC_HISTORY_RESET, payload)

    def _get_layout_service(self) -> UILayoutService | None:

        if not self._ui_service_getter:
            return None

        try:
            return self._ui_service_getter()

        except Exception as exc:
            self._logger.debug("Layout service getter raised: %s", exc)

            return None

    def _apply_ui_layout(self, layout: dict[str, Any] | None, *, clear_missing: bool) -> None:

        service = self._get_layout_service()

        if not layout or service is None:
            return

        snapshot = self._deserialize_ui_layout(layout)

        try:
            service.apply(
                snapshot,
                allow_restore_state=True,
                clear_missing=clear_missing,
                preserve_toolbars=True,
            )

        except Exception as exc:
            self._logger.warning("Failed to apply UI layout: %s", exc)

    def _serialize_ui_layout(self, options: SaveOptions) -> UILayoutPayload | None:

        if not options.include_ui_layout:
            return None

        service = self._get_layout_service()

        if service is None:
            return None

        try:
            snapshot = service.snapshot(include_panels=True)

        except Exception as exc:
            self._logger.warning("Failed to snapshot UI layout: %s", exc)

            return None

        payload: UILayoutPayload = snapshot_to_payload(snapshot)

        return payload

    def _deserialize_ui_layout(self, payload: UILayoutPayload) -> UILayoutSnapshot:

        return payload_to_snapshot(payload)

    def _extract_canvas_metadata(self) -> _CanvasMetadata:
        return self._metadata_service.extract_canvas_metadata()

    def _normalize_dpi(self, raw: Any) -> tuple[float, float]:
        return self._metadata_service.normalize_dpi(raw)

    def _find_layer_snapshot(self, layer_id: str) -> LayerSnapshot | None:

        for snapshot in self._capture_layers():
            if snapshot.id == layer_id:
                return snapshot

        return None

    def _ensure_rgba(self, data: NDArray[Any]) -> NDArray[np.uint8]:

        arr = np.asarray(data)

        if arr.ndim != 3:
            raise ValueError("image data must be HxWxC")

        if arr.shape[2] == 3:
            alpha = np.full((arr.shape[0], arr.shape[1], 1), 255, dtype=arr.dtype)

            arr = np.concatenate([arr, alpha], axis=2)

        if arr.dtype != np.uint8:
            arr = np.clip(arr, 0, 255).astype(np.uint8)

        return arr

    def _qimage_to_array(self, image: QImage) -> NDArray[np.uint8]:

        converted = image.convertToFormat(QImage.Format.Format_RGBA8888)

        byte_length = converted.bytesPerLine() * converted.height()

        buffer = converted.bits()

        if hasattr(buffer, "setsize"):
            buffer.setsize(byte_length)

            data_view = buffer

        else:
            data_view = buffer if isinstance(buffer, memoryview) else memoryview(buffer)

        arr = np.frombuffer(data_view, dtype=np.uint8, count=byte_length).reshape(
            converted.height(), converted.bytesPerLine()
        )

        arr = arr[:, : converted.width() * 4]

        return arr.reshape(converted.height(), converted.width(), 4).copy()

    def _array_to_qimage(
        self,
        data: NDArray[np.uint8],
        width: int,
        height: int,
    ) -> QImage:

        arr = self._ensure_rgba(data)

        if arr.shape[0] != height or arr.shape[1] != width:
            arr = np.pad(
                arr,
                (
                    (0, max(0, height - arr.shape[0])),
                    (0, max(0, width - arr.shape[1])),
                    (0, 0),
                ),
                mode="constant",
            )

            arr = arr[:height, :width]

        qimage = QImage(arr.data, width, height, arr.strides[0], QImage.Format.Format_RGBA8888)

        converted = qimage.copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

        return converted

    def _array_to_qimage_native_size(self, data: NDArray[np.uint8]) -> QImage:

        arr = np.ascontiguousarray(self._ensure_rgba(data))
        height, width = arr.shape[:2]

        qimage = QImage(arr.data, width, height, arr.strides[0], QImage.Format.Format_RGBA8888)

        return qimage.copy().convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)

    def _to_blend_mode(self, name: str) -> BlendMode:

        normalized = name.replace(" ", "_").upper()

        try:
            return BlendMode[normalized]

        except KeyError:
            return BlendMode.NORMAL

    def _emit_progress(
        self,
        value: float,
        stage: str,
        path: Path,
        callback: ProgressCallback | None,
    ) -> None:
        self._persistence_service.emit_progress(value, stage, path, callback)

    def _import_into_simple_canvas(
        self,
        image: NDArray[np.uint8],
        meta: ImageMetadata,
        insert_above: bool,
        as_new: bool,
    ) -> str | None:

        canvas = self._canvas

        assert isinstance(canvas, SimpleCanvasService)

        if any(layer.native_cmyk is not None for layer in canvas._layer_tree().flatten_layers()):
            from .native_cmyk.image_import_command import (  # noqa: PLC0415
                NativeCmykImageImportCommand,
            )

            command = NativeCmykImageImportCommand(
                canvas,
                image,
                self._resolve_import_layer_name(meta),
                insert_above=insert_above,
                as_new=as_new,
                source_profile=meta.color_profile.data if meta.color_profile else None,
            )
            return command.layer_id if canvas.submit(command) else None

        # Defer recompose so that add_layer / move_layer / set_active_layer
        # do not trigger intermediate composites while the layer stack is
        # being assembled.
        canvas.defer_recompose()

        try:
            return self._import_into_simple_canvas_inner(
                canvas,
                image,
                meta,
                insert_above,
                as_new,
            )
        finally:
            canvas.resume_recompose()

    def _import_into_simple_canvas_inner(
        self,
        canvas: SimpleCanvasService,
        image: NDArray[np.uint8],
        meta: ImageMetadata,
        insert_above: bool,
        as_new: bool,
    ) -> str | None:

        # Canvas size is NOT changed.  The imported image is stored at its
        # original resolution in the layer image so that pixels outside the
        # canvas bounds are preserved (non-destructive).

        layer_stack: list[_Layer] = getattr(canvas, "_layers", [])

        previous_active_index = getattr(canvas, "_active_index", len(layer_stack) - 1)

        prior_active_id = canvas.current_layer().id if layer_stack else None

        created_layer_id: str | None = None

        target_layer_id: str | None = None

        if as_new or not prior_active_id:
            layer_name = self._resolve_import_layer_name(meta)

            layer_ref = canvas.add_layer(layer_name)

            created_layer_id = layer_ref.id

            target_layer_id = layer_ref.id

            try:
                layer_stack = getattr(canvas, "_layers", [])

                new_index = len(layer_stack) - 1

                destination_index = new_index

                min_insert_index = 1 if layer_stack else 0

                if previous_active_index >= 0 and layer_stack:
                    min_insert_index = min(min_insert_index, len(layer_stack) - 1)

                    if insert_above:
                        destination_index = min(previous_active_index + 1, len(layer_stack) - 1)

                        destination_index = max(destination_index, min_insert_index)

                    else:
                        destination_index = max(previous_active_index, min_insert_index)

                if destination_index != new_index:
                    canvas.move_layer(new_index, destination_index)

                canvas.set_active_layer(layer_ref.id)

            except Exception as exc:  # pragma: no cover - defensive
                self._logger.debug("Failed to reorder imported layer: %s", exc)

        else:
            target_layer_id = prior_active_id

        if target_layer_id is None:
            self._logger.error("No target layer available for import")

            return None

        ok = self._paint_image_to_layer(canvas, target_layer_id, image)
        if not ok:
            if created_layer_id:
                canvas.remove_layer(created_layer_id)
            return None

        return target_layer_id

    def _paint_image_to_layer(
        self,
        canvas: SimpleCanvasService,
        layer_id: str,
        image: NDArray[np.uint8],
    ) -> bool:
        """Paint *image* directly onto *layer_id*, expanding the buffer if needed.

        Bypasses ``apply_image`` which clips to canvas bounds, so pixels
        outside the canvas are preserved (non-destructive import).

        Returns ``True`` on success.
        """
        qimage = canvas._numpy_to_qimage(image, 1.0)
        if qimage is None:
            self._logger.error("Failed to convert imported image to QImage")
            return False

        layer = canvas._get_layer_by_id(layer_id)
        if layer is None or layer.image is None:
            self._logger.error("Target layer %s has no image buffer", layer_id)
            return False

        img_h, img_w = image.shape[:2]

        # Expand the layer image to fit the imported image if needed.
        if img_w > layer.image.width() or img_h > layer.image.height():
            new_w = max(layer.image.width(), img_w)
            new_h = max(layer.image.height(), img_h)
            expanded = QImage(new_w, new_h, QImage.Format.Format_ARGB32_Premultiplied)
            expanded.fill(Qt.GlobalColor.transparent)
            layer.image = expanded

        painter = QPainter(layer.image)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.drawImage(0, 0, qimage)
        painter.end()

        layer.texture_version += 1
        canvas._invalidate_layer_cache()
        canvas._recompose()
        return True

    def _import_into_legacy_canvas(
        self,
        image: NDArray[np.uint8],
        meta: ImageMetadata,
        insert_above: bool,
        as_new: bool,
    ) -> str | None:

        canvas = self._canvas

        assert isinstance(canvas, LegacyCanvas)

        active_layer = canvas.active_layer

        if not as_new and active_layer is not None:
            try:
                active_layer.set_image_data(image)

            except Exception as exc:  # pragma: no cover - defensive
                self._logger.exception("Failed to replace legacy layer: %s", exc)

                return None

            return cast(str, active_layer.id)

        layer_name = self._resolve_import_layer_name(meta)

        layer = Layer(name=layer_name, width=image.shape[1], height=image.shape[0])

        layer.set_image_data(image)

        insert_index = len(canvas.layers)

        active_index = getattr(canvas, "_active_index", len(canvas.layers) - 1)

        min_insert_index = 1 if canvas.layers else 0

        if canvas.layers:
            if insert_above:
                insert_index = min(active_index + 1, len(canvas.layers))

                insert_index = max(insert_index, min_insert_index)

            else:
                insert_index = max(active_index, min_insert_index)

                insert_index = min(insert_index, len(canvas.layers))

        canvas.layers.insert(insert_index, layer)

        canvas._active_index = insert_index

        canvas._invalidate_composite()

        return cast(str, layer.id)

    def _resolve_import_layer_name(self, meta: ImageMetadata) -> str:

        if meta.extra:
            for key in ("layer_name", "name", "title"):
                value = meta.extra.get(key)

                if isinstance(value, str) and value.strip():
                    return value.strip()

        return "Imported Layer"


_DEFAULT_IMPORT_LAYER = DocumentServiceImpl.import_layer

__all__ = ["DocumentServiceImpl"]
