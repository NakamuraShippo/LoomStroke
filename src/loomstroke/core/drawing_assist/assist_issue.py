"""Validation finding for one drawing assist (stable code + field)."""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["AssistIssue"]


@dataclass(frozen=True, slots=True)
class AssistIssue:
    code: str
    field: str = ""
    message: str = ""
