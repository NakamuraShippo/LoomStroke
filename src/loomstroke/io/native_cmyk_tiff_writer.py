"""Atomic, lossless 8/16-bit CMYK TIFF output with an embedded ICC profile."""

from __future__ import annotations

import struct
from pathlib import Path

import numpy as np

from loomstroke.core.native_cmyk.color_transform import CmykColorTransform

from .atomic_export_writer import write_atomically


class NativeCmykTiffWriter:
    @classmethod
    def write(cls, destination, inks, *, profile, dpi):
        CmykColorTransform.validate_profile(profile)
        if inks.dtype not in (np.uint8, np.uint16) or inks.ndim != 3 or inks.shape[2] != 4:
            raise ValueError("CMYK TIFF requires four unsigned 8/16-bit ink planes")
        if min(inks.shape[:2]) < 1 or inks.nbytes > 256 * 1024 * 1024:
            raise ValueError("CMYK TIFF exceeds the pixel memory budget")
        if len(dpi) != 2 or not all(np.isfinite(value) and 0 < value <= 4_000_000 for value in dpi):
            raise ValueError("Invalid TIFF resolution")
        return write_atomically(
            Path(destination), lambda target: cls._encode(target, inks, profile, dpi)
        )

    @staticmethod
    def _encode(destination, inks, profile, dpi):
        height, width = inks.shape[:2]
        bits = inks.dtype.itemsize * 8
        pixel_data = np.ascontiguousarray(inks, dtype="u1" if bits == 8 else "<u2")
        rows = 64
        counts = [min(rows, height - y) * width * 4 * (bits // 8) for y in range(0, height, rows)]
        # IFD payloads are offset from the start of a classic little-endian TIFF.
        entries = {
            256: (4, struct.pack("<I", width)),
            257: (4, struct.pack("<I", height)),
            258: (3, struct.pack("<4H", *([bits] * 4))),
            259: (3, struct.pack("<H", 1)),  # uncompressed
            262: (3, struct.pack("<H", 5)),  # separated CMYK
            273: (4, bytes(4 * len(counts))),
            277: (3, struct.pack("<H", 4)),
            278: (4, struct.pack("<I", rows)),
            279: (4, struct.pack(f"<{len(counts)}I", *counts)),
            282: (5, struct.pack("<2I", round(dpi[0] * 1000), 1000)),
            283: (5, struct.pack("<2I", round(dpi[1] * 1000), 1000)),
            284: (3, struct.pack("<H", 1)),  # chunky
            296: (3, struct.pack("<H", 2)),  # inches
            332: (3, struct.pack("<H", 1)),  # CMYK ink set
            339: (3, struct.pack("<4H", 1, 1, 1, 1)),
            34675: (7, profile),
        }
        sizes = {3: 2, 4: 4, 5: 8, 7: 1}
        payload_offset = 8 + 2 + len(entries) * 12 + 4
        offsets = {}
        for tag, (_kind, payload) in sorted(entries.items()):
            if len(payload) > 4:
                offsets[tag] = payload_offset
                payload_offset += len(payload) + len(payload) % 2
        strips = []
        cursor = payload_offset
        for count in counts:
            strips.append(cursor)
            cursor += count
        entries[273] = (4, struct.pack(f"<{len(strips)}I", *strips))
        with destination.open("wb") as output:
            output.write(struct.pack("<2sHIH", b"II", 42, 8, len(entries)))
            for tag, (kind, payload) in sorted(entries.items()):
                value = (
                    struct.pack("<I", offsets[tag]) if len(payload) > 4 else payload.ljust(4, b"\0")
                )
                output.write(struct.pack("<HHI", tag, kind, len(payload) // sizes[kind]) + value)
            output.write(struct.pack("<I", 0))
            for _tag, (_kind, payload) in sorted(entries.items()):
                if len(payload) > 4:
                    output.write(payload)
                    if len(payload) % 2:
                        output.write(b"\0")
            for y in range(0, height, rows):
                output.write(pixel_data[y : y + rows].tobytes())
