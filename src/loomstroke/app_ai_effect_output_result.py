"""Observed canvas application results for AI Effect generation responses."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class AIEffectOutputApplication:
    expected_count: int
    applied_count: int = 0
    failed_count: int = 0

    @property
    def complete(self) -> bool:
        return (
            self.expected_count > 0
            and self.applied_count == self.expected_count
            and self.failed_count == 0
        )

    def attempt(self, callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Observe the existing canvas return contract without discarding assets."""
        try:
            result = callback(*args, **kwargs)
        except Exception:
            logger.exception("AI Effect generated output application failed")
            self.failed_count += 1
            return None
        if result:
            self.applied_count += 1
        else:
            self.failed_count += 1
        return result

    def update_payload(self, payload: dict[str, Any]) -> None:
        """Report generation success separately from actual canvas mutation."""
        payload["appliedToCanvas"] = self.applied_count > 0
        payload["canvasMutation"] = (
            "applied_outputs"
            if self.complete
            else ("partial_outputs" if self.applied_count else "none")
        )
        payload["canvasApplication"] = {
            "expectedOutputCount": self.expected_count,
            "appliedOutputCount": self.applied_count,
            "failedOutputCount": self.failed_count,
        }
        if not self.complete:
            payload["status"] = "failed"
            payload["error"] = {
                "class": "BACKEND",
                "code": "canvas_apply_failed",
                "title": "Generated outputs could not be fully applied",
                "detail": "Generated assets remain available; check the target layer and retry.",
            }


def observed_output_application(result: Any, outcome: Any) -> AIEffectOutputApplication:
    """Respect app overrides that explicitly report success or failure."""
    if isinstance(result, AIEffectOutputApplication):
        return result
    images = getattr(getattr(outcome, "result", None), "output_images", None) or []
    count = len(images)
    return AIEffectOutputApplication(
        expected_count=count,
        applied_count=count if result is True else 0,
        failed_count=0 if result is True else count,
    )
