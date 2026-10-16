"""Base class for preflight rules (CT-16 §7 — pure, Qt-free).

A rule reads the immutable snapshot and the profile and returns issues.
``issue()`` applies the profile's severity override to the rule's default
severity (explicit per-issue severities are kept).  Rules that cannot evaluate raise
:class:`CapabilityMissing`; any other exception is turned into a
``not_checked`` outcome by the service (spec 16 §14).
"""

from __future__ import annotations

from typing import Any, ClassVar

from .document_preflight_snapshot import DocumentPreflightSnapshot
from .preflight_issue import IssueSeverity, PreflightIssue
from .preflight_profile import MangaPreflightProfile

__all__ = ["PreflightRule"]


class PreflightRule:
    """Subclasses set the class attributes and implement :meth:`evaluate`."""

    rule_id: ClassVar[str] = ""
    category: ClassVar[str] = ""
    default_severity: ClassVar[IssueSeverity] = "warning"

    def evaluate(
        self, snapshot: DocumentPreflightSnapshot, profile: MangaPreflightProfile
    ) -> tuple[PreflightIssue, ...]:
        raise NotImplementedError

    def issue(
        self,
        profile: MangaPreflightProfile,
        reason_code: str,
        *,
        page_id: str | None = None,
        layer_id: str | None = None,
        object_id: str | None = None,
        resource_id: str | None = None,
        bounds: tuple[float, float, float, float] | None = None,
        severity: IssueSeverity | None = None,
        recommended_actions: tuple[str, ...] = (),
        **args: Any,
    ) -> PreflightIssue:
        # A profile override re-grades the rule's *default* severity only;
        # an explicit per-issue severity (hard errors, side notes) stays.
        resolved = (
            severity
            if severity is not None
            else profile.severity_for(self.rule_id, self.default_severity)
        )
        if resolved not in {"fatal", "warning", "info"}:
            resolved = self.default_severity
        return PreflightIssue(
            rule_id=self.rule_id,
            category=self.category,
            severity=resolved,  # type: ignore[arg-type]
            reason_code=reason_code,
            page_id=page_id,
            layer_id=layer_id,
            object_id=object_id,
            resource_id=resource_id,
            bounds=bounds,
            message_args=PreflightIssue.pack_args(**args),
            recommended_actions=recommended_actions,
        )
