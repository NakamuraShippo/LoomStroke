"""The shared export request/result contract (CT-09 Phase E1).

Every exporter — GUI, batch (CT-10), MCP, and later PDF/Webtoon — speaks
this vocabulary, so "what metadata goes out" and "what the format can
carry" are decided in exactly one place.  Three rules carry the design
(spec 09 §9/§10):

- **Metadata precedence**: explicit request > page canvas override >
  document metadata > safe defaults (72 dpi, treated-as-sRGB).  Defaults
  never fabricate ICC bytes — "treated as sRGB" is not "tagged as sRGB".
- **No silent omission**: a format that cannot carry what the request
  asks for produces an :class:`ExportIssue`, never a quiet drop.  JPEG
  cannot hold alpha, so without an explicit matte it flattens onto the
  document background *with a warning*.
- Pure and Qt-free; encoders (FileIO / PSD / SVG adapters) consume the
  resolved values, they never re-derive them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

__all__ = [
    "ExportFormatCapabilities",
    "ExportIssue",
    "ResolvedAlpha",
    "ResolvedMetadata",
    "capabilities",
    "resolve_alpha",
    "resolve_export_metadata",
]

IssueSeverity = Literal["warning", "error"]

_DEFAULT_DPI = (72.0, 72.0)


@dataclass(frozen=True, slots=True)
class ExportIssue:
    """A stable-code issue; the GUI translates from ``code``, never from
    ``message`` (spec 09 §14)."""

    code: str
    message: str
    severity: IssueSeverity = "warning"


@dataclass(frozen=True, slots=True)
class ExportFormatCapabilities:
    """What a format can carry.  ``layered`` marks formats whose adapter
    keeps layer structure (PSD) or vector objects (SVG)."""

    format_id: str
    supports_alpha: bool
    supports_icc: bool
    supports_dpi: bool
    layered: bool = False


_CAPABILITIES: dict[str, ExportFormatCapabilities] = {
    "png": ExportFormatCapabilities(
        "png", supports_alpha=True, supports_icc=True, supports_dpi=True
    ),
    "jpeg": ExportFormatCapabilities(
        "jpeg", supports_alpha=False, supports_icc=True, supports_dpi=True
    ),
    "webp": ExportFormatCapabilities(
        "webp", supports_alpha=True, supports_icc=True, supports_dpi=False
    ),
    "tiff": ExportFormatCapabilities(
        "tiff", supports_alpha=True, supports_icc=True, supports_dpi=True
    ),
    "bmp": ExportFormatCapabilities(
        "bmp", supports_alpha=False, supports_icc=False, supports_dpi=True
    ),
    "psd": ExportFormatCapabilities(
        "psd", supports_alpha=True, supports_icc=True, supports_dpi=True, layered=True
    ),
    "svg": ExportFormatCapabilities(
        "svg", supports_alpha=True, supports_icc=False, supports_dpi=False, layered=True
    ),
}


def capabilities(format_id: str) -> ExportFormatCapabilities:
    """Capabilities for ``format_id`` (case-insensitive, "jpg" == "jpeg")."""
    normalized = format_id.strip().lower().lstrip(".")
    if normalized == "jpg":
        normalized = "jpeg"
    if normalized == "tif":
        normalized = "tiff"
    try:
        return _CAPABILITIES[normalized]
    except KeyError:
        raise ValueError(f"unknown export format: {format_id!r}") from None


@dataclass(frozen=True, slots=True)
class ResolvedMetadata:
    """Metadata after precedence + validation, ready for an encoder."""

    dpi: tuple[float, float]
    icc_bytes: bytes | None
    issues: tuple[ExportIssue, ...] = field(default=())


def resolve_export_metadata(
    *,
    request_dpi: tuple[float, float] | float | None = None,
    canvas_dpi: tuple[float, float] | float | None = None,
    document_dpi: tuple[float, float] | float | None = None,
    icc_bytes: bytes | None = None,
) -> ResolvedMetadata:
    """Apply the precedence chain and normalize (spec 09 §9).

    ``icc_bytes`` is whatever the caller resolved through the same chain;
    it passes through untouched when present and is **never fabricated**
    when absent.  Parse validation is the encoder's job (it owns the ICC
    library); this layer owns precedence and DPI hygiene only.
    """
    issues: list[ExportIssue] = []
    dpi = _DEFAULT_DPI
    for candidate in (document_dpi, canvas_dpi, request_dpi):
        if candidate is None:
            continue
        normalized = _normalized_dpi(candidate)
        if normalized is None:
            issues.append(
                ExportIssue(
                    code="dpi_invalid",
                    message=f"ignored invalid dpi value: {candidate!r}",
                )
            )
            continue
        dpi = normalized
    return ResolvedMetadata(dpi=dpi, icc_bytes=icc_bytes, issues=tuple(issues))


def _normalized_dpi(value: tuple[float, float] | float) -> tuple[float, float] | None:
    pair = (value, value) if isinstance(value, (int, float)) else tuple(value)
    if len(pair) != 2:
        return None
    normalized = []
    for component in pair:
        number = float(component)
        if not math.isfinite(number) or number < 1.0:
            return None
        normalized.append(number)
    return (normalized[0], normalized[1])


@dataclass(frozen=True, slots=True)
class ResolvedAlpha:
    """``keep_alpha=False`` means: composite onto ``matte_rgba`` first."""

    keep_alpha: bool
    matte_rgba: tuple[int, int, int, int] | None
    issues: tuple[ExportIssue, ...] = field(default=())


def resolve_alpha(
    format_id: str,
    *,
    alpha_mode: Literal["preserve", "matte"] = "preserve",
    matte_rgba: tuple[int, int, int, int] | None = None,
    background_rgba: tuple[int, int, int, int] = (255, 255, 255, 255),
) -> ResolvedAlpha:
    """Decide what happens to transparency for ``format_id`` (spec 09 §9).

    An explicit matte always wins.  A format without alpha support
    flattens onto the document background — with a warning, because the
    user did not ask for that (JPEG rule).
    """
    caps = capabilities(format_id)
    if alpha_mode == "matte":
        return ResolvedAlpha(keep_alpha=False, matte_rgba=matte_rgba or background_rgba)
    if caps.supports_alpha:
        return ResolvedAlpha(keep_alpha=True, matte_rgba=None)
    return ResolvedAlpha(
        keep_alpha=False,
        matte_rgba=background_rgba,
        issues=(
            ExportIssue(
                code="alpha_flattened_to_background",
                message=f"{caps.format_id} cannot store alpha; "
                "transparency was flattened onto the document background",
            ),
        ),
    )
