"""Private response carried by synchronous Plugin Manager package actions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

_PluginUpdateOutcome = Literal["success", "failed", "cancelled"]


class _PluginUpdateRequest(dict[str, Any]):
    """Keep the event's mapping keys unchanged while returning its outcome."""

    __slots__ = ("outcome",)

    def __init__(self, action: Literal["update", "rollback"] = "update") -> None:
        super().__init__(action=action)
        self.outcome: _PluginUpdateOutcome | None = None

    @staticmethod
    def reply_to(payload: Mapping[str, Any], outcome: _PluginUpdateOutcome) -> None:
        if isinstance(payload, _PluginUpdateRequest):
            payload.outcome = outcome
