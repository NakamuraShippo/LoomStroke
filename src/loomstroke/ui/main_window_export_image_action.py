"""``File > Export...`` action for MainWindow (CT-09 spec §6).

Dialog (format / colour policy / alpha / options / warning preview) ->
destination file chooser filtered to that format -> raster export through
:func:`export_raster` with the document ICC profile resolved by
:class:`ExportColorPolicy`.  PSD / SVG keep delegating to the existing
layered exporters on the window.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np
from numpy.typing import NDArray
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QDialog, QFileDialog, QMessageBox

from ..io.export_color_policy import ExportColorPolicy
from ..io.export_image_request import ExportImageRequest
from ..io.raster_export import export_raster
from .dialogs.export_image_dialog import ExportImageDialog

logger = logging.getLogger(__name__)

#: (format id, dialog filter, default suffix)
_FILTERS: dict[str, tuple[str, str]] = {
    "png": ("PNG Image (*.png)", ".png"),
    "jpeg": ("JPEG Image (*.jpg *.jpeg)", ".jpg"),
    "tiff": ("TIFF Image (*.tif *.tiff)", ".tif"),
    "webp": ("WebP Image (*.webp)", ".webp"),
    "bmp": ("BMP Image (*.bmp)", ".bmp"),
    "psd": ("PSD Image (*.psd)", ".psd"),
    "svg": ("SVG Image (*.svg)", ".svg"),
}


class MainWindowExportImageAction:
    """Run the Export Image dialog and write the active page."""

    def __init__(
        self,
        window: Any,
        *,
        dialog_factory: Callable[..., Any] | None = None,
        file_dialog: Callable[[], Any] | None = None,
        dialog_type: Callable[[], Any] | None = None,
        message_box: Callable[[], Any] | None = None,
        color_policy: ExportColorPolicy | None = None,
        exporter: Callable[..., Any] | None = None,
    ) -> None:
        self._window = window
        self._dialog_factory = dialog_factory or ExportImageDialog
        self._file_dialog = file_dialog or (lambda: QFileDialog)
        self._dialog_type = dialog_type or (lambda: QDialog)
        self._message_box = message_box or (lambda: QMessageBox)
        self._color_policy = color_policy or ExportColorPolicy()
        self._exporter = exporter or export_raster

    def run(self) -> bool:
        """Return ``True`` when a file was written."""
        canvas = self._canvas_service()
        if canvas is None:
            self._warn("Export Unavailable", "Canvas service is not available.")
            return False
        profile = self._document_profile(canvas)
        dialog = self._dialog_factory(
            width=int(getattr(canvas, "_width", 1) or 1),
            height=int(getattr(canvas, "_height", 1) or 1),
            dpi=self._canvas_dpi(canvas)[0],
            document_profile_name=getattr(profile, "name", None) if profile else None,
            background_rgba=self._background_rgba(canvas),
            defaults=self._defaults(),
            parent=self._window,
        )
        if dialog.exec() != self._dialog_type().DialogCode.Accepted:
            return False
        request = dialog.request()
        path = self._choose_path(request.format_id)
        if path is None:
            return False
        return self.export(request, path)

    def export(self, request: ExportImageRequest, path: Path) -> bool:
        """Write ``path`` according to ``request`` (dialog-free entry for tests)."""
        window = self._window
        if request.format_id == "svg":
            return bool(window._export_svg(path))
        if request.format_id == "psd":
            return bool(window._export_psd(path))
        canvas = self._canvas_service()
        pixmap = window._get_composited_pixmap()
        if pixmap is None or canvas is None:
            self._warn("Export Unavailable", "Canvas composite is not available.")
            return False
        rgba = self._pixmap_to_rgba(pixmap)
        profile = self._document_profile(canvas)
        resolved = self._color_policy.resolve(
            request.color_policy,
            document_profile=getattr(profile, "data", None) if profile else None,
        )
        if resolved.transform is not None:
            rgba = resolved.transform(rgba)
        result = self._exporter(
            rgba,
            Path(path),
            request.format_id,
            canvas_dpi=self._canvas_dpi(canvas),
            icc_bytes=resolved.icc_bytes,
            alpha_mode=request.alpha_mode,
            matte_rgba=request.matte_rgba,
            background_rgba=self._background_rgba(canvas),
            encoder_options=dict(request.encoder_options),
        )
        issues = tuple(resolved.issues) + tuple(result.issues)
        if result.status == "failed":
            self._message_box().critical(
                window,
                window._ts("Export Failed"),
                window._ts("Unable to export image: {path}").format(path=path),
            )
            return False
        for issue in issues:
            logger.info("export warning: %s (%s)", issue.code, issue.message)
        message = window._ts("Exported {name}").format(name=Path(path).name)
        if issues:
            message += " (!)"
        window.statusBar().showMessage(message, 3000)
        return True

    # -- helpers ------------------------------------------------------------------

    def _choose_path(self, format_id: str) -> Path | None:
        window = self._window
        filter_text, suffix = _FILTERS.get(format_id, _FILTERS["png"])
        document_path = getattr(window, "_document_path", None)
        default = (
            Path(document_path).with_suffix(suffix) if document_path else Path(f"untitled{suffix}")
        )
        path, _selected = self._file_dialog().getSaveFileName(
            window,
            window._ts("Export Image"),
            str(default),
            window._ts(filter_text),
        )
        if not path:
            return None
        chosen = Path(path)
        if not chosen.suffix:
            chosen = chosen.with_suffix(suffix)
        return chosen

    def _canvas_service(self) -> Any | None:
        host_api = getattr(self._window, "_host_api", None)
        return getattr(host_api, "canvas", None) if host_api else None

    @staticmethod
    def _document_profile(canvas: Any) -> Any | None:
        profile = getattr(canvas, "color_profile", None)
        if profile is None or not getattr(profile, "data", None):
            return None
        return profile

    @staticmethod
    def _canvas_dpi(canvas: Any) -> tuple[float, float]:
        try:
            dpi = float(canvas._dpi)  # noqa: SLF001
            return (dpi, dpi)
        except Exception:
            pass
        try:
            pair = tuple(canvas.config.dpi)
            return (float(pair[0]), float(pair[1]))
        except Exception:
            return (72.0, 72.0)

    @staticmethod
    def _background_rgba(canvas: Any) -> tuple[int, int, int, int]:
        for source in (canvas, getattr(canvas, "config", None)):
            color = getattr(source, "background_color", None)
            if color is None:
                continue
            try:
                r, g, b, a = (int(part) for part in tuple(color))
                return (r, g, b, a)
            except Exception:
                continue
        background = getattr(canvas, "_background", None)
        if background is not None and hasattr(background, "red"):
            return (background.red(), background.green(), background.blue(), background.alpha())
        return (255, 255, 255, 255)

    def _defaults(self) -> dict[str, Any]:
        settings = getattr(self._window, "_settings", None)
        get = getattr(settings, "get", None)
        if not callable(get):
            return {}
        return {
            "jpeg_quality": get("io.jpeg.quality", 95),
            "jpeg_progressive": get("io.jpeg.progressive", False),
            "png_compress_level": get("io.png.compress_level", 6),
            "tiff_compression": get("io.tiff.compression", "tiff_lzw"),
            "webp_quality": get("io.webp.quality", 90),
            "webp_lossless": get("io.webp.lossless", False),
        }

    @staticmethod
    def _pixmap_to_rgba(pixmap: QPixmap) -> NDArray[np.uint8]:
        image = pixmap.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        width, height = image.width(), image.height()
        raw = np.frombuffer(bytes(image.constBits()), dtype=np.uint8)
        rows = raw.reshape(height, image.bytesPerLine())
        return np.ascontiguousarray(rows[:, : width * 4].reshape(height, width, 4))

    def _warn(self, title: str, text: str) -> None:
        self._message_box().warning(self._window, self._window._ts(title), self._window._ts(text))
