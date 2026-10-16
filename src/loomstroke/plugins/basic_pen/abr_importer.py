from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .abr_extraction_budget import (
    ABRExtractionBudget,
    ExtractionBudgetState,
    decode_packbits_row,
    v6_sample_candidate,
)

logger = logging.getLogger(__name__)


class ABRParseError(RuntimeError):
    """Raised when ABR parsing fails or encounters unsupported data."""


@dataclass(frozen=True)
class _BrushTip:
    width: int
    height: int
    pixels: bytes  # 8-bit grayscale, 0-255


class _BinaryReader:
    def __init__(self, data: bytes) -> None:
        self._data = memoryview(data)
        self._pos = 0
        self._size = len(data)

    def tell(self) -> int:
        return self._pos

    def seek(self, pos: int) -> None:
        if pos < 0 or pos > self._size:
            raise ABRParseError(f"Seek out of range: {pos}")
        self._pos = pos

    def skip(self, amount: int) -> None:
        self.seek(self._pos + amount)

    def remaining(self) -> int:
        return self._size - self._pos

    def read_bytes(self, length: int) -> bytes:
        if length < 0 or self._pos + length > self._size:
            raise ABRParseError("Unexpected end of ABR data")
        chunk = self._data[self._pos : self._pos + length].tobytes()
        self._pos += length
        return chunk

    def _read_struct(self, fmt: str, size: int) -> int:
        if self._pos + size > self._size:
            raise ABRParseError("Unexpected end of ABR data")
        value = struct.unpack_from(fmt, self._data, self._pos)[0]
        self._pos += size
        return int(value)

    def read_u8(self) -> int:
        return self._read_struct(">B", 1)

    def read_i8(self) -> int:
        return self._read_struct(">b", 1)

    def read_u16(self) -> int:
        return self._read_struct(">H", 2)

    def read_u32(self) -> int:
        return self._read_struct(">I", 4)


FOURCC_8BIM = int.from_bytes(b"8BIM", "big")
FOURCC_SAMP = int.from_bytes(b"samp", "big")


def _reach_8bim_section(reader: _BinaryReader, tagname: int) -> bool:
    while reader.remaining() >= 8:
        tag = reader.read_u32()
        name = reader.read_u32()
        if tag != FOURCC_8BIM:
            return False
        if name == tagname:
            return True
        section_size = reader.read_u32()
        if section_size % 2:
            section_size += 1
        reader.skip(section_size)
    return False


def _decode_packbits(reader: _BinaryReader, encoded_length: int) -> bytes:
    if encoded_length <= 0:
        return b""
    out = bytearray()
    read_bytes = 0
    while read_bytes < encoded_length:
        n = reader.read_i8()
        read_bytes += 1
        if 0 <= n <= 127:
            count = n + 1
            out.extend(reader.read_bytes(count))
            read_bytes += count
        elif -127 <= n <= -1:
            count = 1 - n
            value = reader.read_bytes(1)
            read_bytes += 1
            out.extend(value * count)
        else:
            # -128 is a no-op in PackBits
            continue
    return bytes(out)


def _read_brush_pixels(
    reader: _BinaryReader,
    width: int,
    height: int,
    depth: int,
    compression: int,
    *,
    bounded: bool = False,
) -> bytes:
    bytes_per_pixel = depth // 8
    total_bytes = width * height * bytes_per_pixel
    if total_bytes <= 0:
        raise ABRParseError("Invalid brush dimensions")
    if compression == 0:
        return reader.read_bytes(total_bytes)
    if compression != 1:
        raise ABRParseError(f"Unsupported ABR compression: {compression}")

    row_lengths = [reader.read_u16() for _ in range(height)]
    row_bytes = width * bytes_per_pixel
    output = bytearray(total_bytes)
    offset = 0
    for row_length in row_lengths:
        if bounded:
            try:
                decoded = decode_packbits_row(reader.read_bytes(row_length), row_bytes)
            except ValueError as exc:
                raise ABRParseError(str(exc)) from exc
        else:
            decoded = _decode_packbits(reader, row_length)
        if len(decoded) < row_bytes:
            decoded = decoded.ljust(row_bytes, b"\x00")
        output[offset : offset + row_bytes] = decoded[:row_bytes]
        offset += row_bytes
    return bytes(output)


def _normalize_depth(pixels: bytes, depth: int) -> bytes:
    if depth == 8:
        return pixels
    if depth == 16:
        return pixels[0::2]
    raise ABRParseError(f"Unsupported ABR bit depth: {depth}")


def _invert_pixels(pixels: bytes) -> bytes:
    return bytes((255 - value) for value in pixels)


def _should_invert(pixels: bytes) -> bool:
    total = len(pixels)
    if total == 0:
        return False
    # Sample to keep cost bounded for large tips.
    step = max(1, total // 200000)
    count = 0
    total_value = 0
    bright = 0
    dark = 0
    for idx in range(0, total, step):
        value = pixels[idx]
        total_value += value
        count += 1
        if value >= 220:
            bright += 1
        elif value <= 35:
            dark += 1
    if count == 0:
        return False
    mean = total_value / count
    if mean >= 160:
        return True
    return bool(bright > dark * 2 and mean >= 120)


def _maybe_invert_pixels(pixels: bytes) -> bytes:
    return _invert_pixels(pixels) if _should_invert(pixels) else pixels


def _reserve_tip(budget: ExtractionBudgetState, width: int, height: int, depth: int):
    if depth not in (8, 16):
        raise ABRParseError(f"Unsupported ABR bit depth: {depth}")
    try:
        budget.reserve(width, height)
    except ValueError as exc:
        raise ABRParseError(str(exc)) from exc


def _read_brush(
    reader: _BinaryReader, budget: ExtractionBudgetState | None = None
) -> _BrushTip | None:
    brush_type = reader.read_u16()
    brush_size = reader.read_u32()
    brush_start = reader.tell()
    if brush_size <= 0:
        raise ABRParseError("Invalid brush size")

    if brush_type != 2:
        reader.seek(brush_start + brush_size)
        return None

    reader.skip(8)  # Unknown header fields
    top = reader.read_u32()
    left = reader.read_u32()
    bottom = reader.read_u32()
    right = reader.read_u32()
    depth = reader.read_u16()
    compression = reader.read_u8()

    width = int(right - left)
    height = int(bottom - top)
    if width <= 0 or height <= 0:
        reader.seek(brush_start + brush_size)
        return None

    if budget is not None:
        _reserve_tip(budget, width, height, depth)
    pixels = _read_brush_pixels(
        reader, width, height, depth, compression, bounded=budget is not None
    )
    pixels = _normalize_depth(pixels, depth)
    pixels = _maybe_invert_pixels(pixels)

    reader.seek(brush_start + brush_size)
    return _BrushTip(width=width, height=height, pixels=pixels)


def _extract_tip_v6_record(
    record: bytes, budget: ExtractionBudgetState | None = None
) -> _BrushTip | None:
    if budget is not None:
        candidate = v6_sample_candidate(record)
        if candidate is None:
            return None
        start, width, height, depth, compression = candidate
        _reserve_tip(budget, width, height, depth)
        reader = _BinaryReader(record)
        reader.seek(start)
        pixels = _read_brush_pixels(reader, width, height, depth, compression, bounded=True)
        pixels = _maybe_invert_pixels(_normalize_depth(pixels, depth))
        return _BrushTip(width=width, height=height, pixels=pixels)
    max_dim = 4096
    best_score: tuple[int, int] | None = None
    best_tip: _BrushTip | None = None
    for offset in range(0, len(record) - 19):
        top, left, bottom, right = struct.unpack_from(">IIII", record, offset)
        width = int(right - left)
        height = int(bottom - top)
        if width <= 0 or height <= 0 or width > max_dim or height > max_dim:
            continue
        if width * height > max_dim * max_dim:
            continue
        depth = struct.unpack_from(">H", record, offset + 16)[0]
        compression = record[offset + 18]
        if depth not in (8, 16) or compression not in (0, 1):
            continue
        pixel_start = offset + 19
        if compression == 1 and pixel_start + height * 2 > len(record):
            continue
        reader = _BinaryReader(record)
        try:
            reader.seek(pixel_start)
            pixels = _read_brush_pixels(reader, width, height, depth, compression)
            pixels = _normalize_depth(pixels, depth)
        except ABRParseError:
            continue
        pixels = _maybe_invert_pixels(pixels)
        tail = len(record) - reader.tell()
        area = width * height
        score = (tail, -area)
        if best_score is None or score < best_score:
            best_score = score
            best_tip = _BrushTip(width=width, height=height, pixels=pixels)
    return best_tip


class ABRImporter:
    """Extract brush tip PNGs from Photoshop ABR files (v1/v2/v6)."""

    def extract_tips(  # noqa: PLR0912
        self, abr_file: str, out_dir: str, *, budget: ABRExtractionBudget | None = None
    ) -> list[str]:
        abr_path = Path(abr_file)
        if not abr_path.exists():
            raise ABRParseError(f"ABR file not found: {abr_file}")

        data = abr_path.read_bytes()
        reader = _BinaryReader(data)
        version = reader.read_u16()
        tips: list[_BrushTip] = []
        budget_state = ExtractionBudgetState(budget) if budget is not None else None

        if version in (1, 2):
            count = reader.read_u16()
            for _ in range(count):
                brush = _read_brush(reader, budget_state)
                if brush is not None:
                    tips.append(brush)
        elif version == 6:
            _ = reader.read_u16()  # subversion, unused
            if not _reach_8bim_section(reader, FOURCC_SAMP):
                raise ABRParseError("ABR v6 sample section not found")
            section_size = reader.read_u32()
            data_start = reader.tell()
            data_end = data_start + section_size
            if data_end > reader.tell() + reader.remaining():
                raise ABRParseError("ABR sample section out of bounds")
            while reader.tell() + 4 <= data_end:
                brush_size = reader.read_u32()
                if brush_size <= 0:
                    break
                if reader.tell() + brush_size > data_end:
                    raise ABRParseError("ABR brush record out of bounds")
                record = reader.read_bytes(brush_size)
                brush = _extract_tip_v6_record(record, budget_state)
                if brush is not None:
                    tips.append(brush)
                padding = (-brush_size) % 4
                if padding:
                    if reader.tell() + padding > data_end:
                        break
                    reader.skip(padding)
            reader.seek(data_end)
        else:
            raise ABRParseError(f"Unsupported ABR version: {version}")

        if not tips:
            return []

        output_dir = self.ensure_output_dir(out_dir)
        output_paths: list[str] = []
        for idx, tip in enumerate(tips):
            filename = f"tip_{idx:03d}.png"
            target = output_dir / filename
            image = Image.frombytes("L", (tip.width, tip.height), tip.pixels)
            image.save(target)
            output_paths.append(str(target))
        logger.info("Extracted %d brush tips from %s", len(output_paths), abr_file)
        return output_paths

    def ensure_output_dir(self, out_dir: str) -> Path:
        path = Path(out_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path
