"""CSV / JSON script import with preview (CT-17 §5.1 / §12 / §13).

Columns: ``chapter,page,panel,speaker,text,direction,dialogue_id`` (header
row required; order free; extra columns ignored).  JSON:
``{"schema": "loomstroke.comic_script.v1", "rows": [{...}]}``.  Limits on
bytes / rows / cell length; cells are data only — a formula-looking cell
(``=``, ``+``, ``-``, ``@`` prefix) is flagged but never evaluated.  Rows
with a known ``dialogue_id`` become updates (previous text kept for the
diff view); nothing is committed until ``apply`` runs on chosen rows.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Mapping
from typing import Any, ClassVar

from .comic_project_mutator import ComicProjectMutator
from .comic_project_snapshot import ComicProjectSnapshot
from .script_import_preview import ScriptImportPreview
from .script_import_row import ScriptImportRow

__all__ = ["ComicScriptImporter"]

_HEADER_ALIASES: dict[str, str] = {
    "chapter": "chapter",
    "章": "chapter",
    "page": "page",
    "ページ": "page",
    "panel": "panel",
    "コマ": "panel",
    "speaker": "speaker",
    "話者": "speaker",
    "text": "text",
    "台詞": "text",
    "セリフ": "text",
    "direction": "direction",
    "dialogue_id": "dialogue_id",
    "id": "dialogue_id",
}


class ComicScriptImporter:
    SCHEMA: ClassVar[str] = "loomstroke.comic_script.v1"
    MAX_BYTES: ClassVar[int] = 8 * 1024 * 1024
    MAX_ROWS: ClassVar[int] = 20_000
    MAX_CELL: ClassVar[int] = 4_000

    def __init__(self, mutator: ComicProjectMutator | None = None) -> None:
        self._mutator = mutator or ComicProjectMutator()

    # -- preview -------------------------------------------------------------------

    def preview(
        self, text: str, fmt: str, *, snapshot: ComicProjectSnapshot
    ) -> ScriptImportPreview:
        if len(text.encode("utf-8", errors="replace")) > self.MAX_BYTES:
            return ScriptImportPreview(issues=("file_too_large",))
        try:
            records = self._parse(text, fmt)
        except ValueError as error:
            return ScriptImportPreview(issues=(str(error),))
        if len(records) > self.MAX_ROWS:
            return ScriptImportPreview(issues=("too_many_rows",))
        rows = tuple(
            self._classify(index + 1, record, snapshot) for index, record in enumerate(records)
        )
        return ScriptImportPreview(rows=rows)

    def _parse(self, text: str, fmt: str) -> list[dict[str, str]]:
        if fmt == "json":
            return self._parse_json(text)
        if fmt == "csv":
            return self._parse_csv(text)
        raise ValueError("unsupported_format")

    def _parse_json(self, text: str) -> list[dict[str, str]]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError("invalid_json") from error
        if not isinstance(payload, Mapping) or not str(payload.get("schema", "")).startswith(
            "loomstroke.comic_script.v1"
        ):
            raise ValueError("unsupported_schema")
        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise ValueError("rows_missing")
        records: list[dict[str, str]] = []
        for raw in rows:
            if not isinstance(raw, Mapping):
                continue
            records.append({str(k): ("" if v is None else str(v)) for k, v in raw.items()})
        return records

    def _parse_csv(self, text: str) -> list[dict[str, str]]:
        reader = csv.reader(io.StringIO(text.lstrip("﻿")))
        try:
            header = next(reader)
        except StopIteration as error:
            raise ValueError("empty_file") from error
        columns = [
            _HEADER_ALIASES.get(name.strip().lower(), name.strip().lower()) for name in header
        ]
        if "text" not in columns:
            raise ValueError("text_column_missing")
        records: list[dict[str, str]] = []
        for values in reader:
            if not any(cell.strip() for cell in values):
                continue
            records.append({columns[i]: values[i] for i in range(min(len(columns), len(values)))})
        return records

    def _classify(
        self, row_number: int, record: Mapping[str, str], snapshot: ComicProjectSnapshot
    ) -> ScriptImportRow:
        issues: list[str] = []
        text = self._unquote(str(record.get("text", "")))
        speaker = self._unquote(str(record.get("speaker", "")).strip())
        chapter = str(record.get("chapter", "")).strip()
        direction = str(record.get("direction", "")).strip().lower() or None
        dialogue_id = str(record.get("dialogue_id", "")).strip() or None
        page_number = self._int(record.get("page"))
        panel = self._int(record.get("panel"))
        if not text.strip():
            issues.append("text_missing")
        if len(text) > self.MAX_CELL or len(speaker) > self.MAX_CELL:
            issues.append("cell_too_long")
        for cell in (text, speaker, chapter):
            if cell[:1] in {"=", "+", "-", "@"} and len(cell) > 1:
                issues.append("formula_like")
                break
        if direction is not None and direction not in ("vertical", "horizontal"):
            issues.append("direction_invalid")
            direction = None
        if record.get("page", "").strip() and page_number is None:
            issues.append("page_invalid")
        action = "add"
        previous: str | None = None
        if dialogue_id and dialogue_id in snapshot.dialogues:
            previous = snapshot.dialogues[dialogue_id].text
            action = "update" if previous != text else "unchanged"
        blocking = {"text_missing", "cell_too_long"}
        if blocking & set(issues):
            action = "invalid"
        return ScriptImportRow(
            row_number=row_number,
            action=action,
            chapter=chapter,
            page_number=page_number,
            panel=panel,
            speaker=speaker,
            text=text,
            reading_direction=direction,
            dialogue_id=dialogue_id,
            previous_text=previous,
            issues=tuple(issues),
        )

    @staticmethod
    def _unquote(cell: str) -> str:
        # The exporter neutralises formula-looking cells with a leading
        # apostrophe (spreadsheet convention); strip it so round trips are exact.
        if len(cell) > 1 and cell[0] == "'" and cell[1] in {"=", "+", "-", "@"}:
            return cell[1:]
        return cell

    @staticmethod
    def _int(value: Any) -> int | None:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            return int(text)
        except ValueError:
            return None

    # -- apply ---------------------------------------------------------------------

    def apply(
        self,
        snapshot: ComicProjectSnapshot,
        preview: ScriptImportPreview,
        *,
        selected_rows: Iterable[int] | None = None,
    ) -> tuple[ComicProjectSnapshot, int]:
        """Apply ``add`` / ``update`` rows (all, or only ``selected_rows`` numbers)."""
        if preview.blocked:
            raise ValueError("preview has file-level issues")
        wanted = set(selected_rows) if selected_rows is not None else None
        chapters_by_title = {c.title: c.chapter_id for c in snapshot.chapters.values()}
        applied = 0
        current = snapshot
        for row in preview.applicable_rows:
            if wanted is not None and row.row_number not in wanted:
                continue
            entry_id = None
            if row.chapter:
                chapter_id = chapters_by_title.get(row.chapter)
                if chapter_id is None:
                    current, chapter_id = self._mutator.add_chapter(current, row.chapter)
                    chapters_by_title[row.chapter] = chapter_id
                if row.page_number is not None:
                    entry_id = self._entry_for(current, chapter_id, row.page_number)
                    if entry_id is None:
                        current, entry_id = self._mutator.add_page_entry(
                            current, chapter_id, page_number=row.page_number
                        )
            if row.action == "update" and row.dialogue_id:
                current = self._mutator.update_dialogue(
                    current,
                    row.dialogue_id,
                    text=row.text,
                    speaker=row.speaker or None,
                    reading_direction=row.reading_direction,
                )
                if entry_id is not None:
                    current = self._mutator.assign_dialogue(
                        current, row.dialogue_id, entry_id=entry_id
                    )
            else:
                current, _new_id = self._mutator.add_dialogue(
                    current,
                    speaker=row.speaker,
                    text=row.text,
                    entry_id=entry_id,
                    reading_direction=row.reading_direction,
                    dialogue_id=row.dialogue_id,
                )
            applied += 1
        return current, applied

    @staticmethod
    def _entry_for(snapshot: ComicProjectSnapshot, chapter_id: str, page_number: int) -> str | None:
        for entry in snapshot.page_entries_for_chapter(chapter_id):
            if entry.page_number == page_number:
                return entry.entry_id
        return None
