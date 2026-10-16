"""Versioned declarative macro (CT-20 §8.1 ``MacroDefinition``); JSON only, no code.

``from_dict`` is strict: forbidden keys anywhere in the payload (``code``,
``script``, ``eval`` ...) raise ``MacroFormatError`` instead of being ignored.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, ClassVar, Literal

from .macro_format_error import MacroFormatError
from .macro_parameter import MacroParameter
from .macro_step import MacroStep

__all__ = ["MacroDefinition", "UndoGrouping"]

UndoGrouping = Literal["page", "step"]


@dataclass(frozen=True, slots=True)
class MacroDefinition:
    macro_id: str
    name: str
    steps: tuple[MacroStep, ...] = ()
    parameters: tuple[MacroParameter, ...] = ()
    description: str = ""
    tags: tuple[str, ...] = ()
    created_app_version: str = ""
    undo_grouping: UndoGrouping = "page"
    schema_version: int = field(default=1)

    SCHEMA: ClassVar[str] = "loomstroke.macro"
    SCHEMA_VERSION: ClassVar[int] = 1
    MAX_STEPS: ClassVar[int] = 500
    MAX_DEPTH: ClassVar[int] = 8
    FORBIDDEN_KEYS: ClassVar[frozenset[str]] = frozenset(
        {"code", "script", "eval", "exec", "module", "shell", "python", "import", "__class__"}
    )

    def parameter(self, name: str) -> MacroParameter | None:
        return next((p for p in self.parameters if p.name == name), None)

    def enabled_steps(self) -> tuple[MacroStep, ...]:
        return tuple(step for step in self.steps if step.enabled)

    def with_steps(self, steps: tuple[MacroStep, ...]) -> MacroDefinition:
        return replace(self, steps=tuple(steps))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "schema_version": self.SCHEMA_VERSION,
            "macro_id": self.macro_id,
            "name": self.name,
            "description": self.description,
            "tags": list(self.tags),
            "created_app_version": self.created_app_version,
            "undo_grouping": self.undo_grouping,
            "parameters": [parameter.to_dict() for parameter in self.parameters],
            "steps": [step.to_dict() for step in self.steps],
        }

    @classmethod
    def from_dict(cls, data: Any) -> MacroDefinition:
        if not isinstance(data, dict):
            raise MacroFormatError("macro_not_object")
        cls._scan_forbidden(data, depth=0)
        if data.get("schema", cls.SCHEMA) != cls.SCHEMA:
            raise MacroFormatError("macro_schema_unknown")
        version = data.get("schema_version", 1)
        if not isinstance(version, int) or version < 1 or version > cls.SCHEMA_VERSION:
            raise MacroFormatError("macro_schema_version_unsupported")
        macro_id = data.get("macro_id")
        name = data.get("name")
        if not isinstance(macro_id, str) or not macro_id or not isinstance(name, str):
            raise MacroFormatError("macro_identity_invalid")
        raw_steps = data.get("steps", [])
        if not isinstance(raw_steps, list) or len(raw_steps) > cls.MAX_STEPS:
            raise MacroFormatError("macro_steps_invalid")
        steps: list[MacroStep] = []
        for raw in raw_steps:
            step = MacroStep.from_dict(raw)
            if step is None:
                raise MacroFormatError("macro_step_invalid")
            steps.append(step)
        raw_params = data.get("parameters", [])
        if not isinstance(raw_params, list):
            raise MacroFormatError("macro_parameters_invalid")
        parameters: list[MacroParameter] = []
        for raw in raw_params:
            parameter = MacroParameter.from_dict(raw)
            if parameter is None:
                raise MacroFormatError("macro_parameter_invalid")
            parameters.append(parameter)
        undo_grouping = data.get("undo_grouping", "page")
        if undo_grouping not in ("page", "step"):
            raise MacroFormatError("macro_undo_grouping_invalid")
        tags = data.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(t, str) for t in tags):
            raise MacroFormatError("macro_tags_invalid")
        return cls(
            macro_id=macro_id,
            name=name,
            steps=tuple(steps),
            parameters=tuple(parameters),
            description=str(data.get("description") or ""),
            tags=tuple(tags),
            created_app_version=str(data.get("created_app_version") or ""),
            undo_grouping=undo_grouping,
            schema_version=version,
        )

    @classmethod
    def _scan_forbidden(cls, node: Any, *, depth: int) -> None:
        if depth > cls.MAX_DEPTH:
            raise MacroFormatError("macro_nesting_too_deep")
        if isinstance(node, dict):
            for key, value in node.items():
                if isinstance(key, str) and key.lower() in cls.FORBIDDEN_KEYS:
                    raise MacroFormatError("macro_forbidden_key", key)
                cls._scan_forbidden(value, depth=depth + 1)
        elif isinstance(node, list):
            for value in node:
                cls._scan_forbidden(value, depth=depth + 1)
