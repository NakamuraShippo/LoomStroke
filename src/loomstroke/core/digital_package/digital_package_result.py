"""Outcome of one digital package export (CT-26)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .package_issue import PackageIssue

__all__ = ["DigitalPackageResult"]


@dataclass(frozen=True, slots=True)
class DigitalPackageResult:
    """``external_validation`` stays ``not_checked`` unless EPUBCheck actually ran."""

    status: Literal["success", "failed", "cancelled"]
    format_id: str
    destination: Path | None
    item_count: int = 0
    byte_count: int = 0
    issues: tuple[PackageIssue, ...] = field(default_factory=tuple)
    internal_validation: Literal["passed", "failed", "not_checked"] = "not_checked"
    external_validation: Literal["passed", "failed", "not_checked"] = "not_checked"
    external_summary: str = ""

    @property
    def fatal_issues(self) -> tuple[PackageIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "fatal")

    @property
    def file_name(self) -> str:
        return self.destination.name if self.destination is not None else ""
