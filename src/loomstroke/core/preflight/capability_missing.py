"""Raised by a rule that cannot evaluate in this build / snapshot (CT-16 §14).

The service turns it into a ``not_checked`` outcome with the given stable
reason code instead of a pass.
"""

from __future__ import annotations

from .preflight_issue import PreflightIssue

__all__ = ["CapabilityMissing"]


class CapabilityMissing(Exception):
    def __init__(self, reason_code: str, *, issues: tuple[PreflightIssue, ...] = ()) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.issues = issues
