"""Pure rule evaluation over an immutable snapshot (CT-16 §7 / §14 / §16).

Deterministic: rules run in profile order, issues are sorted by page
order, rule id and issue id, so the same snapshot + profile yield the
same report (only ``report_id`` / ``created_at`` differ and both are
injectable).  A rule that raises :class:`CapabilityMissing` or any other
exception becomes ``not_checked`` with a sanitized reason code — never a
pass.  Cancel is polled between rules.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import UTC, datetime

from .capability_missing import CapabilityMissing
from .document_preflight_snapshot import DocumentPreflightSnapshot
from .preflight_fingerprint import PreflightFingerprint
from .preflight_issue import PreflightIssue
from .preflight_profile import MangaPreflightProfile
from .preflight_report import PreflightReport
from .preflight_rule_registry import PreflightRuleRegistry
from .rule_outcome import RuleOutcome

logger = logging.getLogger(__name__)

__all__ = ["MangaPreflightService"]

Cancel = Callable[[], bool]
Progress = Callable[[int, int, str], None]


class MangaPreflightService:
    """Run a profile's rules over a snapshot."""

    CANCELLED: str = "scan_cancelled"
    RULE_ERROR: str = "rule_error"
    MISSING: str = "profile_capability_missing"

    def __init__(
        self,
        registry: PreflightRuleRegistry | None = None,
        *,
        clock: Callable[[], str] | None = None,
    ) -> None:
        self._registry = registry or PreflightRuleRegistry()
        self._clock = clock or (lambda: datetime.now(UTC).isoformat())

    def run(
        self,
        snapshot: DocumentPreflightSnapshot,
        profile: MangaPreflightProfile,
        *,
        cancel: Cancel | None = None,
        on_progress: Progress | None = None,
    ) -> PreflightReport:
        outcomes = self.evaluate(snapshot, profile, cancel=cancel, on_progress=on_progress)
        issues = self._sorted_issues(snapshot, outcomes)
        fingerprint = PreflightFingerprint.content(snapshot)
        structure = PreflightFingerprint.structure(snapshot)
        created = self._clock()
        report_id = hashlib.sha1(  # noqa: S324
            f"{fingerprint}|{profile.profile_id}|{profile.version}|{created}".encode()
        ).hexdigest()[:16]
        return PreflightReport(
            report_id=report_id,
            profile_id=profile.profile_id,
            profile_version=profile.version,
            document_id=snapshot.document_id,
            fingerprint=fingerprint,
            structure_fingerprint=structure,
            dirty_signature=snapshot.dirty_signature,
            created_at=created,
            issues=issues,
            checked=tuple(o.rule_id for o in outcomes if o.status == "checked"),
            not_checked=tuple(
                (o.rule_id, o.reason_code or self.RULE_ERROR)
                for o in outcomes
                if o.status == "not_checked"
            ),
            page_ids=snapshot.page_ids,
        )

    def evaluate(
        self,
        snapshot: DocumentPreflightSnapshot,
        profile: MangaPreflightProfile,
        *,
        cancel: Cancel | None = None,
        on_progress: Progress | None = None,
    ) -> tuple[RuleOutcome, ...]:
        planned = self._registry.rules_for(profile)
        outcomes: list[RuleOutcome] = []
        for index, (rule_id, rule) in enumerate(planned):
            if cancel is not None and cancel():
                outcomes.extend(
                    RuleOutcome(rest_id, "not_checked", reason_code=self.CANCELLED)
                    for rest_id, _rule in planned[index:]
                )
                break
            if on_progress is not None:
                on_progress(index, len(planned), rule_id)
            if rule is None:
                outcomes.append(RuleOutcome(rule_id, "not_checked", reason_code=self.MISSING))
                continue
            try:
                issues = tuple(rule.evaluate(snapshot, profile))
            except CapabilityMissing as missing:
                outcomes.append(
                    RuleOutcome(
                        rule_id,
                        "not_checked",
                        issues=getattr(missing, "issues", ()),
                        reason_code=missing.reason_code,
                    )
                )
            except Exception:
                logger.warning("preflight rule %s failed", rule_id, exc_info=True)
                outcomes.append(RuleOutcome(rule_id, "not_checked", reason_code=self.RULE_ERROR))
            else:
                outcomes.append(RuleOutcome(rule_id, "checked", issues))
        if on_progress is not None:
            on_progress(len(planned), len(planned), "")
        return tuple(outcomes)

    @staticmethod
    def _sorted_issues(
        snapshot: DocumentPreflightSnapshot, outcomes: tuple[RuleOutcome, ...]
    ) -> tuple[PreflightIssue, ...]:
        order = {page_id: index for index, page_id in enumerate(snapshot.page_ids)}
        issues = [issue for outcome in outcomes for issue in outcome.issues]
        issues.sort(
            key=lambda issue: (
                order.get(issue.page_id or "", -1),
                issue.rule_id,
                issue.layer_id or "",
                issue.object_id or "",
                issue.issue_id,
            )
        )
        return tuple(issues)
