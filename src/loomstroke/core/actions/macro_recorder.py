"""Turns successful catalog actions into macro steps while recording (CT-20 §6.1).

Only ``recordable`` descriptors become steps; everything else is counted as
dropped so the UI can say what was not captured.  Recording never touches the
document.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from .action_catalog import ActionCatalog
from .macro_definition import MacroDefinition
from .macro_step import MacroStep
from .target_selector import TargetSelector

__all__ = ["MacroRecorder"]


class MacroRecorder:
    def __init__(self, catalog: ActionCatalog) -> None:
        self._catalog = catalog
        self._steps: list[MacroStep] = []
        self._name = ""
        self._recording = False
        self._dropped = 0

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def step_count(self) -> int:
        return len(self._steps)

    @property
    def dropped_count(self) -> int:
        return self._dropped

    def start(self, name: str = "") -> None:
        self._steps = []
        self._dropped = 0
        self._name = name
        self._recording = True

    def record(
        self, action_id: str, params: Mapping[str, Any], target: TargetSelector
    ) -> MacroStep | None:
        if not self._recording:
            return None
        descriptor = self._catalog.get(action_id)
        if descriptor is None or not descriptor.recordable:
            self._dropped += 1
            return None
        step = MacroStep(
            step_id=f"s{len(self._steps) + 1}",
            action_id=action_id,
            action_version=descriptor.version,
            bindings=dict(params),
            target=target,
        )
        self._steps.append(step)
        return step

    def stop(self, *, app_version: str = "") -> MacroDefinition | None:
        if not self._recording:
            return None
        self._recording = False
        if not self._steps:
            return None
        macro_id = "macro_" + uuid.uuid4().hex[:12]
        return MacroDefinition(
            macro_id=macro_id,
            name=self._name or macro_id,
            steps=tuple(self._steps),
            created_app_version=app_version,
        )

    def discard(self) -> None:
        self._recording = False
        self._steps = []
        self._dropped = 0
