"""``page`` source: composite of an explicit page (active page or a stored page snapshot)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

__all__ = ["PageSourceResolver"]

RenderPage = Callable[[Any, Any], Any]
"""``(document_metadata, page_snapshot) -> RGBA ndarray`` (same contract as export)."""


class PageSourceResolver:
    def __init__(
        self, error_type: type[Exception], *, render_page: RenderPage | None = None
    ) -> None:
        self._error = error_type
        self._render_page = render_page

    def active_page_id(self, host_api: object) -> str | None:
        pages = getattr(host_api, "pages", None)
        getter = getattr(pages, "get_active_page_id", None)
        if not callable(getter):
            return None
        try:
            value = getter()
        except Exception:
            return None
        return str(value) if value else None

    def resolve(
        self, host_api: object, page_id: str | None, composite_resolver: Callable[[object], Any]
    ) -> tuple[Any, str | None]:
        """Return ``(qimage, page_id)``; the active page uses the live composite."""
        active = self.active_page_id(host_api)
        target = str(page_id).strip() if page_id else active
        if not target:
            raise self._error("page_missing", "SAM3 page scope needs a page id.")
        canvas = getattr(host_api, "canvas", None)
        if target == active or active is None:
            return composite_resolver(canvas), target
        return self._render_snapshot(host_api, target), target

    def _render_snapshot(self, host_api: object, page_id: str) -> Any:
        pages = getattr(host_api, "pages", None)
        getter = getattr(pages, "get_page_snapshot", None)
        if not callable(getter):
            raise self._error("page_snapshot_unavailable", "SAM3 page scope: no page snapshot API.")
        try:
            snapshot = getter(page_id)
        except Exception as exc:
            raise self._error(
                "page_snapshot_unavailable", "SAM3 page scope: snapshot failed."
            ) from exc
        if snapshot is None:
            raise self._error("page_missing", "SAM3 page scope: page not found.")
        render = self._render_page or self._default_render_page()
        metadata = self._document_metadata(host_api)
        try:
            rgba = render(metadata, snapshot)
        except Exception as exc:
            raise self._error("page_render_failed", "SAM3 page scope: page render failed.") from exc
        return self._qimage_from_rgba(rgba)

    @staticmethod
    def _default_render_page() -> RenderPage:
        from loomstroke.ui.actions.page_renderer import PageRenderer  # noqa: PLC0415

        renderer = PageRenderer()

        def render(metadata: Any, page: Any) -> Any:
            try:
                return renderer.render(metadata, page)
            finally:
                renderer.release()

        return render

    @staticmethod
    def _document_metadata(host_api: object) -> Any:
        from loomstroke.io.layer_io import DocumentMetadata  # noqa: PLC0415

        canvas = getattr(host_api, "canvas", None)
        return DocumentMetadata(
            width=int(getattr(canvas, "_width", 1) or 1),
            height=int(getattr(canvas, "_height", 1) or 1),
        )

    @staticmethod
    def _qimage_from_rgba(rgba: Any) -> Any:
        import numpy as np  # noqa: PLC0415
        from PySide6.QtGui import QImage  # noqa: PLC0415

        array = np.ascontiguousarray(np.asarray(rgba).astype(np.uint8, copy=False))
        height, width = array.shape[:2]
        image = QImage(array.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
        return image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
