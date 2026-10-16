"""JSON sidecar codec for a comic project (CT-17 §9).

Schema ``loomstroke.comic_project.v1``.  Unknown top-level keys are kept
and written back untouched (forward compatibility); unknown entity
fields are dropped with a warning rather than failing the whole load.
No absolute paths, no credentials — only what the models hold.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any, ClassVar

from .comic_page_entry import ComicPageEntry
from .comic_panel_entry import ComicPanelEntry
from .comic_project import ComicProject
from .comic_project_snapshot import ComicProjectSnapshot
from .dialogue_entry import DialogueEntry
from .document_link import DocumentLink
from .production_task import ProductionTask
from .story_chapter import StoryChapter
from .story_scene import StoryScene

logger = logging.getLogger(__name__)

__all__ = ["ComicProjectCodec"]

_COLLECTIONS: tuple[tuple[str, type, str], ...] = (
    ("chapters", StoryChapter, "chapter_id"),
    ("scenes", StoryScene, "scene_id"),
    ("page_entries", ComicPageEntry, "entry_id"),
    ("panels", ComicPanelEntry, "panel_id"),
    ("dialogues", DialogueEntry, "dialogue_id"),
    ("tasks", ProductionTask, "task_id"),
)


class ComicProjectCodec:
    SCHEMA: ClassVar[str] = "loomstroke.comic_project.v1"
    MAX_BYTES: ClassVar[int] = 64 * 1024 * 1024

    def to_payload(
        self, snapshot: ComicProjectSnapshot, *, extra: Mapping[str, Any] | None = None
    ) -> dict[str, Any]:
        payload: dict[str, Any] = dict(extra or {})
        payload["schema"] = self.SCHEMA
        project = asdict(snapshot.project)
        project["document_links"] = [asdict(link) for link in snapshot.project.document_links]
        payload["project"] = project
        for name, _cls, _key in _COLLECTIONS:
            items = getattr(snapshot, name)
            payload[name] = [self._entity_payload(item) for item in items.values()]
        return payload

    @staticmethod
    def _entity_payload(item: Any) -> dict[str, Any]:
        data = asdict(item)
        if "text_object_refs" in data:
            data["text_object_refs"] = [list(pair) for pair in data["text_object_refs"]]
        return data

    def dumps(
        self, snapshot: ComicProjectSnapshot, *, extra: Mapping[str, Any] | None = None
    ) -> str:
        return (
            json.dumps(self.to_payload(snapshot, extra=extra), ensure_ascii=False, indent=2) + "\n"
        )

    def loads(self, text: str) -> tuple[ComicProjectSnapshot, dict[str, Any]]:
        if len(text.encode("utf-8")) > self.MAX_BYTES:
            raise ValueError("comic project file is too large")
        payload = json.loads(text)
        if not isinstance(payload, Mapping):
            raise ValueError("comic project payload must be an object")
        return self.from_payload(payload)

    def from_payload(
        self, payload: Mapping[str, Any]
    ) -> tuple[ComicProjectSnapshot, dict[str, Any]]:
        """``(snapshot, unknown_top_level_keys)``."""
        schema = str(payload.get("schema", ""))
        if not schema.startswith("loomstroke.comic_project.v1"):
            raise ValueError(f"unsupported comic project schema: {schema or 'missing'}")
        raw_project = payload.get("project")
        if not isinstance(raw_project, Mapping):
            raise ValueError("comic project payload lacks 'project'")
        project = self._project_from(raw_project)
        collections: dict[str, dict[str, Any]] = {}
        for name, cls, key in _COLLECTIONS:
            collections[name] = {}
            for raw in payload.get(name, []) or []:
                if not isinstance(raw, Mapping):
                    continue
                item = self._entity_from(cls, raw)
                if item is not None:
                    collections[name][str(getattr(item, key))] = item
        known = {"schema", "project", *(name for name, _c, _k in _COLLECTIONS)}
        extra = {key: value for key, value in payload.items() if key not in known}
        return ComicProjectSnapshot(project=project, **collections), extra

    @staticmethod
    def _project_from(raw: Mapping[str, Any]) -> ComicProject:
        links = tuple(
            DocumentLink(
                document_id=str(link.get("document_id", "")),
                basename=str(link.get("basename", "")),
                content_hash=str(link.get("content_hash", "")),
            )
            for link in raw.get("document_links", []) or []
            if isinstance(link, Mapping) and link.get("document_id")
        )
        return ComicProject(
            project_id=str(raw.get("project_id", "")),
            title=str(raw.get("title", "")),
            reading_direction=str(raw.get("reading_direction", "rtl")),
            document_links=links,
            workflow_profile_id=str(raw.get("workflow_profile_id", "manga.default")),
            revision=int(raw.get("revision", 1) or 1),
            created_at=str(raw.get("created_at", "")),
        )

    @staticmethod
    def _entity_from(entity_type: type, raw: Mapping[str, Any]) -> Any | None:
        fields = set(getattr(entity_type, "__dataclass_fields__", {}))
        kwargs: dict[str, Any] = {}
        for key, value in raw.items():
            if key not in fields:
                continue
            normalized = value
            if isinstance(value, list):
                normalized = tuple(
                    tuple(part) if isinstance(part, list) else part for part in value
                )
            kwargs[key] = normalized
        try:
            return entity_type(**kwargs)
        except (TypeError, ValueError):
            logger.warning("comic project: skipping malformed %s entry", entity_type.__name__)
            return None
