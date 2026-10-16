"""Atomic local sidecar store (CT-17 §9): ``<root>/comic_projects/<id>/project.json``.

The art document is never touched; a missing sidecar only means "not
linked to a Comic Project".  Saves are atomic (temp + replace) and guard
against stale writers by comparing the stored revision with the caller's
expectation.  Corrupt files raise instead of silently starting over.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
from pathlib import Path
from typing import Any, ClassVar

from loomstroke.io.atomic_export_writer import write_atomically

from .comic_project_codec import ComicProjectCodec
from .comic_project_snapshot import ComicProjectSnapshot
from .revision_conflict_error import RevisionConflictError

logger = logging.getLogger(__name__)

__all__ = ["ComicProjectStore"]

_SAFE_ID = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class ComicProjectStore:
    DIRECTORY: ClassVar[str] = "comic_projects"
    FILE_NAME: ClassVar[str] = "project.json"

    def __init__(self, root: Path, *, codec: ComicProjectCodec | None = None) -> None:
        self._root = Path(root) / self.DIRECTORY
        self._codec = codec or ComicProjectCodec()
        self._extras: dict[str, dict[str, Any]] = {}

    @property
    def root(self) -> Path:
        return self._root

    def project_dir(self, project_id: str) -> Path:
        if not _SAFE_ID.match(project_id):
            raise ValueError(f"unsafe project id: {project_id!r}")
        return self._root / project_id

    def project_file(self, project_id: str) -> Path:
        return self.project_dir(project_id) / self.FILE_NAME

    # -- queries -------------------------------------------------------------------

    def exists(self, project_id: str) -> bool:
        return self.project_file(project_id).is_file()

    def list_projects(self) -> tuple[tuple[str, str], ...]:
        """``(project_id, title)`` for every readable sidecar."""
        if not self._root.is_dir():
            return ()
        found: list[tuple[str, str]] = []
        for child in sorted(self._root.iterdir()):
            file = child / self.FILE_NAME
            if not file.is_file():
                continue
            try:
                payload = json.loads(file.read_text(encoding="utf-8"))
                title = str(payload.get("project", {}).get("title", child.name))
            except Exception:
                title = child.name
            found.append((child.name, title))
        return tuple(found)

    def load(self, project_id: str) -> ComicProjectSnapshot | None:
        file = self.project_file(project_id)
        if not file.is_file():
            return None
        snapshot, extra = self._codec.loads(file.read_text(encoding="utf-8"))
        self._extras[project_id] = extra
        return snapshot

    # -- writes --------------------------------------------------------------------

    def save(
        self, snapshot: ComicProjectSnapshot, *, expected_revision: int | None = None
    ) -> ComicProjectSnapshot:
        project_id = snapshot.project.project_id
        file = self.project_file(project_id)
        if expected_revision is not None and file.is_file():
            stored = self._stored_revision(file)
            if stored is not None and stored != expected_revision:
                raise RevisionConflictError(project_id, expected=expected_revision, actual=stored)
        text = self._codec.dumps(snapshot, extra=self._extras.get(project_id))
        file.parent.mkdir(parents=True, exist_ok=True)
        write_atomically(file, lambda tmp: tmp.write_text(text, encoding="utf-8"))
        return snapshot

    def delete(self, project_id: str) -> bool:
        file = self.project_file(project_id)
        if not file.is_file():
            return False
        file.unlink()
        self._extras.pop(project_id, None)
        with contextlib.suppress(OSError):
            file.parent.rmdir()
        return True

    @staticmethod
    def _stored_revision(file: Path) -> int | None:
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
            return int(payload.get("project", {}).get("revision", 0))
        except Exception:
            logger.warning("comic project sidecar unreadable: %s", file.name)
            return None
