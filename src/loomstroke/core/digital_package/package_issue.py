"""Validation finding for a digital package (CT-26 spec §Package validation)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = ["PackageIssue"]


@dataclass(frozen=True, slots=True)
class PackageIssue:
    """Stable ``code``; ``detail`` is a member path or short fact, never an absolute path."""

    code: str
    severity: Literal["fatal", "warning", "info"] = "fatal"
    detail: str = ""
