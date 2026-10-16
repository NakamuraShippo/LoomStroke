"""Normalized screentone definition (CT-02 spec "データモデル").

This is the *runtime* form.  The persisted form is the plain
``effect_params`` mapping on the effect layer (schema_version 1); the
normalizer converts between the two and never rewrites unknown keys.
"""

from __future__ import annotations

from dataclasses import dataclass, field

SCHEMA_VERSION = 1
PATTERNS: tuple[str, ...] = ("round", "ellipse", "line", "cross", "stochastic")
ORIGIN_MODES: tuple[str, ...] = ("page", "local", "custom")
PREVIEW_POLICIES: tuple[str, ...] = ("accurate", "moire_safe", "solid_fallback")

MIN_LPI = 5.0
MAX_LPI = 200.0
#: Finer than this at the output scale cannot be represented; spec §performance.
MIN_PITCH_PX = 2.0


@dataclass(frozen=True, slots=True)
class ScreentoneParams:
    """Validated tone definition.  All units are stable (LPI, %, degrees, canvas px)."""

    pattern: str = "round"
    frequency_lpi: float = 60.0
    density_percent: float = 20.0
    angle_deg: float = 45.0
    origin_mode: str = "page"
    origin_px: tuple[float, float] = (0.0, 0.0)
    dot_aspect: float = 1.0
    seed: int = 0
    tone_rgba: tuple[int, int, int, int] = (0, 0, 0, 255)
    preview_policy: str = "moire_safe"
    schema_version: int = SCHEMA_VERSION
    #: Keys of the source mapping this instance could not interpret (kept
    #: so a later save does not destroy them).
    unknown_keys: tuple[str, ...] = field(default=(), compare=False)

    @property
    def density(self) -> float:
        """Coverage fraction in ``[0, 1]``."""
        return max(0.0, min(1.0, float(self.density_percent) / 100.0))
