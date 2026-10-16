"""One preflight finding (CT-16 spec 16 §8.1).

Stable ``reason_code`` + ``message_args`` — the GUI translates from the
code, never from free text; ``issue_id`` is derived deterministically from
the rule, the target ids and the args, so the same snapshot and profile
always yield the same ids (spec 16 §16).  No document text, paths or
pixels are ever stored on an issue (spec 16 §13).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

__all__ = ["IssueSeverity", "PreflightIssue"]

IssueSeverity = Literal["fatal", "warning", "info"]


@dataclass(frozen=True, slots=True)
class PreflightIssue:
    """``message_args`` is a sorted tuple of ``(key, value)`` string pairs."""

    rule_id: str
    category: str
    severity: IssueSeverity
    reason_code: str
    page_id: str | None = None
    layer_id: str | None = None
    object_id: str | None = None
    resource_id: str | None = None
    bounds: tuple[float, float, float, float] | None = None
    message_args: tuple[tuple[str, str], ...] = ()
    recommended_actions: tuple[str, ...] = ()

    @property
    def issue_id(self) -> str:
        material = "|".join(
            (
                self.rule_id,
                self.reason_code,
                self.page_id or "",
                self.layer_id or "",
                self.object_id or "",
                self.resource_id or "",
                ";".join(f"{key}={value}" for key, value in self.message_args),
            )
        )
        return hashlib.sha1(material.encode("utf-8")).hexdigest()[:16]  # noqa: S324

    def args(self) -> dict[str, str]:
        return dict(self.message_args)

    @staticmethod
    def pack_args(**values: object) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((str(key), str(value)) for key, value in values.items()))
