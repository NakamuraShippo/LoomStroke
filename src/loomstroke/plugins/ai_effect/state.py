"""AI Effect onboarding / transient state (state.json).

Spec reference: ``AIeffect_design/04_python_design/onboarding.md`` §10.1
and ``settings.md`` §5.

This file is intentionally separated from preferences.json because the
lifecycle is different: state.json holds progress flags that are reset on
fresh installs / account migrations, whereas preferences.json carries
persistent user choices.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from loomstroke.plugins.ai_effect._atomic_io import atomic_write_json

logger = logging.getLogger(__name__)


CURRENT_STATE_SCHEMA_VERSION = 1


@dataclass
class OnboardingSection:
    """Which one-time dialogs have already fired."""

    gateway_setup_shown_for: list[str] = field(default_factory=list)
    first_gen_done: bool = False
    preset_manager_visited: bool = False


@dataclass
class OnboardingState:
    """Root state.json dataclass."""

    schema_version: int = CURRENT_STATE_SCHEMA_VERSION
    onboarding: OnboardingSection = field(default_factory=OnboardingSection)
    last_selected_gateway: str | None = None

    @classmethod
    def load(cls, path: Path) -> OnboardingState:
        if not path.exists():
            return cls()

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Failed to load AI Effect state from %s: %s", path, exc)
            return cls()

        return _from_dict(raw)

    def save(self, path: Path) -> None:
        """Atomic JSON write with fsync (see ``_atomic_io.atomic_write_json``)."""
        atomic_write_json(path, asdict(self))

    # ------------------------------------------------------------------
    # Convenience mutators (keep call sites tidy)
    # ------------------------------------------------------------------

    def mark_gateway_setup_shown(self, gateway_id: str) -> bool:
        """Record that onboarding ran for this gateway. Returns True if newly added."""
        if gateway_id in self.onboarding.gateway_setup_shown_for:
            return False
        self.onboarding.gateway_setup_shown_for.append(gateway_id)
        return True

    def mark_first_gen_done(self) -> None:
        self.onboarding.first_gen_done = True


def _from_dict(raw: dict[str, Any]) -> OnboardingState:
    onb_raw = raw.get("onboarding", {}) or {}
    onb = OnboardingSection(
        gateway_setup_shown_for=list(onb_raw.get("gateway_setup_shown_for", [])),
        first_gen_done=bool(onb_raw.get("first_gen_done", False)),
        preset_manager_visited=bool(onb_raw.get("preset_manager_visited", False)),
    )
    last = raw.get("last_selected_gateway")
    return OnboardingState(
        schema_version=int(raw.get("schema_version", CURRENT_STATE_SCHEMA_VERSION)),
        onboarding=onb,
        last_selected_gateway=str(last) if isinstance(last, str) else None,
    )
