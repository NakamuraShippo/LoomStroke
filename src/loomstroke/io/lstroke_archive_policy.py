"""Content-aware ZIP member policy for ``.lstroke`` archive validation.

``LayerIO`` historically enforced one flat compression-ratio limit (200x)
for every archive member.  That limit is correct for manifest/JSON/GLB and
unknown members, but past LoomStroke versions stored already-compressed
layer PNGs with ``ZIP_DEFLATED``: a mostly-uniform 4K layer PNG legitimately
re-deflates at 200-1000x, so the flat limit rejected files this app saved
itself.  Raising the flat limit instead would have let a Deflate bomb hide
in any member (including a multi-GB ``manifest.json`` that
``archive.read()`` materializes in RAM before any other check).

This policy classifies members instead:

- **Default members** (``manifest.json``, JSON, GLB, unknown paths) keep the
  legacy 200x ratio limit.  ``manifest.json`` additionally gets a dedicated
  uncompressed-size cap that is checked against ``ZipInfo.file_size``
  *before* the manifest is read, plus a bounded read helper that never
  materializes more than the cap + 1 bytes.
- **LoomStroke image members** may exceed 200x only as a narrow
  backward-compatibility allowance for deflated PNGs written by past saves,
  and only when every condition holds: a path the LoomStroke save layout
  actually generates, ``ZIP_DEFLATED`` (the only method past saves used),
  encoded size within the layer-image byte cap, a verified PNG signature +
  IHDR whose dimensions respect the pixel/byte caps, a cumulative decoded
  estimate within the document decode budget, and a hard 1100x ceiling
  (Deflate's theoretical maximum is ~1032x).

Manifest size-cap evidence (``MAX_MANIFEST_BYTES`` = 64 MiB): a real 4K
document with raster + vector + text layers produced a 10,091-byte
manifest; serialized vector anchors measure ~86 bytes each, so 64 MiB
covers ~700k anchors plus an embedded base64 ICC profile — orders of
magnitude above any observed document while bounding what a hostile
archive can force into memory.
"""

from __future__ import annotations

import re
import zipfile

MANIFEST_MEMBER = "manifest.json"

# Legacy flat limit, kept for every non-image member.
DEFAULT_MAX_COMPRESSION_RATIO = 200
# Backward-compat ceiling for verified LoomStroke PNG members only.
# Deflate cannot exceed ~1032:1, so nothing legitimate is ever rejected.
IMAGE_MAX_COMPRESSION_RATIO = 1100
# Dedicated uncompressed cap for manifest.json (see module docstring).
MAX_MANIFEST_BYTES = 64 * 1024 * 1024

# Image member paths the LoomStroke save layout actually generates:
# - doc_thumb.png at the archive root
# - pages/<id>/page_thumb.png
# - pages/<id>/layers/{layer,mask,effect_mask}_<idx>.png
# - pages/<id>/thumbnails/thumb_<idx>.png
# - the same layers/... and thumbnails/... paths at the root for legacy
#   v1.0 single-page documents (loader migrates them to pages)
# Page ids are normalized to [A-Za-z0-9_-]{1,64} by LayerIO.
# Index width: the save naming is ``{idx:03d}`` — MINIMUM three digits, so
# documents with 1000+ layers legitimately produce 4-digit names like
# ``layer_1000.png``.  Five digits would need index >= 10000, which is
# impossible under _MAX_ARCHIVE_MEMBERS = 10,000 (every layer contributes
# at least an image + thumbnail member, plus manifest/doc_thumb), so
# \d{3,4} covers everything a real save can write.
_IMAGE_MEMBER_RE = re.compile(
    r"(?:doc_thumb\.png"
    r"|pages/[A-Za-z0-9_-]{1,64}/page_thumb\.png"
    r"|(?:pages/[A-Za-z0-9_-]{1,64}/)?"
    r"(?:layers/(?:layer|mask|effect_mask)_\d{3,4}\.png"
    r"|thumbnails/thumb_\d{3,4}\.png))"
)

_PNG_HEADER_PROBE_BYTES = 33  # signature (8) + IHDR length/type (8) + IHDR data (13) + CRC (4)


def png_dimensions_from_header(header: bytes, safe_path: str) -> tuple[int, int]:
    """Parse and validate a PNG signature + IHDR, returning (width, height)."""

    if len(header) < 24 or not header.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError(f"layer image '{safe_path}' is not a PNG")
    if header[12:16] != b"IHDR":
        raise ValueError(f"layer image '{safe_path}' has invalid PNG header")
    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    if width <= 0 or height <= 0:
        raise ValueError(f"layer image '{safe_path}' has invalid dimensions")
    return width, height


class LstrokeArchiveMemberPolicy:
    """Validate ``.lstroke`` ZIP members with per-content-class limits.

    One instance covers one validation run: the cumulative decoded-image
    estimate for high-ratio members is tracked per archive.
    """

    def __init__(
        self,
        *,
        max_image_bytes: int,
        max_image_pixels: int,
        max_document_decode_bytes: int,
    ) -> None:
        self._max_image_bytes = int(max_image_bytes)
        self._max_image_pixels = int(max_image_pixels)
        self._max_document_decode_bytes = int(max_document_decode_bytes)
        self._high_ratio_decode_estimate = 0

    # -- member ratio policy ----------------------------------------------
    def validate_member(
        self,
        archive: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        safe_name: str,
    ) -> None:
        """Raise ``ValueError`` when ``info`` violates the compression policy."""

        if info.compress_size <= 0:
            return
        ratio = info.file_size / info.compress_size
        if ratio <= DEFAULT_MAX_COMPRESSION_RATIO:
            return
        if ratio > IMAGE_MAX_COMPRESSION_RATIO:
            raise ValueError(
                f"Suspicious compression ratio ({ratio:.0f}x) for member '{info.filename}'"
            )
        self._validate_high_ratio_image_member(archive, info, safe_name, ratio)

    def _validate_high_ratio_image_member(
        self,
        archive: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        safe_name: str,
        ratio: float,
    ) -> None:
        """Legacy allowance: deflated LoomStroke PNGs may exceed 200x.

        Every condition is AND-ed; any failure rejects the member with the
        compression policy named in the error.
        """

        reject_prefix = (
            f"Compression ratio ({ratio:.0f}x) for member '{info.filename}' exceeds the "
            f"archive policy limit ({DEFAULT_MAX_COMPRESSION_RATIO}x)"
        )
        if _IMAGE_MEMBER_RE.fullmatch(safe_name) is None:
            raise ValueError(f"{reject_prefix} and is not a LoomStroke image member")
        if info.compress_type != zipfile.ZIP_DEFLATED:
            raise ValueError(
                f"{reject_prefix} and uses a compression method past LoomStroke "
                "saves never wrote"
            )
        if info.file_size > self._max_image_bytes:
            raise ValueError(
                f"{reject_prefix} and its encoded size ({info.file_size:,} bytes) exceeds "
                f"the layer image limit ({self._max_image_bytes:,} bytes)"
            )
        with archive.open(info, "r") as handle:
            header = handle.read(_PNG_HEADER_PROBE_BYTES)
        try:
            width, height = png_dimensions_from_header(header, safe_name)
        except ValueError as exc:
            raise ValueError(f"{reject_prefix}: {exc}") from exc
        pixels = width * height
        decoded_bytes = pixels * 4
        if pixels > self._max_image_pixels:
            raise ValueError(
                f"{reject_prefix} and its dimensions ({width}x{height}) exceed the pixel "
                f"limit ({self._max_image_pixels:,})"
            )
        if decoded_bytes > self._max_image_bytes:
            raise ValueError(
                f"{reject_prefix} and its decoded size ({decoded_bytes:,} bytes) exceeds "
                f"the layer image limit ({self._max_image_bytes:,} bytes)"
            )
        self._high_ratio_decode_estimate += decoded_bytes
        if self._high_ratio_decode_estimate > self._max_document_decode_bytes:
            raise ValueError(
                f"{reject_prefix} and the cumulative decoded image estimate "
                f"({self._high_ratio_decode_estimate:,} bytes) exceeds the document "
                f"budget ({self._max_document_decode_bytes:,} bytes)"
            )

    # -- manifest policy ---------------------------------------------------
    @staticmethod
    def validate_manifest_info(info: zipfile.ZipInfo) -> None:
        """Reject an oversized manifest BEFORE it is read into memory."""

        if info.file_size > MAX_MANIFEST_BYTES:
            raise ValueError(
                f"manifest.json uncompressed size ({info.file_size:,} bytes) exceeds "
                f"limit ({MAX_MANIFEST_BYTES:,} bytes)"
            )

    @staticmethod
    def read_manifest_bounded(archive: zipfile.ZipFile) -> bytes:
        """Read manifest.json without materializing more than the cap.

        ``ZipInfo.file_size`` is validated first, then the stream read is
        bounded to cap + 1 bytes so even a lying local header cannot force
        a larger allocation.  Raises ``KeyError`` when the member is absent
        (callers keep their existing missing-manifest handling).
        """

        info = archive.getinfo(MANIFEST_MEMBER)
        LstrokeArchiveMemberPolicy.validate_manifest_info(info)
        with archive.open(info, "r") as handle:
            data = handle.read(MAX_MANIFEST_BYTES + 1)
        if len(data) > MAX_MANIFEST_BYTES:
            raise ValueError(f"manifest.json stream exceeds limit ({MAX_MANIFEST_BYTES:,} bytes)")
        return data


__all__ = (
    "DEFAULT_MAX_COMPRESSION_RATIO",
    "IMAGE_MAX_COMPRESSION_RATIO",
    "MANIFEST_MEMBER",
    "MAX_MANIFEST_BYTES",
    "LstrokeArchiveMemberPolicy",
    "png_dimensions_from_header",
)
