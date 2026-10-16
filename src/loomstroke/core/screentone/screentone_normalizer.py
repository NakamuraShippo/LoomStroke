"""Raw ``effect_params`` <-> :class:`ScreentoneParams` (CT-02).

``normalize`` is total: any mapping (including ``{}``, legacy or partially
broken payloads) yields usable params, clamping numbers into range and
falling back to ``round`` for an unknown pattern — while remembering the
unknown keys so ``to_payload`` can carry them along instead of dropping
them (spec §エラー / フォールバック).
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from .screentone_params import (
    MAX_LPI,
    MIN_LPI,
    ORIGIN_MODES,
    PATTERNS,
    PREVIEW_POLICIES,
    SCHEMA_VERSION,
    ScreentoneParams,
)

_KNOWN_KEYS: frozenset[str] = frozenset(
    {
        "schema_version",
        "pattern",
        "frequency_lpi",
        "density_percent",
        "angle_deg",
        "origin_mode",
        "origin_px",
        "dot_aspect",
        "seed",
        "tone_rgba",
        "preview_policy",
    }
)


class ScreentoneNormalizer:
    """Clamp / default / round-trip screentone parameters."""

    def normalize(self, raw: Mapping[str, Any] | None) -> ScreentoneParams:
        payload = dict(raw) if isinstance(raw, Mapping) else {}
        defaults = ScreentoneParams()
        pattern = str(payload.get("pattern", defaults.pattern)).strip().lower()
        if pattern not in PATTERNS:
            pattern = defaults.pattern
        origin_mode = str(payload.get("origin_mode", defaults.origin_mode)).strip().lower()
        if origin_mode not in ORIGIN_MODES:
            origin_mode = defaults.origin_mode
        policy = str(payload.get("preview_policy", defaults.preview_policy)).strip().lower()
        if policy not in PREVIEW_POLICIES:
            policy = defaults.preview_policy
        angle = self._number(payload.get("angle_deg"), defaults.angle_deg)
        angle = ((angle + 180.0) % 360.0) - 180.0
        return ScreentoneParams(
            pattern=pattern,
            frequency_lpi=self._clamp(
                self._number(payload.get("frequency_lpi"), defaults.frequency_lpi),
                MIN_LPI,
                MAX_LPI,
            ),
            density_percent=self._clamp(
                self._number(payload.get("density_percent"), defaults.density_percent),
                0.0,
                100.0,
            ),
            angle_deg=angle,
            origin_mode=origin_mode,
            origin_px=self._point(payload.get("origin_px"), defaults.origin_px),
            dot_aspect=self._clamp(
                self._number(payload.get("dot_aspect"), defaults.dot_aspect), 0.25, 4.0
            ),
            seed=int(self._number(payload.get("seed"), float(defaults.seed))),
            tone_rgba=self._rgba(payload.get("tone_rgba"), defaults.tone_rgba),
            preview_policy=policy,
            schema_version=SCHEMA_VERSION,
            unknown_keys=tuple(sorted(key for key in payload if key not in _KNOWN_KEYS)),
        )

    def to_payload(
        self,
        params: ScreentoneParams,
        *,
        previous: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Plain mapping for ``effect_params``; unknown keys of *previous* survive."""
        payload: dict[str, Any] = {}
        if isinstance(previous, Mapping):
            for key in params.unknown_keys:
                if key in previous:
                    payload[key] = previous[key]
        payload.update(
            {
                "schema_version": SCHEMA_VERSION,
                "pattern": params.pattern,
                "frequency_lpi": float(params.frequency_lpi),
                "density_percent": float(params.density_percent),
                "angle_deg": float(params.angle_deg),
                "origin_mode": params.origin_mode,
                "origin_px": [float(params.origin_px[0]), float(params.origin_px[1])],
                "dot_aspect": float(params.dot_aspect),
                "seed": int(params.seed),
                "tone_rgba": [int(part) for part in params.tone_rgba],
                "preview_policy": params.preview_policy,
            }
        )
        return payload

    # -- helpers ----------------------------------------------------------------

    @staticmethod
    def _number(value: Any, default: float) -> float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            try:
                value = float(value)
            except (TypeError, ValueError):
                return float(default)
        number = float(value)
        if not math.isfinite(number):
            return float(default)
        return number

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    @classmethod
    def _point(cls, value: Any, default: tuple[float, float]) -> tuple[float, float]:
        if isinstance(value, (list, tuple)) and len(value) == 2:
            return (cls._number(value[0], default[0]), cls._number(value[1], default[1]))
        return (float(default[0]), float(default[1]))

    @classmethod
    def _rgba(cls, value: Any, default: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        if isinstance(value, (list, tuple)) and len(value) in (3, 4):
            parts = [int(cls._clamp(cls._number(part, 0.0), 0.0, 255.0)) for part in value]
            if len(parts) == 3:
                parts.append(255)
            return (parts[0], parts[1], parts[2], parts[3])
        return default
