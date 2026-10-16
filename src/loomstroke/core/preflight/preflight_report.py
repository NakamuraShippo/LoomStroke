"""Result of one preflight scan (CT-16 spec 16 §8.1 / §14 / §16)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .preflight_issue import PreflightIssue

__all__ = ["PreflightReport"]


@dataclass(frozen=True, slots=True)
class PreflightReport:
    """``not_checked`` lists ``(rule_id, reason_code)`` — never shown as pass."""

    report_id: str
    profile_id: str
    profile_version: int
    document_id: str
    fingerprint: str
    structure_fingerprint: str
    dirty_signature: str
    created_at: str
    issues: tuple[PreflightIssue, ...] = field(default_factory=tuple)
    checked: tuple[str, ...] = field(default_factory=tuple)
    not_checked: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    page_ids: tuple[str, ...] = field(default_factory=tuple)

    def count(self, severity: str) -> int:
        return sum(1 for issue in self.issues if issue.severity == severity)

    @property
    def fatal_count(self) -> int:
        return self.count("fatal")

    @property
    def warning_count(self) -> int:
        return self.count("warning")

    @property
    def info_count(self) -> int:
        return self.count("info")

    @property
    def has_fatal(self) -> bool:
        return self.fatal_count > 0

    def issues_for_page(self, page_id: str | None) -> tuple[PreflightIssue, ...]:
        return tuple(issue for issue in self.issues if issue.page_id == page_id)

    def is_current(self, *, structure_fingerprint: str, dirty_signature: str) -> bool:
        """True when the document still looks like the scanned one (spec 16 §14)."""
        return (
            structure_fingerprint == self.structure_fingerprint
            and dirty_signature == self.dirty_signature
        )
