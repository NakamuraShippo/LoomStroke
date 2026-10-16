"""Registry of declarative actions and their planner / executor adapters (CT-20 §7)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from .action_context import ActionContext
from .action_descriptor import ActionDescriptor
from .action_outcome import ActionOutcome
from .action_preview import ActionPreview
from .resolved_target import ResolvedTarget

__all__ = ["ActionCatalog", "ActionExecutor", "ActionPlanner"]

ActionExecutor = Callable[[ActionContext, ResolvedTarget, Mapping[str, Any]], ActionOutcome]
ActionPlanner = Callable[[ActionContext, ResolvedTarget, Mapping[str, Any]], ActionPreview]


class ActionCatalog:
    def __init__(self) -> None:
        self._descriptors: dict[str, ActionDescriptor] = {}
        self._executors: dict[str, ActionExecutor] = {}
        self._planners: dict[str, ActionPlanner] = {}

    def register(
        self,
        descriptor: ActionDescriptor,
        executor: ActionExecutor,
        planner: ActionPlanner | None = None,
        *,
        replace: bool = False,
    ) -> None:
        if descriptor.action_id in self._descriptors and not replace:
            raise ValueError(f"action_id_collision:{descriptor.action_id}")
        self._descriptors[descriptor.action_id] = descriptor
        self._executors[descriptor.action_id] = executor
        if planner is not None:
            self._planners[descriptor.action_id] = planner
        else:
            self._planners.pop(descriptor.action_id, None)

    def get(self, action_id: str) -> ActionDescriptor | None:
        return self._descriptors.get(action_id)

    def executor(self, action_id: str) -> ActionExecutor | None:
        return self._executors.get(action_id)

    def planner(self, action_id: str) -> ActionPlanner | None:
        return self._planners.get(action_id)

    def descriptors(self) -> tuple[ActionDescriptor, ...]:
        return tuple(sorted(self._descriptors.values(), key=lambda d: (d.category, d.action_id)))

    def supports(self, action_id: str, version: int) -> str | None:
        """``None`` when usable, else ``action_unavailable`` / ``action_version_unsupported``."""
        descriptor = self._descriptors.get(action_id)
        if descriptor is None:
            return "action_unavailable"
        if descriptor.version != version:
            return "action_version_unsupported"
        return None

    def __contains__(self, action_id: object) -> bool:
        return action_id in self._descriptors

    def __len__(self) -> int:
        return len(self._descriptors)
