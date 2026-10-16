"""A registered fill / style / adjustment kind: stable id, family, parameter schema."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .appearance_issue import AppearanceValidationIssue
from .appearance_param import AppearanceParam

__all__ = ["AppearanceFamily", "AppearanceKind"]

AppearanceFamily = Literal["adjustment", "fill", "style"]


@dataclass(frozen=True, slots=True)
class AppearanceKind:
    kind_id: str
    family: AppearanceFamily
    title: str
    params: tuple[AppearanceParam, ...] = ()
    category: str = ""

    def param(self, key: str) -> AppearanceParam | None:
        return next((p for p in self.params if p.key == key), None)

    def defaults(self) -> dict[str, Any]:
        return {p.key: p.default for p in self.params}

    def validate(
        self, params: Mapping[str, Any] | None
    ) -> tuple[dict[str, Any], tuple[AppearanceValidationIssue, ...]]:
        """Clean params (schema keys coerced, unknown keys retained) + issues."""
        source = dict(params or {})
        clean: dict[str, Any] = {}
        issues: list[AppearanceValidationIssue] = []
        for param in self.params:
            value, code = param.coerce(source.pop(param.key, None))
            clean[param.key] = value
            if code is not None:
                severity = "warning" if code == "param_clamped" else "error"
                issues.append(AppearanceValidationIssue(code, param.key, severity))
        for key, value in source.items():
            clean[key] = value  # unknown-field retention (forward compatibility)
            issues.append(AppearanceValidationIssue("param_unknown", key, "warning"))
        return clean, tuple(issues)
