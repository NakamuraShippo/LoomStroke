"""CMYK TIFF / Grayscale-K print output (CT-11 P3, spec 11 §8.1/§9.2).

Built directly on the CT-11 P1 entry point: every colour decision goes
through LittleCMS with registered, fingerprint-addressed profiles.

- **Profile CMYK** (spec 11 §9.2): RGB→CMYK is never a formula — the
  destination CMYK profile's own black generation does the separation.
  The result is written as a flattened CMYK TIFF with the destination
  profile embedded, so the receiver sees exactly the intent.
- **Grayscale K** (spec 11 §9.2): the flattened page is reduced to
  lightness via LittleCMS (L* of CIELAB — perceptual, not a luma
  formula) and placed on the K channel of a CMYK TIFF with C=M=Y=0.
  K is device ink here; no ICC is embedded because none describes it.

Transparency is flattened onto paper white *before* any conversion
(spec 11 §8.1 — print output has no alpha).  Writes are atomic.
"""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageCms

from .atomic_export_writer import write_atomically
from .color_profile_registry import _INTENTS, ColorProfileRegistry, RenderingIntent

__all__ = [
    "flatten_to_paper",
    "rgba_to_cmyk",
    "rgba_to_grayscale_k",
    "write_cmyk_tiff",
    "write_grayscale_k_tiff",
]


def flatten_to_paper(
    rgba: NDArray[np.uint8], paper: tuple[int, int, int] = (255, 255, 255)
) -> NDArray[np.uint8]:
    """Composite straight-alpha RGBA over an opaque paper colour."""
    if rgba.ndim != 3 or rgba.shape[2] != 4 or rgba.dtype != np.uint8:
        raise ValueError("flatten_to_paper expects straight-alpha RGBA8 (H x W x 4)")
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
    color = rgba[:, :, :3].astype(np.float32)
    background = np.asarray(paper, dtype=np.float32).reshape(1, 1, 3)
    flattened = color * alpha + background * (1.0 - alpha)
    return np.clip(np.rint(flattened), 0, 255).astype(np.uint8)


def rgba_to_cmyk(
    rgba: NDArray[np.uint8],
    registry: ColorProfileRegistry,
    *,
    source_profile_id: str,
    cmyk_profile_id: str,
    intent: RenderingIntent = "relative",
    black_point_compensation: bool = True,
) -> NDArray[np.uint8]:
    """Flatten and separate through the CMYK profile's black generation.

    Returns an ``(H, W, 4)`` uint8 CMYK array.  The destination profile
    must actually be CMYK — a wrong-colorspace profile raises instead of
    producing a silently mislabelled separation (spec 11 §14).
    """
    source_bytes = registry.profile_bytes(source_profile_id)
    if source_bytes is None:
        raise ValueError(f"unknown source profile: {source_profile_id}")
    cmyk_info = registry.info(cmyk_profile_id)
    cmyk_bytes = registry.profile_bytes(cmyk_profile_id)
    if cmyk_info is None or cmyk_bytes is None:
        raise ValueError(f"unknown CMYK profile: {cmyk_profile_id}")
    if cmyk_info.colorspace != "CMYK":
        raise ValueError(f"profile {cmyk_profile_id} is {cmyk_info.colorspace}, not CMYK")

    flags = ImageCms.Flags.NONE
    if black_point_compensation:
        flags = ImageCms.Flags.BLACKPOINTCOMPENSATION
    transform = ImageCms.buildTransform(
        ImageCms.ImageCmsProfile(BytesIO(source_bytes)),
        ImageCms.ImageCmsProfile(BytesIO(cmyk_bytes)),
        "RGB",
        "CMYK",
        renderingIntent=_INTENTS[intent],
        flags=flags,
    )
    rgb_image = Image.fromarray(flatten_to_paper(rgba), mode="RGB")
    converted = ImageCms.applyTransform(rgb_image, transform)
    return np.asarray(converted, dtype=np.uint8)


def rgba_to_grayscale_k(
    rgba: NDArray[np.uint8],
    registry: ColorProfileRegistry,
    *,
    source_profile_id: str,
) -> NDArray[np.uint8]:
    """Flatten to paper and reduce to a K-ink plane via CIELAB L*.

    Returns an ``(H, W)`` uint8 array of ink coverage: paper white is
    K=0, solid black is K=255.
    """
    source_bytes = registry.profile_bytes(source_profile_id)
    if source_bytes is None:
        raise ValueError(f"unknown source profile: {source_profile_id}")
    transform = ImageCms.buildTransform(
        ImageCms.ImageCmsProfile(BytesIO(source_bytes)),
        ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB")),
        "RGB",
        "LAB",
    )
    rgb_image = Image.fromarray(flatten_to_paper(rgba), mode="RGB")
    lab = np.asarray(ImageCms.applyTransform(rgb_image, transform), dtype=np.uint8)
    lightness = lab[:, :, 0]  # Pillow scales L* 0..100 to 0..255
    return (255 - lightness.astype(np.int16)).astype(np.uint8)


def write_cmyk_tiff(
    destination: Path,
    cmyk: NDArray[np.uint8],
    *,
    dpi: tuple[float, float],
    icc_bytes: bytes,
) -> int:
    """Atomically write a flattened CMYK TIFF with the profile embedded."""
    if cmyk.ndim != 3 or cmyk.shape[2] != 4 or cmyk.dtype != np.uint8:
        raise ValueError("write_cmyk_tiff expects a CMYK8 array (H x W x 4)")
    image = Image.fromarray(np.ascontiguousarray(cmyk), mode="CMYK")

    def _encode(temp_path: Path) -> None:
        image.save(temp_path, format="TIFF", dpi=dpi, icc_profile=icc_bytes)

    return write_atomically(Path(destination), _encode)


def write_grayscale_k_tiff(
    destination: Path,
    k_plane: NDArray[np.uint8],
    *,
    dpi: tuple[float, float],
) -> int:
    """Atomically write the K plane as a C=M=Y=0 CMYK TIFF (spec 11 §9.2)."""
    if k_plane.ndim != 2 or k_plane.dtype != np.uint8:
        raise ValueError("write_grayscale_k_tiff expects a K8 plane (H x W)")
    cmyk = np.zeros((*k_plane.shape, 4), dtype=np.uint8)
    cmyk[:, :, 3] = k_plane
    image = Image.fromarray(cmyk, mode="CMYK")

    def _encode(temp_path: Path) -> None:
        image.save(temp_path, format="TIFF", dpi=dpi)

    return write_atomically(Path(destination), _encode)
