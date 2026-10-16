"""What an import would do, before anything is committed (CT-17 §5.1 / §13)."""

from __future__ import annotations

from dataclasses import dataclass, field

from .script_import_row import ScriptImportRow

__all__ = ["ScriptImportPreview"]


@dataclass(frozen=True, slots=True)
class ScriptImportPreview:
    rows: tuple[ScriptImportRow, ...] = field(default_factory=tuple)
    issues: tuple[str, ...] = ()  # file-level issues (encoding, limits, schema)

    def count(self, action: str) -> int:
        return sum(1 for row in self.rows if row.action == action)

    @property
    def counts(self) -> dict[str, int]:
        return {action: self.count(action) for action in ("add", "update", "unchanged", "invalid")}

    @property
    def applicable_rows(self) -> tuple[ScriptImportRow, ...]:
        return tuple(row for row in self.rows if row.action in ("add", "update"))

    @property
    def blocked(self) -> bool:
        return bool(self.issues)
