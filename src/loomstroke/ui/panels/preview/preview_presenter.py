"""
Preview presenter - handles business logic and data management.

Part of the View/Presenter pattern split for preview panel.
Per Master.md A3 specifications.
"""

from __future__ import annotations

import base64
import logging
import re
from collections.abc import Mapping
from contextlib import suppress
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias, cast

import numpy as np
from numpy.typing import NDArray
from PySide6.QtCore import QBuffer, QIODevice, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QImage, QPixmap

from loomstroke.ui.typing import Signal0, Signal3

Events: Any | None
try:
    from ....core.events import Events as _ImportedEvents
except ImportError:  # pragma: no cover - optional dependency
    Events = None
else:
    Events = _ImportedEvents

logger = logging.getLogger(__name__)


ImageArray: TypeAlias = np.ndarray[Any, np.dtype[np.generic]]
_IMAGE_KEYS = (
    "data",
    "bytes",
    "payload",
    "preview",
    "preview_png",
    "preview_data",
    "image",
    "output",
    "result",
    "result_data",
    "image_data",
    "png",
    "uri",
    "url",
    "name",
)
_PATH_KEYS = (
    "path",
    "file",
    "filename",
    "filepath",
    "full_path",
    "output_path",
    "url",
    "uri",
)


class PreviewPresenter(QObject):
    """
    Presenter for the preview panel.

    Handles business logic, data management, and event coordination.
    """

    # Signals for external communication
    # (No apply/save/discard actions are exposed from preview UI in this mode)

    # Internal state signals
    image_changed = Signal(QPixmap, int, int)  # pixmap, width, height
    display_cleared = Signal()

    def __init__(self, event_bus: Any | None = None):
        """
        Initialize the presenter.

        Args:
            event_bus: EventBus instance for communication
        """
        super().__init__()

        self.current_image: ImageArray | None = None
        self.event_bus = event_bus
        self.event_tokens: list[Any] = []

        self._subscribe_events()

    def set_image(self, image_data: ImageArray) -> None:
        """
        Set the preview image.

        Args:
            image_data: Image as numpy array (H, W, C)
        """
        self.current_image = image_data

        # Convert to QPixmap
        pixmap = self._array_to_pixmap(image_data)
        if pixmap:
            height, width = image_data.shape[:2]
            self.image_changed.emit(pixmap, width, height)
            logger.debug(f"Preview image set: {width}x{height}")

    def set_image_from_qpixmap(self, pixmap: QPixmap) -> None:
        """Set preview image from QPixmap."""
        if pixmap.isNull():
            logger.debug("Skipping null preview pixmap")
            return

        width = int(pixmap.width())
        height = int(pixmap.height())
        if width <= 0 or height <= 0:
            logger.debug("Skipping invalid preview pixmap size: %sx%s", width, height)
            return

        # Display immediately even if array conversion fails.
        self.image_changed.emit(pixmap, width, height)

        try:
            image = self._qpixmap_to_array(pixmap)
            if image is None:
                image = self._qpixmap_to_array_via_pillow(pixmap)

            if image is None:
                self.current_image = None
                logger.debug("Failed to convert preview pixmap to ndarray; current_image cleared")
                return

            self.current_image = image
        except Exception as e:
            logger.error("Error converting QPixmap to preview image: %s", e)
            self.current_image = None

    def _qpixmap_to_array(self, pixmap: QPixmap) -> ImageArray | None:
        """Convert QPixmap to RGBA ndarray."""
        qimage = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        width = qimage.width()
        height = qimage.height()
        if width <= 0 or height <= 0:
            return None

        bytes_per_line = qimage.bytesPerLine()
        if bytes_per_line <= 0:
            return None

        expected = int(height * bytes_per_line)
        if expected <= 0:
            return None

        try:
            raw_bytes = self._qimage_to_bytes(qimage, expected)
            if raw_bytes is None:
                return None
            raw = np.frombuffer(raw_bytes, np.uint8)
        except Exception:
            return None
        if raw.size < expected:
            return None

        rows = raw.reshape((height, bytes_per_line))
        row_bytes = width * 4
        if row_bytes <= 0 or row_bytes > bytes_per_line:
            return None

        arr = rows[:, :row_bytes].copy()
        reshaped = arr.reshape((height, width, 4))
        if reshaped.size != int(width * height * 4):
            return None
        return cast(ImageArray, reshaped)

    def _qpixmap_to_array_via_pillow(self, pixmap: QPixmap) -> ImageArray | None:
        """Fallback conversion via PNG bytes and Pillow."""
        try:
            from PIL import Image
        except Exception:
            return None

        try:
            buffer = QBuffer()
            if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
                return None
            if not pixmap.save(buffer, "PNG"):
                buffer.close()
                return None

            data = bytes(cast(Any, buffer.data()))
            buffer.close()
            with Image.open(BytesIO(data)) as image:
                array = np.array(image.convert("RGBA"))
            return cast(ImageArray, array)
        except Exception:
            return None

    def set_image_from_bytes(
        self, image_bytes: bytes | bytearray | memoryview | str | Path
    ) -> None:
        """
        Set preview image from bytes.

        Args:
            image_bytes: Image data as bytes
        """
        # Convert bytes to numpy array
        arr = self._bytes_to_array(image_bytes)
        if arr is not None:
            self.set_image(arr)

    def clear_preview(self) -> None:
        """Clear the preview."""
        self.current_image = None
        self.display_cleared.emit()
        logger.debug("Preview cleared")

    def get_current_image(self) -> ImageArray | None:
        """
        Get the current preview image.

        Returns:
            Image data or None
        """
        return self.current_image

    def _decode_base64_if_possible(self, value: str) -> bytes | None:
        """Decode a base64-like string without raising exceptions."""
        normalized = value.strip().strip('"').strip("'")
        if not normalized:
            return None

        # Avoid trying to decode non-base64-looking strings (like URLs or local paths)
        if not re.fullmatch(r"[A-Za-z0-9+/=\s]+", normalized):
            return None

        try:
            return base64.b64decode(normalized, validate=False)
        except Exception:
            return None

    def _array_to_pixmap(self, image_data: NDArray[Any]) -> QPixmap | None:
        """
        Convert numpy array to QPixmap.

        Args:
            image_data: Image array

        Returns:
            QPixmap or None if conversion fails
        """
        try:
            if image_data.dtype != np.uint8:
                image_data = image_data.astype(np.uint8, copy=True)
            elif not image_data.flags.c_contiguous:
                image_data = image_data.copy()

            height, width = image_data.shape[:2]

            # Determine format
            if len(image_data.shape) == 3:
                if image_data.shape[2] == 4:
                    # RGBA
                    format = QImage.Format.Format_RGBA8888
                elif image_data.shape[2] == 3:
                    # RGB
                    format = QImage.Format.Format_RGB888
                else:
                    logger.error(f"Unsupported channel count: {image_data.shape[2]}")
                    return None
            else:
                # Grayscale
                format = QImage.Format.Format_Grayscale8

            # Create QImage
            bytes_per_line = image_data.strides[0]
            qimage = QImage(image_data.data, width, height, bytes_per_line, format)
            copied_qimage = qimage.copy()

            # Convert to pixmap
            return QPixmap.fromImage(copied_qimage)

        except Exception as e:
            logger.error(f"Error converting array to pixmap: {e}")
            return None

    @staticmethod
    def _qimage_to_bytes(qimage: QImage, expected_size: int) -> bytes | None:
        """Read QImage bytes in a version-safe way."""
        try:
            bits = qimage.constBits()
        except Exception:
            try:
                bits = qimage.bits()
            except Exception:
                return None

        try:
            buffer_view = memoryview(bits)
            if len(buffer_view) >= expected_size:
                return buffer_view[:expected_size].tobytes()
        except Exception:
            logger.debug("qimage memoryview extract failed", exc_info=True)

        try:
            return bytes(bits)
        except Exception:
            logger.debug("qimage bytes extract failed", exc_info=True)

        return None

    def _bytes_to_array(
        self,
        image_bytes: bytes | bytearray | memoryview | str | Path,
    ) -> NDArray[Any] | None:
        """
        Convert image bytes to numpy array.

        Args:
            image_bytes: Image bytes

        Returns:
            Numpy array or None if conversion fails
        """
        try:
            # Accept base64-like data URI / base64 string as well.
            if isinstance(image_bytes, bytearray):
                image_bytes = bytes(image_bytes)
            elif isinstance(image_bytes, memoryview):
                image_bytes = image_bytes.tobytes()
            elif isinstance(image_bytes, bytes):
                raw_bytes = image_bytes
                try:
                    normalized = raw_bytes.decode("utf-8")
                    decoded = self._decode_base64_if_possible(normalized)
                    if decoded is not None:
                        image_bytes = decoded
                except Exception:
                    image_bytes = raw_bytes
            elif isinstance(image_bytes, Path):
                image_bytes = str(image_bytes)

            if isinstance(image_bytes, str):
                normalized = image_bytes.strip().strip('"').strip("'")

                if normalized.startswith("file://"):
                    normalized = normalized[7:]

                candidate_path = Path(normalized)
                if candidate_path.exists() and candidate_path.is_file():
                    image_bytes = candidate_path.read_bytes()
                else:
                    if normalized.startswith(("http://", "https://")):
                        logger.debug("Ignoring remote URL preview payload: %s", normalized[:80])
                        return None
                    if normalized.startswith("data:"):
                        comma = normalized.find(",")
                        if comma != -1:
                            normalized = normalized[comma + 1 :]

                    decoded = self._decode_base64_if_possible(normalized)
                    image_bytes = decoded if decoded is not None else normalized.encode("utf-8")

            if not isinstance(image_bytes, bytes):
                return None
            payload = image_bytes

            # Create QImage from bytes
            qimage = QImage()
            if not qimage.loadFromData(payload):
                fallback = self._decode_image_with_pillow(payload)
                if fallback is None:
                    logger.error("Failed to load image from bytes")
                    return None
                return cast(ImageArray, fallback.copy())

            if qimage.isNull():
                logger.error("Failed to load image from bytes")
                return None

            # Get dimensions
            width = qimage.width()
            height = qimage.height()

            # Convert to RGB888 format
            qimage = qimage.convertToFormat(QImage.Format.Format_RGB888)

            # Get bytes
            bytes_per_line = qimage.bytesPerLine()
            if bytes_per_line < width * 3:
                return None

            pixel_bytes = self._qimage_to_bytes(qimage, height * bytes_per_line)
            if pixel_bytes is None:
                return None

            raw = np.frombuffer(pixel_bytes, np.uint8)
            if raw.size < height * bytes_per_line:
                return None

            rows = raw[: height * bytes_per_line].reshape((height, bytes_per_line))
            arr = rows[:, : (width * 3)].reshape((height, width, 3))
            # Return copy to avoid memory issues
            return cast(ImageArray, arr.copy())

        except Exception as e:
            logger.error(f"Error converting bytes to array: {e}")
            return None

    def _decode_image_with_pillow(self, image_bytes: bytes) -> NDArray[Any] | None:
        """Decode image bytes using Pillow as a fallback.

        This keeps preview decoding functional when QImage cannot parse a payload
        (for example, for some PIL-friendly formats or incomplete headers).
        """
        try:
            from PIL import Image
        except Exception:
            return None

        try:
            with Image.open(BytesIO(image_bytes)) as image:
                array = np.array(image) if image.mode == "RGBA" else np.array(image.convert("RGB"))
                return cast(ImageArray, array)
        except Exception:
            return None

    def _subscribe_events(self) -> None:
        """Subscribe to EventBus events."""
        if not self.event_bus:
            return

        if Events is None:
            logger.warning(
                "Could not import Events; preview presenter will not receive job updates"
            )
            return

        token = self.event_bus.on(Events.AI_JOB_RESULT, self._on_job_result)
        self.event_tokens.append(token)

        token = self.event_bus.on(Events.AI_JOB_PROGRESS, self._on_job_progress)
        self.event_tokens.append(token)

        token = self.event_bus.on(Events.AI_PREVIEW_READY, self._on_preview_ready)
        self.event_tokens.append(token)

        logger.debug("PreviewPresenter subscribed to EventBus")

    def _on_job_result(self, payload: Any) -> None:
        """
        Handle AI job result event.

        Args:
            payload: JobResultEvent payload
        """
        # Ensure processing happens in main thread
        QTimer.singleShot(
            0,
            lambda payload=payload: self._handle_job_result(payload),
        )

    @Slot(object)
    def _on_preview_ready(self, payload: Any) -> None:
        """Handle AI_PREVIEW_READY event in main thread."""
        QTimer.singleShot(
            0,
            lambda payload=payload: self._handle_job_result(payload),
        )

    @Slot(object)
    def _handle_job_result(self, payload: Any) -> None:
        """Handle job result in main thread."""
        try:
            result_data = self._extract_image_payload(
                payload,
                ("result", "result_data", "data", "output"),
            )
            if result_data is not None:
                self._apply_image_payload(result_data)
                logger.info(
                    "Preview updated with job %s result",
                    getattr(payload, "job_id", "?"),
                )
        except Exception as e:
            logger.error(f"Error handling job result: {e}")

    def _on_job_progress(self, payload: Any) -> None:
        """
        Handle AI job progress event.

        Args:
            payload: JobProgressEvent payload
        """
        # Handle preview updates during generation
        if (
            self._extract_image_payload(
                payload,
                ("preview_data", "preview_png", "preview", "image", "data", "output"),
            )
            is not None
        ):
            QTimer.singleShot(
                0,
                lambda payload=payload: self._handle_job_progress(payload),
            )

    @Slot(object)
    def _handle_job_progress(self, payload: Any) -> None:
        """Handle job progress in main thread."""
        try:
            preview_data = self._extract_image_payload(
                payload,
                ("preview_data", "preview_png", "preview", "image", "data", "output"),
            )
            if preview_data is not None:
                self._apply_image_payload(preview_data, log_prefix="progress")

            message = getattr(payload, "message", None)
            if message:
                logger.debug("Job progress: %s", message)

        except Exception as exc:
            logger.error("Error handling job progress: %s", exc)

    def _extract_image_payload(
        self,
        payload: Any,
        keys: tuple[str, ...],
    ) -> Any | None:
        """Extract image-like payload from event-like objects.

        Supports nested payload wrappers such as ``JobResult`` where image bytes are in
        ``result.output`` and progress payloads using ``preview_data``.
        """
        if payload is None:
            return None

        seen: set[int] = set()
        search_keys = tuple(
            dict.fromkeys(
                (
                    *keys,
                    "preview_data",
                    "preview_png",
                    "preview",
                    "image",
                    "payload",
                    "result",
                    "result_data",
                    "data",
                    "output",
                    "images",
                    "image_data",
                    "png",
                    "uri",
                    "url",
                    "name",
                    "path",
                    "file",
                    "filename",
                    "filepath",
                    "full_path",
                    "output_path",
                ),
            )
        )
        terminal_types = (NDArray[Any], bytes, bytearray, memoryview, str, Path)

        def _as_mapping(value: Any) -> Mapping[str, Any] | None:
            if isinstance(value, Mapping):
                return value

            if hasattr(value, "__dict__"):
                with suppress(Exception):
                    mapping = vars(value)
                    if isinstance(mapping, Mapping):
                        return mapping

            if hasattr(value, "__slots__"):
                with suppress(Exception):
                    slots = value.__slots__
                if isinstance(slots, str):
                    slots = (slots,)
                if isinstance(slots, tuple):
                    values: dict[str, Any] = {}
                    for slot in slots:
                        if isinstance(slot, str) and hasattr(value, slot):
                            values[slot] = getattr(value, slot)
                    return values

            return None

        def _extract(value: Any) -> Any | None:
            if value is None:
                return None

            if isinstance(value, terminal_types):
                return value

            if isinstance(value, (list, tuple)):
                for item in value:
                    nested = _extract(item)
                    if nested is not None:
                        return nested
                return None

            if id(value) in seen:
                return None
            seen.add(id(value))

            mapping = _as_mapping(value)
            if mapping is None:
                return None

            for key in search_keys:
                if key not in mapping:
                    continue
                nested = _extract(mapping[key])
                if nested is not None:
                    return nested

            for nested_value in mapping.values():
                nested = _extract(nested_value)
                if nested is not None:
                    return nested

            return None

        return _extract(payload)

    def _apply_image_payload(
        self,
        image_data: Any,
        *,
        log_prefix: str = "result",
    ) -> None:
        """Apply image payload to preview."""
        extracted = self._extract_image_payload(
            image_data,
            ("result", "result_data", "data", "output", "preview_data", "preview_png"),
        )
        if extracted is not None and extracted is not image_data:
            self._apply_image_payload(extracted, log_prefix=log_prefix)
            return

        if isinstance(image_data, np.ndarray):
            self.set_image(image_data)
            return

        if isinstance(image_data, (bytes, bytearray, memoryview)):
            self.set_image_from_bytes(bytes(image_data))
            logger.debug("Preview updated from %s payload (bytes)", log_prefix)
            return

        if isinstance(image_data, (str, Path)):
            self.set_image_from_bytes(image_data)
            logger.debug(
                "Preview updated from %s payload (%s)", log_prefix, type(image_data).__name__
            )
            return

        if isinstance(image_data, Mapping):
            for key in _IMAGE_KEYS:
                value = image_data.get(key)
                if value is None:
                    continue
                self._apply_image_payload(value, log_prefix=log_prefix)
                return

            images = image_data.get("images")
            if isinstance(images, (list, tuple)) and images:
                for image in images:
                    if image is None:
                        continue
                    self._apply_image_payload(image, log_prefix=log_prefix)
                    return

            for key in _PATH_KEYS:
                value = image_data.get(key)
                if value:
                    self._apply_image_payload(value, log_prefix=log_prefix)
                    return

        if isinstance(image_data, (list, tuple)):
            for item in image_data:
                if item is None:
                    continue
                self._apply_image_payload(item, log_prefix=log_prefix)
                return

        logger.warning("Unsupported preview image payload type: %s", type(image_data).__name__)

    def cleanup(self) -> None:
        """Clean up resources and unsubscribe from events."""
        if self.event_bus:
            for token in self.event_tokens:
                try:
                    # Extract event name from token if available
                    if hasattr(token, "event"):
                        self.event_bus.off(token.event, token)
                except Exception as e:
                    logger.warning(f"Error unsubscribing: {e}")

            self.event_tokens.clear()
            logger.debug("PreviewPresenter unsubscribed from EventBus")


if TYPE_CHECKING:  # pragma: no cover - typing aid only
    PreviewPresenter.image_changed = cast(
        Signal3[QPixmap, int, int],
        PreviewPresenter.image_changed,
    )  # type: ignore[assignment]
    PreviewPresenter.display_cleared = cast(Signal0, PreviewPresenter.display_cleared)  # type: ignore[assignment]
