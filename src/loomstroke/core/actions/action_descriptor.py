"""Declarative description of one recordable action (CT-20 §8.1 ``ActionDescriptor``)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from .action_effect import ActionEffect
from .action_parameter import ActionParameter

__all__ = ["ActionDescriptor", "TargetKind"]

TargetKind = Literal["layer", "page", "document", "none"]


@dataclass(frozen=True, slots=True)
class ActionDescriptor:
    action_id: str
    version: int
    title: str
    category: str = "general"
    parameters: tuple[ActionParameter, ...] = ()
    target_kind: TargetKind = "none"
    effects: tuple[ActionEffect, ...] = (ActionEffect("mutation"),)
    recordable: bool = True
    description: str = ""

    def parameter(self, name: str) -> ActionParameter | None:
        return next((p for p in self.parameters if p.name == name), None)

    def resolve_parameters(
        self, values: Mapping[str, Any]
    ) -> tuple[dict[str, Any], tuple[tuple[str, str], ...]]:
        """Coerce *values* against the schema -> ``(params, ((name, error_code), ...))``."""
        params: dict[str, Any] = {}
        errors: list[tuple[str, str]] = []
        for parameter in self.parameters:
            coerced, error = parameter.coerce(values.get(parameter.name))
            if error is not None:
                errors.append((parameter.name, error))
            else:
                params[parameter.name] = coerced
        for name in values:
            if self.parameter(name) is None:
                errors.append((name, "binding_unknown_name"))
        return params, tuple(errors)

    @property
    def has_file_write(self) -> bool:
        return any(effect.kind == "file_write" for effect in self.effects)

    @property
    def has_external(self) -> bool:
        return any(effect.kind == "external" for effect in self.effects)

    @property
    def mutates(self) -> bool:
        return any(effect.kind == "mutation" for effect in self.effects)

    @property
    def requires_confirmation(self) -> bool:
        return any(effect.confirmation_required for effect in self.effects)
