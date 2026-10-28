"""What to do with destinations that already exist (CT-10 spec §5).

``ask`` / ``replace`` keep the destination (the runner confirms ``ask``
once with the count), ``skip`` drops items whose file exists, ``suffix``
renames them ``name_001.ext`` … picking the first free number that is
also unused inside the batch.  Existing files are never touched here —
the decision is made before any write (spec §13).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar


@dataclass(frozen=True, slots=True)
class CollisionDecision:
    """``destination`` is ``None`` when the item is skipped; ``renamed`` marks suffixing."""

    destination: Path | None
    existed: bool
    renamed: bool = False


class ExportCollisionPolicy:
    """Resolve a batch of destinations against the file system."""

    POLICIES: ClassVar[tuple[str, ...]] = ("ask", "replace", "skip", "suffix")
    MAX_SUFFIX: ClassVar[int] = 9999

    def __init__(self, exists: Callable[[Path], bool] | None = None) -> None:
        self._exists = exists or (lambda path: path.exists())

    @classmethod
    def normalize(cls, policy: str | None) -> str:
        candidate = str(policy or "ask").strip().lower()
        return candidate if candidate in cls.POLICIES else "ask"

    def resolve(self, destinations: Sequence[Path], policy: str) -> list[CollisionDecision]:
        mode = self.normalize(policy)
        taken = {str(path).casefold() for path in destinations}
        decisions: list[CollisionDecision] = []
        for destination in destinations:
            existed = bool(self._exists(destination))
            if not existed or mode in {"ask", "replace"}:
                decisions.append(CollisionDecision(destination, existed))
                continue
            if mode == "skip":
                decisions.append(CollisionDecision(None, existed))
                continue
            renamed = self._free_suffix(destination, taken)
            taken.add(str(renamed).casefold())
            decisions.append(CollisionDecision(renamed, existed, renamed=True))
        return decisions

    def _free_suffix(self, destination: Path, taken: set[str]) -> Path:
        for number in range(1, self.MAX_SUFFIX + 1):
            candidate = destination.with_name(
                f"{destination.stem}_{number:03d}{destination.suffix}"
            )
            key = str(candidate).casefold()
            if key not in taken and not self._exists(candidate):
                return candidate
        raise RuntimeError(f"no free suffix for {destination.name}")
