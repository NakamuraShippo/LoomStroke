"""Builds and resolves locators with the path guards of spec 18 §10 / §13.

Relative locators are normalised posix paths below the document directory;
``..``, drive prefixes, rooted paths and symlinked components are rejected.
"""

from __future__ import annotations

import re
from pathlib import Path, PurePosixPath
from typing import ClassVar

from .resource_locator import ResourceLocator

__all__ = ["ResourceLocatorResolver"]


class ResourceLocatorResolver:
    _DRIVE: ClassVar[re.Pattern[str]] = re.compile(r"^[A-Za-z]:")

    def __init__(self, document_dir: Path | None) -> None:
        self._document_dir = Path(document_dir).absolute() if document_dir is not None else None

    @property
    def document_dir(self) -> Path | None:
        return self._document_dir

    def locator_for(self, path: Path | str) -> ResourceLocator:
        """Portable document-relative locator when possible, else a local absolute one."""
        absolute = Path(path).expanduser().absolute()
        if self._document_dir is not None:
            try:
                relative = absolute.relative_to(self._document_dir)
            except ValueError:
                relative = None
            if (
                relative is not None
                and relative.parts
                and not self.has_symlink(self._document_dir, relative)
            ):
                return ResourceLocator("document", relative.as_posix())
        return ResourceLocator("absolute", str(absolute))

    def resolve(self, locator: ResourceLocator) -> Path | None:
        if locator.base == "absolute":
            return Path(locator.path)
        relative = self.validate_relative(locator.path)
        if relative is None or self._document_dir is None:
            return None
        return self._document_dir.joinpath(*PurePosixPath(relative).parts)

    @classmethod
    def validate_relative(cls, text: object) -> str | None:
        """Normalised posix relative path, or ``None`` when it could escape its base."""
        if not isinstance(text, str) or not text or "\x00" in text:
            return None
        candidate = text.replace("\\", "/")
        if candidate.startswith("/") or cls._DRIVE.match(candidate):
            return None
        parts = [part for part in candidate.split("/") if part not in ("", ".")]
        if not parts or any(part == ".." or part.endswith(":") for part in parts):
            return None
        return "/".join(parts)

    @staticmethod
    def has_symlink(base: Path, relative: Path) -> bool:
        current = Path(base)
        for part in relative.parts:
            current = current / part
            if current.is_symlink():
                return True
        return False

    @staticmethod
    def is_symlink(path: Path) -> bool:
        try:
            return Path(path).is_symlink()
        except OSError:
            return False
