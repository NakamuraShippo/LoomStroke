"""Typed parameter of a declarative action; coerces and validates bound values."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

__all__ = ["ActionParameter", "ParameterKind"]

ParameterKind = Literal["str", "int", "float", "bool", "enum", "path"]


@dataclass(frozen=True, slots=True)
class ActionParameter:
    name: str
    kind: ParameterKind
    required: bool = False
    default: Any = None
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    description: str = ""

    def coerce(self, value: Any) -> tuple[Any, str | None]:
        """Return ``(coerced_value, error_code)``; ``error_code`` is ``None`` when valid."""
        if value is None:
            if self.required and self.default is None:
                return None, "parameter_missing"
            return self.default, None
        if self.kind in ("int", "float", "bool"):
            coerced, error = self._coerce_number(value)
        else:
            coerced, error = self._coerce_text(value)
        if error is not None:
            return None, error
        return coerced, None

    def _coerce_number(self, value: Any) -> tuple[Any, str | None]:
        if self.kind == "bool":
            return (value, None) if isinstance(value, bool) else (None, "parameter_type")
        allowed: tuple[type, ...] = (int,) if self.kind == "int" else (int, float)
        if isinstance(value, bool) or not isinstance(value, allowed):
            return None, "parameter_type"
        number = int(value) if self.kind == "int" else float(value)
        below = self.minimum is not None and number < self.minimum
        above = self.maximum is not None and number > self.maximum
        if below or above:
            return None, "parameter_range"
        return number, None

    def _coerce_text(self, value: Any) -> tuple[Any, str | None]:
        if not isinstance(value, str):
            return None, "parameter_type"
        if self.kind == "enum" and value not in self.choices:
            return None, "parameter_choice"
        if self.kind == "path" and (not value.strip() or "\x00" in value):
            return None, "parameter_path"
        return value, None
