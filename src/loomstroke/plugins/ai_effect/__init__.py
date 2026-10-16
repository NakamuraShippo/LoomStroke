"""AI Effect Layer plugin package.

Implementation of the AI Effect Layer feature per
``AIeffect_design/04_python_design/`` specification suite (10 design docs +
implementation_roadmap.md, confirmed 2026-04-23).

Phase 1 (Foundation) — exports the abstract types and error taxonomy used by
all subsequent phases. No Gateway implementations ship in this phase; see
``tests/support/fake_gateway.py`` for the test-only FakeGateway.
"""

from __future__ import annotations

from loomstroke.plugins.ai_effect.errors import (
    AIError,
    AIGatewayError,
    ErrorClass,
)

__all__ = [
    "AIError",
    "AIGatewayError",
    "ErrorClass",
]
