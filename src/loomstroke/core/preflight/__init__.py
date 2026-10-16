"""Manga preflight (CT-16, spec 16) — pure, Qt-free model, rules and service.

One class per module:

- :mod:`preflight_issue` / :mod:`rule_outcome` / :mod:`capability_missing`
- :mod:`preflight_profile` — :class:`MangaPreflightProfile` (built-ins)
- :mod:`layer_preflight_snapshot` / :mod:`text_preflight_snapshot` /
  :mod:`page_preflight_snapshot` / :mod:`document_preflight_snapshot`
- :mod:`preflight_fingerprint` / :mod:`preflight_report`
- :mod:`preflight_rule` (base) + :mod:`rules` (one rule per module)
- :mod:`preflight_rule_registry` / :mod:`manga_preflight_service`
- :mod:`guard_decision` / :mod:`preflight_export_guard`
"""

from .capability_missing import CapabilityMissing
from .document_preflight_snapshot import DocumentPreflightSnapshot
from .guard_decision import GuardDecision
from .layer_preflight_snapshot import LayerPreflightSnapshot
from .manga_preflight_service import MangaPreflightService
from .page_preflight_snapshot import PagePreflightSnapshot
from .preflight_export_guard import PreflightExportGuard
from .preflight_fingerprint import PreflightFingerprint
from .preflight_issue import PreflightIssue
from .preflight_profile import MangaPreflightProfile
from .preflight_report import PreflightReport
from .preflight_rule import PreflightRule
from .preflight_rule_registry import PreflightRuleRegistry
from .rule_outcome import RuleOutcome
from .text_preflight_snapshot import TextPreflightSnapshot

__all__ = [
    "CapabilityMissing",
    "DocumentPreflightSnapshot",
    "GuardDecision",
    "LayerPreflightSnapshot",
    "MangaPreflightProfile",
    "MangaPreflightService",
    "PagePreflightSnapshot",
    "PreflightExportGuard",
    "PreflightFingerprint",
    "PreflightIssue",
    "PreflightReport",
    "PreflightRule",
    "PreflightRuleRegistry",
    "RuleOutcome",
    "TextPreflightSnapshot",
]
