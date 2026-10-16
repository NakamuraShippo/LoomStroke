"""Where a linked resource lives (CT-18): a portable document-relative path or a local one.

Only the ``document`` base is portable; ``absolute`` locators are kept for local
resolution and never written into package manifests or logs.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

__all__ = ["LocatorBase", "ResourceLocator"]

LocatorBase = Literal["document", "absolute"]


@dataclass(frozen=True, slots=True)
class ResourceLocator:
    base: LocatorBase
    path: str
    """Posix-style relative path for ``document``; native absolute path for ``absolute``."""

    @property
    def is_portable(self) -> bool:
        return self.base == "document"

    @property
    def basename(self) -> str:
        return PurePosixPath(self.path.replace("\\", "/")).name

    def to_dict(self) -> dict[str, Any]:
        return {"base": self.base, "path": self.path}

    @classmethod
    def from_dict(cls, data: Any) -> ResourceLocator | None:
        if not isinstance(data, dict):
            return None
        base = data.get("base")
        path = data.get("path")
        if base not in ("document", "absolute") or not isinstance(path, str) or not path:
            return None
        return cls(base, path)

    def resolve_unchecked(self, document_dir: Path | None) -> Path | None:
        """Join without any safety checks; prefer ``ResourceLocatorResolver.resolve``."""
        if self.base == "absolute":
            return Path(self.path)
        if document_dir is None:
            return None
        return document_dir.joinpath(*PurePosixPath(self.path).parts)
