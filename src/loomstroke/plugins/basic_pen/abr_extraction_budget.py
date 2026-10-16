"""Optional ABR expansion budgets and allocation-free v6 sample preflight."""

from __future__ import annotations

import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class ABRExtractionBudget:
    max_tip_pixels: int = 4_194_304
    max_total_pixels: int = 67_108_864
    max_tips: int = 128

    def __post_init__(self):
        for value in (self.max_tip_pixels, self.max_total_pixels, self.max_tips):
            if type(value) is not int or value <= 0:
                raise ValueError("ABR extraction limits must be positive integers.")


class ExtractionBudgetState:
    def __init__(self, limits: ABRExtractionBudget):
        self.limits = limits
        self.count = 0
        self.pixels = 0

    def reserve(self, width: int, height: int):
        pixels = width * height
        if pixels > self.limits.max_tip_pixels:
            raise ValueError(f"ABR tip pixels exceed {self.limits.max_tip_pixels}.")
        if self.pixels + pixels > self.limits.max_total_pixels:
            raise ValueError(f"ABR total pixels exceed {self.limits.max_total_pixels}.")
        if self.count >= self.limits.max_tips:
            raise ValueError(f"ABR tip count exceeds {self.limits.max_tips}.")
        self.pixels += pixels
        self.count += 1


def v6_sample_candidate(record: bytes):
    """Use encoded ends for the existing tail/area score without decoding images."""

    best = None
    best_score = None
    for offset in range(max(0, len(record) - 18)):
        top, left, bottom, right, depth, compression = struct.unpack_from(">IIIIHB", record, offset)
        width, height = right - left, bottom - top
        if (
            not 0 < width <= 4096
            or not 0 < height <= 4096
            or depth not in (8, 16)
            or compression not in (0, 1)
        ):
            continue
        start = offset + 19
        if compression == 0:
            end = start + width * height * (depth // 8)
        else:
            table_end = start + height * 2
            if table_end > len(record):
                continue
            encoded_size = sum(
                struct.unpack_from(">H", record, start + row * 2)[0] for row in range(height)
            )
            end = table_end + encoded_size
        if end > len(record):
            continue
        score = (len(record) - end, -(width * height))
        if best_score is None or score < best_score:
            best_score = score
            best = (start, width, height, depth, compression)
    return best


def decode_packbits_row(encoded: bytes, row_bytes: int) -> bytes:
    """Consume all tokens while retaining only the requested row width."""

    output = bytearray()
    cursor = 0
    while cursor < len(encoded):
        token = encoded[cursor]
        cursor += 1
        token = token if token < 128 else token - 256
        if token == -128:
            continue
        count = token + 1 if token >= 0 else 1 - token
        length = count if token >= 0 else 1
        if cursor + length > len(encoded):
            raise ValueError("Truncated ABR PackBits row.")
        retained = max(0, min(count, row_bytes - len(output)))
        if token >= 0:
            output.extend(encoded[cursor : cursor + retained])
        else:
            output.extend(encoded[cursor : cursor + 1] * retained)
        cursor += length
    return bytes(output).ljust(row_bytes, b"\x00")
