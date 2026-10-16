"""Independent, persistable brush and filter settings for Filter Pen."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any

from loomstroke.core.command_types import BrushSettings
from loomstroke.core.filter_pen.compatibility import bind_legacy_names as _bind_legacy_names

MIN_SPACING = 0.01
MAX_SPACING = 3.0
LOG_RATIO = math.log(MIN_SPACING / MAX_SPACING)


def spacing_from_density(value: float) -> float:
    return MAX_SPACING * math.exp(max(0.0, min(1.0, value)) * LOG_RATIO)


def density_from_spacing(value: float) -> float:
    return math.log(max(MIN_SPACING, min(MAX_SPACING, value)) / MAX_SPACING) / LOG_RATIO


@dataclass
class FilterPenSettings:
    size: float = 24.0
    opacity: float = 1.0
    hardness: float = 1.0
    spacing: float = 0.1
    pressure_sensitive: bool = True
    tilt_angle: bool = False
    smoothing: float = 0.0
    flow: float = 1.0
    tone_range: float = 1.0
    tone_gamma: float = 0.6
    recontour: bool = False
    recontour_smoothness: float = 30.0
    effect_type: str = "gaussian_blur"
    effect_params: dict[str, Any] = field(default_factory=dict)
    params_by_effect: dict[str, dict[str, Any]] = field(default_factory=dict)
    tip_path: str | None = None
    hardness_mode: str = "tip"

    # Legacy dataclass storage preserves saved tools.effect_pen dictionaries.
    @property
    def filter_type(self) -> str:
        return self.effect_type

    @filter_type.setter
    def filter_type(self, value: str) -> None:
        self.effect_type = value

    @property
    def filter_params(self) -> dict[str, Any]:
        return self.effect_params

    @filter_params.setter
    def filter_params(self, value: dict[str, Any]) -> None:
        self.effect_params = value

    @property
    def params_by_filter(self) -> dict[str, dict[str, Any]]:
        return self.params_by_effect

    @params_by_filter.setter
    def params_by_filter(self, value: dict[str, dict[str, Any]]) -> None:
        self.params_by_effect = value

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["stamp_density"] = density_from_spacing(self.spacing)
        return result

    def update(self, values: dict[str, Any]) -> None:
        values = {
            **values,
            **{
                legacy: values[canonical]
                for canonical, legacy in (
                    ("filter_type", "effect_type"),
                    ("filter_params", "effect_params"),
                    ("params_by_filter", "params_by_effect"),
                )
                if canonical in values
            },
        }
        ranges = {
            "size": (0.1, 1000.0),
            "opacity": (0.0, 1.0),
            "hardness": (0.0, 1.0),
            "spacing": (MIN_SPACING, MAX_SPACING),
            "flow": (0.0, 1.0),
            "tone_range": (0.0, 1.0),
            "tone_gamma": (0.1, 2.0),
            "smoothing": (0.0, 1.0),
            "recontour_smoothness": (0.0, 100.0),
        }
        for key, (low, high) in ranges.items():
            if key in values:
                value = float(values[key])
                if math.isfinite(value):
                    setattr(self, key, min(high, max(low, value)))
        if "stamp_density" in values:
            density = float(values["stamp_density"])
            if math.isfinite(density):
                self.spacing = spacing_from_density(density)
        for key in ("pressure_sensitive", "tilt_angle", "recontour"):
            if key in values:
                setattr(self, key, bool(values[key]))
        if isinstance(values.get("effect_type"), str):
            self.filter_type = values["effect_type"]
        if isinstance(values.get("effect_params"), dict):
            self.filter_params = deepcopy(values["effect_params"])
        if isinstance(values.get("params_by_effect"), dict):
            self.params_by_filter = {
                str(key): deepcopy(params)
                for key, params in values["params_by_effect"].items()
                if isinstance(params, dict)
            }
        if "tip_path" in values:
            self.tip_path = str(values["tip_path"]) if values["tip_path"] else None
        if values.get("hardness_mode") in ("tip", "radial"):
            self.hardness_mode = values["hardness_mode"]

    def brush(self) -> BrushSettings:
        return BrushSettings(
            size=self.size,
            opacity=self.opacity,
            hardness=self.hardness,
            spacing=self.spacing,
            flow=self.flow,
            tone_range=self.tone_range,
            tone_gamma=self.tone_gamma,
        )


# Compatibility aliases for existing imports.
EffectPenSettings = FilterPenSettings


_bind_legacy_names(__name__, {"EffectPenSettings": "FilterPenSettings"})
