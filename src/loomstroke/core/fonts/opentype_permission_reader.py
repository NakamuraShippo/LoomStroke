"""Safe ``OS/2.fsType`` reader for TrueType / OpenType / TTC (CT-25 F3).

Treats the font as untrusted bytes: size, table count and face count are
bounded, every offset is range-checked, and nothing but the table
directory and the first 10 bytes of ``OS/2`` is ever interpreted.  A
missing or malformed table yields ``unknown`` (never ``installable``).
"""

from __future__ import annotations

import struct
from typing import ClassVar

from .font_permission_assessment import FontPermissionAssessment

__all__ = ["OpenTypePermissionReader"]


class OpenTypePermissionReader:
    MAX_BYTES: ClassVar[int] = 64 * 1024 * 1024
    MAX_TABLES: ClassVar[int] = 512
    MAX_FACES: ClassVar[int] = 64
    _SFNT_VERSIONS: ClassVar[tuple[bytes, ...]] = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1")

    def read(self, data: bytes, *, face_index: int = 0) -> FontPermissionAssessment:
        try:
            fs_type = self._fs_type(data, face_index)
        except ValueError as error:
            return FontPermissionAssessment(evidence_codes=(str(error),))
        if fs_type is None:
            return FontPermissionAssessment(evidence_codes=("os2_table_missing",))
        return self.assess_fs_type(fs_type)

    @staticmethod
    def assess_fs_type(fs_type: int) -> FontPermissionAssessment:
        no_subsetting = bool(fs_type & 0x0100)
        bitmap_only = bool(fs_type & 0x0200)
        low = fs_type & 0x000F
        if low & 0x0008:
            embedding = "editable"
        elif low & 0x0004:
            embedding = "preview_print"
        elif low & 0x0002:
            embedding = "restricted"
        else:
            embedding = "installable"
        evidence = (f"os2_fstype_0x{fs_type:04x}",)
        # fsType alone never *permits* redistribution (spec 25): restricted is a
        # firm "no", everything else stays "unknown" until the user attests.
        package_copy = "denied" if embedding == "restricted" else "unknown"
        return FontPermissionAssessment(
            embedding=embedding,  # type: ignore[arg-type]
            no_subsetting=no_subsetting,
            bitmap_only=bitmap_only,
            package_copy=package_copy,
            evidence_codes=evidence,
        )

    # -- parsing -------------------------------------------------------------------

    def _face_offset(self, data: bytes, face_index: int) -> int:
        num_fonts = struct.unpack(">I", data[8:12])[0]
        if num_fonts < 1 or num_fonts > self.MAX_FACES:
            raise ValueError("font_collection_invalid")
        if face_index < 0 or face_index >= num_fonts:
            raise ValueError("font_face_index_invalid")
        start = 12 + face_index * 4
        if start + 4 > len(data):
            raise ValueError("font_truncated")
        offset = struct.unpack(">I", data[start : start + 4])[0]
        if offset + 12 > len(data):
            raise ValueError("font_offset_invalid")
        return offset

    def _fs_type(self, data: bytes, face_index: int) -> int | None:
        if len(data) > self.MAX_BYTES:
            raise ValueError("font_too_large")
        if len(data) < 12:
            raise ValueError("font_truncated")
        offset = self._face_offset(data, face_index) if data[:4] == b"ttcf" else 0
        if data[offset : offset + 4] not in self._SFNT_VERSIONS:
            raise ValueError("font_untrusted_or_invalid")
        num_tables = struct.unpack(">H", data[offset + 4 : offset + 6])[0]
        if num_tables < 1 or num_tables > self.MAX_TABLES:
            raise ValueError("font_table_count_invalid")
        directory = offset + 12
        for index in range(num_tables):
            record = directory + index * 16
            if record + 16 > len(data):
                raise ValueError("font_truncated")
            tag = data[record : record + 4]
            table_offset, table_length = struct.unpack(">II", data[record + 8 : record + 16])
            if tag != b"OS/2":
                continue
            if table_length < 10 or table_offset + 10 > len(data):
                raise ValueError("font_os2_invalid")
            return struct.unpack(">H", data[table_offset + 8 : table_offset + 10])[0]
        return None
