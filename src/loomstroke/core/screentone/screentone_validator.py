"""Screentone validation against the document DPI and output scale (CT-02)."""

from __future__ import annotations

import math

from .screentone_params import MAX_LPI, MIN_LPI, MIN_PITCH_PX, ScreentoneParams
from .tone_issue import ToneIssue

#: Used when the document carries no DPI (spec: preview with the default DPI,
#: hand the warning to preflight).
DEFAULT_DOCUMENT_DPI = 96.0


class ScreentoneValidator:
    """Stable issue codes for a tone definition (pure, UI-free)."""

    def pixel_pitch(self, params: ScreentoneParams, document_dpi: float | None) -> float:
        """Distance between screen lines in canvas pixels: ``dpi / lpi``."""
        dpi = self.effective_dpi(document_dpi)
        lpi = max(float(params.frequency_lpi), 1e-6)
        return dpi / lpi

    @staticmethod
    def effective_dpi(document_dpi: float | None) -> float:
        if document_dpi is None:
            return DEFAULT_DOCUMENT_DPI
        try:
            dpi = float(document_dpi)
        except (TypeError, ValueError):
            return DEFAULT_DOCUMENT_DPI
        if not math.isfinite(dpi) or dpi < 1.0:
            return DEFAULT_DOCUMENT_DPI
        return dpi

    def validate(
        self,
        params: ScreentoneParams,
        document_dpi: float | None,
        *,
        output_scale: float = 1.0,
    ) -> tuple[ToneIssue, ...]:
        issues: list[ToneIssue] = []
        if document_dpi is None or self.effective_dpi(document_dpi) != float(document_dpi or 0):
            issues.append(
                ToneIssue(
                    code="dpi_missing",
                    message=f"document DPI unavailable; using {DEFAULT_DOCUMENT_DPI:g} dpi",
                )
            )
        lpi = float(params.frequency_lpi)
        if not (MIN_LPI <= lpi <= MAX_LPI):
            issues.append(
                ToneIssue(
                    code="lpi_out_of_range",
                    message=f"frequency {lpi:g} lpi outside {MIN_LPI:g}..{MAX_LPI:g}",
                    severity="error",
                )
            )
        pitch = self.pixel_pitch(params, document_dpi)
        if pitch < MIN_PITCH_PX:
            issues.append(
                ToneIssue(
                    code="pitch_below_2px",
                    message=f"screen pitch {pitch:.2f} px is below {MIN_PITCH_PX:g} px",
                    severity="error",
                )
            )
        scale = float(output_scale) if math.isfinite(float(output_scale)) else 1.0
        if scale > 0 and pitch * scale < MIN_PITCH_PX and pitch >= MIN_PITCH_PX:
            issues.append(
                ToneIssue(
                    code="nyquist_moire",
                    message=f"screen pitch {pitch * scale:.2f} px at {scale:g}x would alias",
                )
            )
        density = float(params.density_percent)
        if not (0.0 <= density <= 100.0):
            issues.append(
                ToneIssue(
                    code="density_out_of_range",
                    message=f"density {density:g}% outside 0..100",
                    severity="error",
                )
            )
        return tuple(issues)
