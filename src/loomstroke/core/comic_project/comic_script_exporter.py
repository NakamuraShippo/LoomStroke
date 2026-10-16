"""CSV / JSON script export — the importer's round-trip partner (CT-17 §14 C)."""

from __future__ import annotations

import csv
import io
import json
from typing import Any, ClassVar

from .comic_project_snapshot import ComicProjectSnapshot

__all__ = ["ComicScriptExporter"]


class ComicScriptExporter:
    SCHEMA: ClassVar[str] = "loomstroke.comic_script.v1"
    COLUMNS: ClassVar[tuple[str, ...]] = (
        "dialogue_id",
        "chapter",
        "page",
        "panel",
        "speaker",
        "text",
        "direction",
    )

    def rows(self, snapshot: ComicProjectSnapshot) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for dialogue in snapshot.dialogues_in_order():
            entry = snapshot.page_entries.get(dialogue.entry_id or "")
            chapter = snapshot.chapters.get(entry.chapter_id) if entry else None
            panel = snapshot.panels.get(dialogue.panel_id or "")
            rows.append(
                {
                    "dialogue_id": dialogue.dialogue_id,
                    "chapter": chapter.title if chapter else "",
                    "page": entry.page_number if entry else "",
                    "panel": panel.reading_order if panel else "",
                    "speaker": dialogue.speaker,
                    "text": dialogue.text,
                    "direction": dialogue.reading_direction,
                }
            )
        return rows

    def csv(self, snapshot: ComicProjectSnapshot) -> str:
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=list(self.COLUMNS), lineterminator="\n")
        writer.writeheader()
        for row in self.rows(snapshot):
            # Formula-looking cells are neutralised with a leading apostrophe so
            # spreadsheets never evaluate exported dialogue (spec 17 §12).
            safe = dict(row)
            for key in ("speaker", "text", "chapter"):
                value = str(safe.get(key, ""))
                if value[:1] in {"=", "+", "-", "@"}:
                    safe[key] = "'" + value
            writer.writerow(safe)
        return buffer.getvalue()

    def json(self, snapshot: ComicProjectSnapshot) -> str:
        return (
            json.dumps(
                {
                    "schema": self.SCHEMA,
                    "title": snapshot.project.title,
                    "rows": self.rows(snapshot),
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
