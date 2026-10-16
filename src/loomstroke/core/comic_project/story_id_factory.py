"""Stable ids for story objects (CT-17 §3 / §7.1).

Prefix + random hex; injectable so tests get deterministic ids.  Ids are
never derived from titles or text (renames must not change identity).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import ClassVar

__all__ = ["StoryIdFactory"]


class StoryIdFactory:
    PREFIXES: ClassVar[dict[str, str]] = {
        "project": "cp",
        "chapter": "ch",
        "scene": "sc",
        "page": "pg",
        "panel": "pn",
        "dialogue": "dl",
        "task": "tk",
    }

    def __init__(self, token: Callable[[], str] | None = None) -> None:
        self._token = token or (lambda: uuid.uuid4().hex[:10])

    def next_id(self, kind: str) -> str:
        prefix = self.PREFIXES.get(kind)
        if prefix is None:
            raise ValueError(f"unknown story id kind: {kind}")
        return f"{prefix}_{self._token()}"

    @staticmethod
    def sequential(start: int = 1) -> StoryIdFactory:
        """Deterministic factory for tests (``ch_0001`` style tokens)."""
        counter = {"value": start - 1}

        def token() -> str:
            counter["value"] += 1
            return f"{counter['value']:04d}"

        return StoryIdFactory(token)
