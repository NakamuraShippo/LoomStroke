"""Glyph atlas for direct text rendering (retained vector spec §12.3).

Rasterizes glyph coverage with ``QRawFont.alphaMapForGlyph`` into shared
``r8unorm`` atlas pages, keyed by ``(font resource, glyph id, scale
bucket)``.  Pages are append-only (shelf packing, no eviction/repack), so
assigned UV rectangles never move and cached glyph quad geometry survives
atlas growth (spec: atlas updates must not invalidate vector geometry
caches).  Pages are runtime-only resources.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
from PySide6.QtGui import QFont, QFontDatabase, QImage, QRawFont

logger = logging.getLogger(__name__)

PAGE_SIZE = 1024
_PAD = 1  # gutter between glyphs so bilinear sampling never bleeds
# Cap the rasterized glyph size; beyond this the scale bucket is halved so
# decorative headline text cannot exhaust atlas pages.
MAX_GLYPH_RASTER_PX = 512


@dataclass(frozen=True, slots=True)
class GlyphAtlasSlot:
    """Placement of one rasterized glyph inside an atlas page."""

    page_index: int
    x: int
    y: int
    width: int
    height: int
    bearing_x: float  # ink origin relative to the pen, at raster scale
    bearing_y: float
    raster_scale: float  # rasterized at base pixel size * raster_scale


class _AtlasPage:
    def __init__(self, texture: Any, view: Any) -> None:
        self.texture = texture
        self.view = view
        self.shelf_x = _PAD
        self.shelf_y = _PAD
        self.shelf_height = 0

    def allocate(self, width: int, height: int) -> tuple[int, int] | None:
        if width > PAGE_SIZE - 2 * _PAD or height > PAGE_SIZE - 2 * _PAD:
            return None
        if self.shelf_x + width + _PAD > PAGE_SIZE:
            self.shelf_y += self.shelf_height + _PAD
            self.shelf_x = _PAD
            self.shelf_height = 0
        if self.shelf_y + height + _PAD > PAGE_SIZE:
            return None
        position = (self.shelf_x, self.shelf_y)
        self.shelf_x += width + _PAD
        self.shelf_height = max(self.shelf_height, height)
        return position


class GlyphAtlasManager:
    """Owns atlas pages and the per-glyph slot cache."""

    def __init__(self, bundle: Any) -> None:
        self._bundle = bundle
        self._wgpu = bundle.wgpu
        self._device = bundle.device
        self._queue = bundle.queue
        self._pages: list[_AtlasPage] = []
        self._slots: dict[tuple[str, int, int], GlyphAtlasSlot | None] = {}
        self._raw_fonts: dict[tuple[str, int], QRawFont | None] = {}
        self.uploads = 0

    def page_view(self, page_index: int) -> Any:
        return self._pages[page_index].view

    def slot_for_glyph(
        self,
        *,
        font_resource_id: str,
        font_family: str,
        font_style: str,
        base_pixel_size: float,
        glyph_id: int,
        scale_bucket: int,
    ) -> GlyphAtlasSlot | None:
        """Return the atlas slot for a glyph, rasterizing on first use.

        ``None`` means the glyph has no ink (whitespace-like) or could not
        be rasterized; callers skip the quad or fall back.
        """

        bucket = self._effective_bucket(base_pixel_size, scale_bucket)
        key = (font_resource_id, int(glyph_id), bucket)
        if key in self._slots:
            return self._slots[key]
        slot = self._rasterize(
            font_resource_id=font_resource_id,
            font_family=font_family,
            font_style=font_style,
            base_pixel_size=base_pixel_size,
            glyph_id=int(glyph_id),
            bucket=bucket,
        )
        self._slots[key] = slot
        return slot

    @staticmethod
    def _effective_bucket(base_pixel_size: float, scale_bucket: int) -> int:
        bucket = max(1, 1 << max(0, int(scale_bucket)))
        while bucket > 1 and base_pixel_size * bucket > MAX_GLYPH_RASTER_PX:
            bucket //= 2
        return bucket

    def _raw_font(
        self,
        font_resource_id: str,
        font_family: str,
        font_style: str,
        pixel_size: float,
        bucket: int,
    ) -> QRawFont | None:
        cache_key = (font_resource_id, bucket)
        if cache_key in self._raw_fonts:
            return self._raw_fonts[cache_key]
        raw: QRawFont | None
        try:
            # Same face resolution as make_text_font: styled lookup first.
            if font_family and font_style:
                qfont = QFontDatabase.font(font_family, font_style, 12)
                if not qfont.family():
                    qfont = QFont(font_family)
            elif font_family:
                qfont = QFont(font_family)
            else:
                qfont = QFont()
            raw = QRawFont.fromFont(qfont)
            if raw.isValid():
                raw.setPixelSize(float(pixel_size) * bucket)
            else:
                raw = None
        except Exception:
            logger.debug("glyph atlas font resolution failed: %s", font_resource_id, exc_info=True)
            raw = None
        self._raw_fonts[cache_key] = raw
        return raw

    def _rasterize(
        self,
        *,
        font_resource_id: str,
        font_family: str,
        font_style: str,
        base_pixel_size: float,
        glyph_id: int,
        bucket: int,
    ) -> GlyphAtlasSlot | None:
        raw = self._raw_font(font_resource_id, font_family, font_style, base_pixel_size, bucket)
        if raw is None:
            return None
        image = raw.alphaMapForGlyph(glyph_id, QRawFont.AntialiasingType.PixelAntialiasing)
        if image.isNull() or image.width() <= 0 or image.height() <= 0:
            return None
        if image.format() != QImage.Format.Format_Alpha8:
            image = image.convertToFormat(QImage.Format.Format_Alpha8)
        ink = raw.boundingRect(glyph_id)

        width = image.width()
        height = image.height()
        page_index, position = self._allocate(width, height)
        if position is None:
            return None
        data = self._alpha_bytes(image)
        self._queue.write_texture(
            {
                "texture": self._pages[page_index].texture,
                "origin": (position[0], position[1], 0),
            },
            data,
            {"bytes_per_row": width, "rows_per_image": height},
            (width, height, 1),
        )
        self.uploads += 1
        return GlyphAtlasSlot(
            page_index=page_index,
            x=position[0],
            y=position[1],
            width=width,
            height=height,
            bearing_x=float(ink.x()),
            bearing_y=float(ink.y()),
            raster_scale=float(bucket),
        )

    def _allocate(self, width: int, height: int) -> tuple[int, tuple[int, int] | None]:
        if self._pages:
            page_index = len(self._pages) - 1
            position = self._pages[page_index].allocate(width, height)
            if position is not None:
                return page_index, position
        # Current page full (or none yet): open a new one.  Earlier pages
        # keep their content, so existing UVs stay valid (append-only).
        texture = self._device.create_texture(
            size=(PAGE_SIZE, PAGE_SIZE, 1),
            usage=(self._wgpu.TextureUsage.TEXTURE_BINDING | self._wgpu.TextureUsage.COPY_DST),
            format=self._wgpu.TextureFormat.r8unorm,
        )
        page = _AtlasPage(texture, texture.create_view())
        self._pages.append(page)
        position = page.allocate(width, height)
        return len(self._pages) - 1, position

    @staticmethod
    def _alpha_bytes(image: QImage) -> bytes:
        width = image.width()
        height = image.height()
        row_bytes = image.bytesPerLine()
        raw = np.frombuffer(image.constBits(), dtype=np.uint8).reshape(height, row_bytes)
        return raw[:, :width].tobytes()

    def release(self) -> None:
        for page in self._pages:
            destroy = getattr(page.texture, "destroy", None)
            if callable(destroy):
                try:
                    destroy()
                except Exception:
                    logger.debug("glyph atlas page destroy failed", exc_info=True)
        self._pages.clear()
        self._slots.clear()
        self._raw_fonts.clear()


__all__ = ("MAX_GLYPH_RASTER_PX", "PAGE_SIZE", "GlyphAtlasManager", "GlyphAtlasSlot")
