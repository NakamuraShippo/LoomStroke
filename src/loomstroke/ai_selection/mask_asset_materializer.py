"""Internal helpers for materializing deterministic fake SAM3 mask assets."""

from __future__ import annotations

import binascii
import struct
import zlib

from .assets import Sam3AssetStore
from .data_model import MaskCandidate, MaskCandidateSet


def materialize_fake_mask_assets(
    asset_store: Sam3AssetStore,
    candidate_set: MaskCandidateSet,
) -> tuple[str, ...]:
    """Write cropped alpha mask assets for fake worker candidates when missing."""

    materialized: list[str] = []
    for candidate in candidate_set.candidates:
        if not _is_fake_candidate(candidate) or not _valid_bbox(candidate.bbox):
            continue
        if _mask_asset_exists(asset_store, candidate.mask_asset_id):
            continue

        _, _, width, height = (int(value) for value in candidate.bbox)
        asset_store.save_mask_asset(
            _solid_alpha_png(width, height),
            asset_id=candidate.mask_asset_id,
            kind="alpha_mask",
            width=width,
            height=height,
            bbox=tuple(int(value) for value in candidate.bbox),
            source_candidate_id=candidate.candidate_id,
            metadata={
                "provider": "fake",
                "source": "sam3_fake_mask_materializer",
                "candidateSetId": candidate_set.set_id,
            },
        )
        materialized.append(candidate.mask_asset_id)
    return tuple(materialized)


def _is_fake_candidate(candidate: MaskCandidate) -> bool:
    provenance = candidate.provenance
    return provenance.get("provider") == "fake" or provenance.get("source") == "sam3_fake_worker"


def _valid_bbox(bbox: tuple[int, int, int, int]) -> bool:
    _, _, width, height = (int(value) for value in bbox)
    return width > 0 and height > 0


def _mask_asset_exists(asset_store: Sam3AssetStore, asset_id: str) -> bool:
    try:
        asset_store.load_mask_asset(asset_id)
    except (OSError, ValueError):
        return False
    return True


def _solid_alpha_png(width: int, height: int) -> bytes:
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise ValueError("SAM3 fake mask dimensions must be positive.")

    row = b"\x00" + (b"\xff\xff\xff\xff" * width)
    raw = row * height
    return b"".join(
        (
            b"\x89PNG\r\n\x1a\n",
            _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)),
            _png_chunk(b"IDAT", zlib.compress(raw)),
            _png_chunk(b"IEND", b""),
        )
    )


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    crc = binascii.crc32(kind)
    crc = binascii.crc32(payload, crc) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", crc)


__all__ = ["materialize_fake_mask_assets"]
