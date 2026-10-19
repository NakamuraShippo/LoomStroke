"""One typed parameter of a fill / style / adjustment kind (CT-21 validation + defaults)."""

from __future__ import annotations

import math
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from .relief_style_parameters import clean_lights, clean_profile

__all__ = ["AppearanceParam", "ParamKind"]

ParamKind = Literal["float", "int", "bool", "color", "enum", "point", "lights", "curve"]


@dataclass(frozen=True, slots=True)
class AppearanceParam:
    key: str
    kind: ParamKind
    default: Any
    label: str = ""
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] = ()
    step: float | None = None

    def coerce(self, value: Any) -> tuple[Any, str | None]:
        """Return ``(clean_value, issue_code)``; a non-None code means the default was used."""
        if value is None:
            return self.default, None
        if self.kind == "bool":
            return (
                (bool(value), None)
                if isinstance(value, (bool, int))
                else (self.default, "param_type")
            )
        if self.kind in ("float", "int"):
            return self._number(value)
        if self.kind == "enum":
            return (value, None) if value in self.choices else (self.default, "param_choice")
        if self.kind == "color":
            return self._color(value)
        return self._structured(value)

    def _structured(self, value: Any) -> tuple[Any, str | None]:
        if self.kind == "point":
            return self._point(value)
        try:
            clean = clean_lights(value) if self.kind == "lights" else clean_profile(value)
            return clean, None
        except (ValueError, TypeError, OverflowError):
            return deepcopy(self.default), "param_type"

    def _number(self, value: Any) -> tuple[Any, str | None]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return self.default, "param_type"
        number = float(value)
        if not math.isfinite(number):
            return self.default, "param_not_finite"
        code = None
        if self.minimum is not None and number < self.minimum:
            number, code = float(self.minimum), "param_clamped"
        if self.maximum is not None and number > self.maximum:
            number, code = float(self.maximum), "param_clamped"
        return (round(number) if self.kind == "int" else number), code

    def _color(self, value: Any) -> tuple[Any, str | None]:
        if not isinstance(value, (list, tuple)) or len(value) not in (3, 4):
            return self.default, "param_type"
        channels: list[int] = []
        for channel in value:
            if isinstance(channel, bool) or not isinstance(channel, (int, float)):
                return self.default, "param_type"
            if not math.isfinite(float(channel)):
                return self.default, "param_not_finite"
            channels.append(max(0, min(255, round(float(channel)))))
        if len(channels) == 3:
            channels.append(255)
        return channels, None

    def _point(self, value: Any) -> tuple[Any, str | None]:
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            return self.default, "param_type"
        try:
            x, y = float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return self.default, "param_type"
        if not (math.isfinite(x) and math.isfinite(y)):
            return self.default, "param_not_finite"
        return [x, y], None
