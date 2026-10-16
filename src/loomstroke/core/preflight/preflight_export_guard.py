"""Thin export gate over the last report (CT-16 §6 / §7 / §14).

Blocking is *fatal only* by default; warnings never stop an export
silently.  A stale report is not an export permission — it only earns a
reminder to re-scan — and no report at all leaves export unblocked
(preflight is opt-in until a profile says otherwise).
"""

from __future__ import annotations

from .guard_decision import GuardDecision
from .preflight_report import PreflightReport

__all__ = ["PreflightExportGuard"]


class PreflightExportGuard:
    """Decide what an export entry point should do about preflight."""

    def decide(
        self,
        report: PreflightReport | None,
        *,
        structure_fingerprint: str,
        dirty_signature: str,
    ) -> GuardDecision:
        if report is None:
            return GuardDecision("no_report")
        if not report.is_current(
            structure_fingerprint=structure_fingerprint, dirty_signature=dirty_signature
        ):
            return GuardDecision("stale", report.fatal_count, report.warning_count)
        if report.has_fatal:
            return GuardDecision("confirm_fatal", report.fatal_count, report.warning_count)
        return GuardDecision("allow", 0, report.warning_count)
