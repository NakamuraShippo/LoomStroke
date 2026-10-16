"""One step of a macro: action + bindings + target + error policy (CT-20 §8.1)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from .target_selector import TargetSelector

__all__ = ["MacroStep", "OnError"]

OnError = Literal["stop", "continue", "skip"]
_SCALARS = (str, int, float, bool)
_ON_ERROR = ("stop", "continue", "skip")


def _no_target() -> TargetSelector:
    return TargetSelector("none")


@dataclass(frozen=True, slots=True)
class MacroStep:
    step_id: str
    action_id: str
    action_version: int
    bindings: Mapping[str, Any] = field(default_factory=dict)
    """``{name: literal}`` or ``{name: {"$param": macro_parameter_name}}``."""
    target: TargetSelector = field(default_factory=_no_target)
    on_error: OnError = "stop"
    enabled: bool = True

    PARAM_REF_KEY: ClassVar[str] = "$param"

    @classmethod
    def parameter_ref(cls, value: Any) -> str | None:
        if isinstance(value, Mapping) and set(value) == {cls.PARAM_REF_KEY}:
            name = value[cls.PARAM_REF_KEY]
            return name if isinstance(name, str) and name else None
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "action_id": self.action_id,
            "action_version": int(self.action_version),
            "bindings": {
                key: dict(value) if isinstance(value, Mapping) else value
                for key, value in self.bindings.items()
            },
            "target": self.target.to_dict(),
            "on_error": self.on_error,
            "enabled": bool(self.enabled),
        }

    @classmethod
    def from_dict(cls, data: Any) -> MacroStep | None:
        if not isinstance(data, dict):
            return None
        identity = cls._parse_identity(data)
        bindings = cls._parse_bindings(data.get("bindings", {}))
        target = TargetSelector.from_dict(data.get("target", {"kind": "none"}))
        on_error = data.get("on_error", "stop")
        enabled = data.get("enabled", True)
        if identity is None or bindings is None or target is None:
            return None
        if on_error not in _ON_ERROR or not isinstance(enabled, bool):
            return None
        step_id, action_id, version = identity
        return cls(step_id, action_id, version, bindings, target, on_error, enabled)

    @staticmethod
    def _parse_identity(data: dict[str, Any]) -> tuple[str, str, int] | None:
        step_id = data.get("step_id")
        action_id = data.get("action_id")
        version = data.get("action_version")
        if not all(isinstance(v, str) and v for v in (step_id, action_id)):
            return None
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            return None
        return str(step_id), str(action_id), version

    @classmethod
    def _parse_bindings(cls, raw: Any) -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        bindings: dict[str, Any] = {}
        for key, value in raw.items():
            scalar = value is None or isinstance(value, _SCALARS)
            if not isinstance(key, str) or not (scalar or cls.parameter_ref(value) is not None):
                return None
            bindings[key] = value
        return bindings
