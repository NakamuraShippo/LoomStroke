"""Rule lookup by id (CT-16 §7 ``PreflightRuleRegistry``)."""

from __future__ import annotations

from collections.abc import Iterable

from .preflight_profile import MangaPreflightProfile
from .preflight_rule import PreflightRule
from .rules import BUILTIN_RULES

__all__ = ["PreflightRuleRegistry"]


class PreflightRuleRegistry:
    """Built-in rules plus anything registered at runtime."""

    def __init__(self, rules: Iterable[PreflightRule] | None = None) -> None:
        self._rules: dict[str, PreflightRule] = {}
        for rule in rules if rules is not None else (cls() for cls in BUILTIN_RULES):
            self.register(rule)

    def register(self, rule: PreflightRule) -> None:
        if not rule.rule_id:
            raise ValueError("rule_id is required")
        self._rules[rule.rule_id] = rule

    def rule(self, rule_id: str) -> PreflightRule | None:
        return self._rules.get(rule_id)

    def rule_ids(self) -> tuple[str, ...]:
        return tuple(self._rules)

    def rules_for(
        self, profile: MangaPreflightProfile
    ) -> tuple[tuple[str, PreflightRule | None], ...]:
        """``(rule_id, rule)`` in profile order; ``None`` = capability missing."""
        return tuple((rule_id, self._rules.get(rule_id)) for rule_id in profile.enabled_rules())
