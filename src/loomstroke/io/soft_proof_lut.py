"""CPU-built soft-proof 3D LUTs (CT-11 P2, spec 11 §8.1/§8.2).

The proof round-trip — document RGB → proof (printer) profile → display
RGB — is computed once per profile/intent combination by LittleCMS
(``ImageCms.buildProofTransform``, the CPU authority of spec 11 §8.1)
over an N³ identity grid.  The resulting table is what the wgpu final
pass samples with trilinear filtering; ``sample_lut`` is the CPU
reference implementation of exactly that sampling, used by the golden
tests that pin the GPU tolerance.

The gamut mask (spec 11 §8.2) is derived from LittleCMS's GAMUTCHECK
alarm: grid colors whose gamut-check output diverges from their proof
output are out of gamut.  It is a per-LUT-cell mask, not a promise of
per-pixel exactness between cells.

Everything here is pure CPU/numpy — no wgpu, no Qt — so it can run in a
worker (spec 11 §12).
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageCms

from .color_profile_registry import _INTENTS, ColorProfileRegistry, RenderingIntent

__all__ = ["ProofLut", "build_proof_lut", "sample_lut"]

_DEFAULT_SIZE = 33
# Colors this far (max channel delta) from their gamut-check alarm
# rendition count as in gamut; GAMUTCHECK replaces out-of-gamut colors
# with a flat alarm gray, so real divergence is large.
_GAMUT_DIVERGENCE = 8


@dataclass(frozen=True, slots=True)
class ProofLut:
    """An N³ RGB→RGB proof table plus its out-of-gamut companion.

    ``table[b, g, r]`` holds the display color for the grid point
    ``(r, g, b)`` — the memory order a ``(width=r, height=g, depth=b)``
    3D texture upload expects.  ``key`` identifies the transform
    (profile fingerprints + intents), never a path.
    """

    key: str
    size: int
    table: NDArray[np.uint8]  # (N, N, N, 3) uint8, indexed [b, g, r]
    gamut_mask: NDArray[np.uint8]  # (N, N, N) uint8, 255 = out of gamut


def build_proof_lut(
    registry: ColorProfileRegistry,
    *,
    source_profile_id: str,
    proof_profile_id: str,
    intent: RenderingIntent = "perceptual",
    proof_intent: RenderingIntent = "relative",
    black_point_compensation: bool = True,
    size: int = _DEFAULT_SIZE,
) -> ProofLut:
    """Build the proof round-trip LUT through LittleCMS.

    The display side is the sRGB assumption of spec 11 §8.3 (monitor
    profiles arrive with the platform adapter).  Unknown profile ids
    raise — a proof that silently ignored its profile would lie
    (spec 11 §14).
    """
    if size < 2 or size > 128:
        raise ValueError(f"unreasonable LUT size: {size}")
    source_bytes = registry.profile_bytes(source_profile_id)
    proof_bytes = registry.profile_bytes(proof_profile_id)
    if source_bytes is None:
        raise ValueError(f"unknown source profile: {source_profile_id}")
    if proof_bytes is None:
        raise ValueError(f"unknown proof profile: {proof_profile_id}")

    display = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    source = ImageCms.ImageCmsProfile(BytesIO(source_bytes))
    proof = ImageCms.ImageCmsProfile(BytesIO(proof_bytes))
    base_flags = ImageCms.Flags.SOFTPROOFING
    if black_point_compensation:
        base_flags |= ImageCms.Flags.BLACKPOINTCOMPENSATION

    grid = _identity_grid(size)
    proofed = _apply_proof(grid, source, display, proof, intent, proof_intent, base_flags)
    checked = _apply_proof(
        grid,
        source,
        display,
        proof,
        intent,
        proof_intent,
        base_flags | ImageCms.Flags.GAMUTCHECK,
    )
    divergence = np.abs(proofed.astype(np.int16) - checked.astype(np.int16)).max(axis=-1)
    gamut_mask = np.where(divergence > _GAMUT_DIVERGENCE, 255, 0).astype(np.uint8)

    key = (
        f"{source_profile_id}:{proof_profile_id}:{intent}:{proof_intent}"
        f":bpc={int(black_point_compensation)}:n={size}"
    )
    return ProofLut(key=key, size=size, table=proofed, gamut_mask=gamut_mask)


def sample_lut(lut: ProofLut, rgb: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """Trilinear LUT sampling — the CPU golden for the GPU final pass.

    ``rgb`` is ``(..., 3)`` uint8; the result has the same shape.
    """
    if rgb.shape[-1] != 3 or rgb.dtype != np.uint8:
        raise ValueError("sample_lut expects uint8 RGB with a trailing 3-channel axis")
    n = lut.size
    coords = rgb.astype(np.float32) / 255.0 * (n - 1)
    low = np.floor(coords).astype(np.int32)
    low = np.minimum(low, n - 2)
    frac = coords - low

    def corner(dr: int, dg: int, db: int) -> NDArray[np.float32]:
        return lut.table[low[..., 2] + db, low[..., 1] + dg, low[..., 0] + dr].astype(np.float32)

    fr = frac[..., 0:1]
    fg = frac[..., 1:2]
    fb = frac[..., 2:3]
    c00 = corner(0, 0, 0) * (1 - fr) + corner(1, 0, 0) * fr
    c10 = corner(0, 1, 0) * (1 - fr) + corner(1, 1, 0) * fr
    c01 = corner(0, 0, 1) * (1 - fr) + corner(1, 0, 1) * fr
    c11 = corner(0, 1, 1) * (1 - fr) + corner(1, 1, 1) * fr
    c0 = c00 * (1 - fg) + c10 * fg
    c1 = c01 * (1 - fg) + c11 * fg
    result = c0 * (1 - fb) + c1 * fb
    return np.clip(np.rint(result), 0, 255).astype(np.uint8)


def _identity_grid(size: int) -> NDArray[np.uint8]:
    """The (N, N, N, 3) grid of source colors, indexed [b, g, r]."""
    axis = np.rint(np.linspace(0.0, 255.0, size)).astype(np.uint8)
    b, g, r = np.meshgrid(axis, axis, axis, indexing="ij")
    return np.stack([r, g, b], axis=-1)


def _apply_proof(
    grid: NDArray[np.uint8],
    source: ImageCms.ImageCmsProfile,
    display: ImageCms.ImageCmsProfile,
    proof: ImageCms.ImageCmsProfile,
    intent: RenderingIntent,
    proof_intent: RenderingIntent,
    flags: int,
) -> NDArray[np.uint8]:
    size = grid.shape[0]
    transform = ImageCms.buildProofTransform(
        source,
        display,
        proof,
        "RGB",
        "RGB",
        renderingIntent=_INTENTS[intent],
        proofRenderingIntent=_INTENTS[proof_intent],
        flags=flags,
    )
    flat = Image.fromarray(grid.reshape(size * size, size, 3), mode="RGB")
    return np.asarray(ImageCms.applyTransform(flat, transform), dtype=np.uint8).reshape(
        size, size, size, 3
    )
