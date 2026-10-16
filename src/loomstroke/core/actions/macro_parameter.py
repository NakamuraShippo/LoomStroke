"""User-facing parameter declared by a macro and bound at run time."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .action_parameter import ParameterKind

__all__ = ["MacroParameter"]

_KINDS = ("str", "int", "float", "bool", "enum", "path")


@dataclass(frozen=True, slots=True)
class MacroParameter:
    name: str
    kind: ParameterKind = "str"
    default: Any = None
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "default": self.default,
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: Any) -> MacroParameter | None:
        if not isinstance(data, dict):
            return None
        name = data.get("name")
        kind = data.get("kind", "str")
        if not isinstance(name, str) or not name or kind not in _KINDS:
            return None
        default = data.get("default")
        if default is not None and not isinstance(default, (str, int, float, bool)):
            return None
        return cls(name, kind, default, str(data.get("description") or ""))
