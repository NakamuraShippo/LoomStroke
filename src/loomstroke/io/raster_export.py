"""Raster export through the shared contract (CT-09 Phase E2).

The one function every raster caller (GUI now; batch and MCP later) goes
through: resolve metadata and alpha via :mod:`export_contract`, encode
with the existing :class:`FileIO` metadata path (DPI / ICC actually land
in the file, unlike the old ``QPixmap.save`` route), and write through
:func:`write_atomically` so a failed encode never damages an existing
output.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray

from .atomic_export_writer import write_atomically
from .export_contract import (
    ExportIssue,
    capabilities,
    resolve_alpha,
    resolve_export_metadata,
)
from .file_io import ColorProfile, FileFormat, FileIO, ImageMetadata

logger = logging.getLogger(__name__)

__all__ = ["RasterExportResult", "export_raster"]


@dataclass(frozen=True, slots=True)
class RasterExportResult:
    """Success with warnings is distinct from clean success (spec 09 §15)."""

    status: Literal["success", "warning", "failed"]
    destination: Path | None
    issues: tuple[ExportIssue, ...]
    bytes_written: int = 0


def export_raster(
    rgba: NDArray[np.uint8],
    destination: Path,
    format_id: str,
    *,
    request_dpi: tuple[float, float] | float | None = None,
    canvas_dpi: tuple[float, float] | float | None = None,
    document_dpi: tuple[float, float] | float | None = None,
    icc_bytes: bytes | None = None,
    alpha_mode: Literal["preserve", "matte"] = "preserve",
    matte_rgba: tuple[int, int, int, int] | None = None,
    background_rgba: tuple[int, int, int, int] = (255, 255, 255, 255),
    file_io: FileIO | None = None,
    encoder_options: Mapping[str, Any] | None = None,
) -> RasterExportResult:
    """Export one straight-alpha RGBA8 frame (H x W x 4).

    ``encoder_options`` are per-export encoder overrides (JPEG quality, PNG
    compress level, TIFF compression, WebP quality / lossless); FileIO only
    honours its allowlisted keys for the target format (CT-09 §6).
    """
    caps = capabilities(format_id)
    if caps.layered:
        raise ValueError(f"{caps.format_id} is not a raster export format")
    if rgba.ndim != 3 or rgba.shape[2] != 4 or rgba.dtype != np.uint8:
        raise ValueError("export_raster expects straight-alpha RGBA8 (H x W x 4)")

    metadata_resolution = resolve_export_metadata(
        request_dpi=request_dpi,
        canvas_dpi=canvas_dpi,
        document_dpi=document_dpi,
        icc_bytes=icc_bytes,
    )
    alpha_resolution = resolve_alpha(
        caps.format_id,
        alpha_mode=alpha_mode,
        matte_rgba=matte_rgba,
        background_rgba=background_rgba,
    )
    issues = list(metadata_resolution.issues)
    # CT-09 E3: capability gaps surface as issues, never as silent drops.
    dpi_requested = any(value is not None for value in (request_dpi, canvas_dpi, document_dpi))
    if dpi_requested and not caps.supports_dpi:
        issues.append(
            ExportIssue(
                code="dpi_not_stored",
                message=f"{caps.format_id} does not store DPI metadata",
            )
        )
    if metadata_resolution.icc_bytes and not caps.supports_icc:
        issues.append(
            ExportIssue(
                code="icc_not_stored",
                message=f"{caps.format_id} cannot embed an ICC profile",
            )
        )
    has_transparency = bool((rgba[:, :, 3] != 255).any())
    if has_transparency:
        # The flatten warning only matters when there was something to
        # flatten; a fully opaque page exports identically either way.
        issues.extend(alpha_resolution.issues)

    data: NDArray[np.uint8] = rgba
    if not alpha_resolution.keep_alpha:
        data = _matte(rgba, alpha_resolution.matte_rgba or background_rgba)

    profile = None
    if metadata_resolution.icc_bytes and caps.supports_icc:
        profile = ColorProfile(name="embedded", data=metadata_resolution.icc_bytes)
    metadata = ImageMetadata(
        width=int(data.shape[1]),
        height=int(data.shape[0]),
        channels=int(data.shape[2]),
        bit_depth=8,
        color_profile=profile,
        dpi=metadata_resolution.dpi,
        extra={"encoder_options": dict(encoder_options)} if encoder_options else None,
    )
    encoder = file_io or FileIO()
    file_format = FileFormat(caps.format_id)

    def encode(temp_path: Path) -> None:
        if not encoder.write(temp_path, data, metadata, file_format):
            raise OSError(f"encoder failed for {destination.name}")

    try:
        bytes_written = write_atomically(Path(destination), encode)
    except Exception as error:
        logger.warning("raster export failed", exc_info=True)
        issues.append(ExportIssue(code="encode_failed", message=str(error), severity="error"))
        return RasterExportResult(status="failed", destination=None, issues=tuple(issues))

    status: Literal["success", "warning"] = "warning" if issues else "success"
    return RasterExportResult(
        status=status,
        destination=Path(destination),
        issues=tuple(issues),
        bytes_written=bytes_written,
    )


def _matte(rgba: NDArray[np.uint8], matte_rgba: tuple[int, int, int, int]) -> NDArray[np.uint8]:
    """Composite straight-alpha RGBA over an opaque matte -> RGB."""
    alpha = rgba[:, :, 3:4].astype(np.float32) / 255.0
    rgb = rgba[:, :, :3].astype(np.float32)
    matte: Any = np.asarray(matte_rgba[:3], dtype=np.float32)
    flattened = rgb * alpha + matte * (1.0 - alpha)
    return np.asarray(flattened.round().clip(0, 255).astype(np.uint8))
