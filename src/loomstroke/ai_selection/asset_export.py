"""Transparent PNG export helpers for SAM3 game-asset workflows.

Pure image/file helpers behind the manual export actions of the SAM3 AI
Select tool. Coordinates are input-image local throughout: the masked layer
images produced by :mod:`cut_to_layer_adapter` are layer-sized and
layer-local, so the trim bbox never leaves this module.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .mask_refine_adapter import _nonzero_bbox

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

MAX_EXPORT_PADDING_PX = 64
SHEET_SPACING_PX = 2
SHEET_MAX_SIDE_PX = 8192

_FORBIDDEN_NAME_CHARS = ("/", "\\", ":", "\0")


class Sam3AssetExportError(ValueError):
    """Raised when an asset export step fails with a stable reason code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class Sam3AssetExportWriteError(Sam3AssetExportError):
    """Write failure carrying how many files were completed before it."""

    def __init__(self, code: str, message: str, *, written: int) -> None:
        super().__init__(code, message)
        self.written = written


@dataclass(slots=True)
class AssetExportBatchResult:
    """Path-free summary of a batch export (event payloads stay count-only)."""

    written: int = 0


def trim_image_to_alpha_bbox(
    image: QImage,
    *,
    padding_px: int = 0,
) -> tuple[QImage, tuple[int, int, int, int]] | None:
    """Crop to the minimal non-zero-alpha bbox, expanded by ``padding_px``.

    The bbox is in input-image local coordinates and the padding is clamped
    to the input image rect (the masked pixels are already alpha-multiplied,
    so padded pixels are fully transparent). Returns None when the image is
    empty or fully transparent.
    """

    import numpy as np  # noqa: PLC0415
    from PySide6.QtGui import QImage  # noqa: PLC0415

    if image is None or image.isNull() or image.width() <= 0 or image.height() <= 0:
        return None
    premultiplied = image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    alpha = _qimage_channel(premultiplied, channel=3, np=np)
    bbox = _nonzero_bbox(alpha, np)
    if bbox is None:
        return None

    padding = max(0, min(MAX_EXPORT_PADDING_PX, int(padding_px)))
    x, y, width, height = bbox
    left = max(0, x - padding)
    top = max(0, y - padding)
    right = min(int(premultiplied.width()), x + width + padding)
    bottom = min(int(premultiplied.height()), y + height + padding)
    padded_bbox = (left, top, right - left, bottom - top)
    return premultiplied.copy(left, top, right - left, bottom - top), padded_bbox


def encode_transparent_png(image: QImage) -> bytes:
    """Encode as a straight-alpha transparent PNG via Format_RGBA8888."""

    return encode_sprite_sheet_image(image, image_format="png")


def encode_sprite_sheet_image(image: QImage, *, image_format: str = "png") -> bytes:
    """Encode a sprite sheet as a straight-alpha transparent PNG or WebP.

    WebP uses quality 100 (lossless for alpha>0 pixels; libwebp does not
    preserve the RGB of fully transparent pixels, which is harmless here since
    the sheet fills its gaps with (0,0,0,0)).
    """

    from PySide6.QtCore import QBuffer, QIODevice  # noqa: PLC0415
    from PySide6.QtGui import QImage  # noqa: PLC0415

    fmt = str(image_format).lower()
    qt_format = "WEBP" if fmt == "webp" else "PNG"
    quality = 100 if fmt == "webp" else -1
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    if not converted.save(buffer, qt_format, quality):
        raise Sam3AssetExportError(
            "asset_export_write_failed",
            f"SAM3 asset export {qt_format} encoding failed.",
        )
    return bytes(buffer.data())


def next_power_of_two(value: int) -> int:
    """Smallest power of two >= value; 1 for value <= 1."""

    n = int(value)
    if n <= 1:
        return 1
    return 1 << (n - 1).bit_length()


def build_selection_export_image(
    source_image: QImage,
    selection_array: Any,
    *,
    selection_bounds: tuple[int, int, int, int],
    layer_offset: tuple[int, int],
) -> QImage | None:
    """Multiply active-layer pixels by the canvas selection alpha.

    ``selection_array`` is the canvas-sized uint8 alpha (selections cannot
    extend past the canvas). The layer rect (offset + size) is intersected
    with ``selection_bounds`` in canvas coordinates; both are cropped to the
    intersection and multiplied in layer-local space with the same
    ``(source * alpha + 127) // 255`` rounding as the cut-to-layer adapter.
    Returns None when the intersection is empty.
    """

    import numpy as np  # noqa: PLC0415
    from PySide6.QtGui import QImage  # noqa: PLC0415

    if source_image is None or source_image.isNull():
        return None
    layer_width = int(source_image.width())
    layer_height = int(source_image.height())
    if layer_width <= 0 or layer_height <= 0:
        return None

    offset_x, offset_y = int(layer_offset[0]), int(layer_offset[1])
    sel_x, sel_y, sel_width, sel_height = (int(value) for value in selection_bounds)
    canvas_height, canvas_width = selection_array.shape

    # Intersect the layer rect with the selection bounds in canvas space,
    # clamped to the canvas (the selection array's coordinate system).
    left = max(offset_x, sel_x, 0)
    top = max(offset_y, sel_y, 0)
    right = min(offset_x + layer_width, sel_x + sel_width, canvas_width)
    bottom = min(offset_y + layer_height, sel_y + sel_height, canvas_height)
    if right <= left or bottom <= top:
        return None

    alpha = np.zeros((layer_height, layer_width), dtype=np.uint8)
    alpha[top - offset_y : bottom - offset_y, left - offset_x : right - offset_x] = selection_array[
        top:bottom, left:right
    ]
    if not bool(alpha.any()):
        return None

    premultiplied = source_image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
    source_data = _qimage_bgra(premultiplied, np=np)
    masked = (
        (source_data.astype(np.uint16) * alpha[:, :, None].astype(np.uint16) + 127) // 255
    ).astype(np.uint8)
    return QImage(
        masked.data,
        layer_width,
        layer_height,
        layer_width * 4,
        QImage.Format.Format_ARGB32_Premultiplied,
    ).copy()


def sheet_max_side_px() -> int:
    """Call-time accessor so tests can monkeypatch SHEET_MAX_SIDE_PX."""

    return SHEET_MAX_SIDE_PX


@dataclass(slots=True)
class SpriteSheetFrame:
    """One trimmed sprite destined for a sheet (premultiplied pixels)."""

    name: str
    image: Any  # QImage, ARGB32_Premultiplied
    trim_bbox: tuple[int, int, int, int]  # layer-local, padding included
    source_size: tuple[int, int]  # masked (= layer) size before trimming


@dataclass(slots=True)
class SpriteSheetLayout:
    """Deterministic placements (sheet coordinates, margin included)."""

    frames: list[tuple[SpriteSheetFrame, int, int]]
    sheet_size: tuple[int, int]


def pack_sprite_sheet(
    frames: list[SpriteSheetFrame],
    *,
    spacing_px: int | None = None,
    max_side_px: int | None = None,
    power_of_two: bool = False,
) -> SpriteSheetLayout:
    """Deterministic shelf packing: sort by (-height, input order), fill rows
    up to a content target width, shrink-to-fit the final sheet, keep a
    ``spacing_px`` gap between frames and as the outer margin. ``power_of_two``
    rounds the final sheet dimensions up to the next power of two (frame
    coordinates are unchanged; only bottom-right transparent padding grows)."""

    import math  # noqa: PLC0415

    spacing = SHEET_SPACING_PX if spacing_px is None else int(spacing_px)
    max_side = SHEET_MAX_SIDE_PX if max_side_px is None else int(max_side_px)
    if not frames:
        raise Sam3AssetExportError(
            "asset_export_empty",
            "SAM3 sprite sheet export has no frames.",
        )

    order = sorted(range(len(frames)), key=lambda i: (-int(frames[i].image.height()), i))
    total_area = sum(
        (int(frame.image.width()) + spacing) * (int(frame.image.height()) + spacing)
        for frame in frames
    )
    widest = max(int(frame.image.width()) for frame in frames)
    root = math.isqrt(max(1, total_area))
    if root * root < total_area:
        root += 1
    target_content_width = max(widest, root)

    placements: list[tuple[SpriteSheetFrame, int, int]] = []
    x = 0
    y = 0
    row_height = 0
    used_width = 0
    for index in order:
        frame = frames[index]
        width = int(frame.image.width())
        height = int(frame.image.height())
        if x > 0 and x + width > target_content_width:
            y += row_height + spacing
            x = 0
            row_height = 0
        placements.append((frame, spacing + x, spacing + y))
        x += width + spacing
        row_height = max(row_height, height)
        used_width = max(used_width, x - spacing)

    sheet_width = used_width + 2 * spacing
    sheet_height = y + row_height + 2 * spacing
    if power_of_two:
        sheet_width = next_power_of_two(sheet_width)
        sheet_height = next_power_of_two(sheet_height)
    if sheet_width > max_side or sheet_height > max_side:
        raise Sam3AssetExportError(
            "asset_export_sheet_too_large",
            "SAM3 sprite sheet exceeds the maximum texture size.",
        )
    return SpriteSheetLayout(frames=placements, sheet_size=(sheet_width, sheet_height))


def compose_sprite_sheet_image(layout: SpriteSheetLayout) -> Any:
    from PySide6.QtGui import QImage, QPainter  # noqa: PLC0415

    width, height = layout.sheet_size
    sheet = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    sheet.fill(0)
    painter = QPainter(sheet)
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
    for frame, x, y in layout.frames:
        painter.drawImage(x, y, frame.image)
    painter.end()
    return sheet


def build_sprite_atlas_payload(layout: SpriteSheetLayout, *, image_name: str) -> dict[str, Any]:
    """TexturePacker JSON-hash compatible atlas (frames in index order)."""

    frames: dict[str, Any] = {}
    for frame, x, y in sorted(layout.frames, key=lambda item: item[0].name):
        width = int(frame.image.width())
        height = int(frame.image.height())
        frames[frame.name] = {
            "frame": {"x": x, "y": y, "w": width, "h": height},
            "rotated": False,
            "trimmed": True,
            "spriteSourceSize": {
                "x": frame.trim_bbox[0],
                "y": frame.trim_bbox[1],
                "w": width,
                "h": height,
            },
            "sourceSize": {"w": frame.source_size[0], "h": frame.source_size[1]},
        }
    return {
        "frames": frames,
        "meta": {
            "app": "LoomStroke",
            "version": "1.0",
            "image": str(image_name),
            "format": "RGBA8888",
            "size": {"w": layout.sheet_size[0], "h": layout.sheet_size[1]},
            "scale": "1",
        },
    }


def write_export_file(path: Path, data: bytes) -> None:
    """Atomically write a single user-approved target path (overwrite OK)."""

    target = Path(path)
    if not target.parent.is_dir():
        raise Sam3AssetExportError(
            "asset_export_target_missing",
            "SAM3 asset export target directory does not exist.",
        )
    try:
        _atomic_write_bytes(target, data)
    except OSError as exc:
        raise Sam3AssetExportWriteError(
            "asset_export_write_failed",
            f"SAM3 asset export write failed: {type(exc).__name__}",
            written=0,
        ) from exc


def write_export_batch(
    output_dir: Path,
    files: Iterable[tuple[str, bytes]],
) -> AssetExportBatchResult:
    """Write files into a user-chosen directory without silent overwrites.

    ``files`` may be a generator: entries are consumed, encoded upstream, and
    written one at a time (peak memory stays at one sprite). Name collisions
    get a ``_2`` style suffix. On an I/O failure the batch stops, files
    already written stay on disk, and the raised error carries the count.
    """

    root = Path(output_dir)
    if not root.is_dir():
        raise Sam3AssetExportError(
            "asset_export_target_missing",
            "SAM3 asset export target directory does not exist.",
        )
    written = 0
    for name, data in files:
        target = root / _validated_export_name(name)
        target = _collision_free_target(target)
        try:
            _atomic_write_bytes(target, data)
        except OSError as exc:
            raise Sam3AssetExportWriteError(
                "asset_export_write_failed",
                f"SAM3 asset export write failed: {type(exc).__name__}",
                written=written,
            ) from exc
        written += 1
    return AssetExportBatchResult(written=written)


def _validated_export_name(name: str) -> str:
    cleaned = str(name)
    if (
        not cleaned
        or cleaned != cleaned.strip()
        or ".." in cleaned
        or cleaned.startswith(".")
        or any(char in cleaned for char in _FORBIDDEN_NAME_CHARS)
    ):
        raise Sam3AssetExportError(
            "asset_export_write_failed",
            "SAM3 asset export file name is not allowed.",
        )
    return cleaned


def _collision_free_target(target: Path) -> Path:
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for counter in range(2, 1000):
        candidate = target.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
    raise Sam3AssetExportError(
        "asset_export_write_failed",
        "SAM3 asset export could not find a collision-free file name.",
    )


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    handle, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(tmp_path), str(target))
    except OSError:
        tmp_path.unlink(missing_ok=True)
        raise


def _qimage_channel(image: QImage, *, channel: int, np: Any) -> Any:
    # Copy only the requested channel (1/4 of the BGRA buffer): trimming an
    # 8K layer must not allocate a second full-frame copy just for alpha.
    return _qimage_bgra_view(image, np=np)[:, :, channel].copy()


def _qimage_bgra(image: QImage, *, np: Any) -> Any:
    return _qimage_bgra_view(image, np=np).copy()


def _qimage_bgra_view(image: QImage, *, np: Any) -> Any:
    width = int(image.width())
    height = int(image.height())
    # bits() can detach (e.g. images wrapping an external const buffer) and
    # repack the pixel data, changing bytesPerLine — read the stride only
    # after the detach has happened.
    bits = image.bits()
    bytes_per_line = int(image.bytesPerLine())
    byte_count = bytes_per_line * height
    if hasattr(bits, "setsize"):
        bits.setsize(byte_count)
    data = np.frombuffer(bits, dtype=np.uint8, count=byte_count)
    rows = data.reshape(height, bytes_per_line)
    return rows[:, : width * 4].reshape(height, width, 4)


__all__ = [
    "MAX_EXPORT_PADDING_PX",
    "SHEET_MAX_SIDE_PX",
    "SHEET_SPACING_PX",
    "AssetExportBatchResult",
    "Sam3AssetExportError",
    "Sam3AssetExportWriteError",
    "SpriteSheetFrame",
    "SpriteSheetLayout",
    "build_selection_export_image",
    "build_sprite_atlas_payload",
    "compose_sprite_sheet_image",
    "encode_sprite_sheet_image",
    "encode_transparent_png",
    "next_power_of_two",
    "pack_sprite_sheet",
    "sheet_max_side_px",
    "trim_image_to_alpha_bbox",
    "write_export_batch",
    "write_export_file",
]
