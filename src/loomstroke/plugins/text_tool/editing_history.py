"""Preserve text range metadata across the inline editor's Qt undo history."""

from __future__ import annotations

import time
from collections.abc import Mapping
from copy import deepcopy

from loomstroke.core.japanese_text.annotations import (
    ANNOTATION_KEYS,
    annotation_selection,
    map_annotation_range,
    replace_annotation_range,
)
from loomstroke.core.japanese_text.character_styles import apply_character_override
from loomstroke.core.japanese_text.named_styles import apply_named_style
from loomstroke.core.japanese_text.paragraph_styles import (
    apply_paragraph_alignment,
    apply_paragraph_style,
)
from loomstroke.plugins.vector_tools.common import VectorObject

from .common import TEXT_META_KEY, extract_text_content, merge_line_height_run, merge_tracking_run
from .editing_metadata import _changed_span, clone_text_for_edit, replace_edited_text


class TextEditMetadataHistory:
    """Keep character range metadata beside Qt's plain-text undo positions."""

    def __init__(self, obj: VectorObject, *, undo_steps: int = 0) -> None:
        self._position = int(undo_steps)
        self._snapshots = {self._position: clone_text_for_edit(obj)}
        self._parents: dict[int, int] = {}
        self._changes: dict[int, list[tuple[int, int, int]]] = {}

    def update(
        self,
        obj: VectorObject,
        content: str,
        *,
        undo_steps: int,
        cursor_position: int | None = None,
    ) -> VectorObject:
        """Restore known undo/redo states, or record a new contiguous text edit."""
        position = int(undo_steps)
        previous = self._snapshots.get(self._position)
        if previous is not None and _object_content(previous) == _object_content(obj):
            # Formatting can change without adding a QTextDocument undo step.
            self._snapshots[self._position] = clone_text_for_edit(obj)
        known = self._snapshots.get(position)
        if position != self._position and known is not None and _object_content(known) == content:
            updated = _restore_edit_ranges(obj, known)
        else:
            updated = replace_edited_text(obj, content, cursor_position=cursor_position)
            span = _changed_span(_object_content(obj), content, cursor_position)
            if position < self._position:
                # A new text box can acquire metadata after typing has already
                # created Qt undo entries. Discovering an earlier entry is an
                # undo, not a new edit or a reason to discard the redo branch.
                self._insert_earlier_snapshot(position, updated, span)
            else:
                # A new edit following undo discards the previous redo branch.
                for future in [step for step in self._snapshots if step > self._position]:
                    del self._snapshots[future]
                    self._parents.pop(future, None)
                    self._changes.pop(future, None)
                if position != self._position:
                    self._parents[position] = self._position
                    self._changes[position] = []
                self._changes.setdefault(position, []).append(span)
        self._position = position
        self._snapshots[position] = clone_text_for_edit(updated)
        return updated

    def _insert_earlier_snapshot(self, position, obj, reverse_span):
        """Insert an unrecorded undo state with edges directed towards later edits."""
        earlier = [step for step in self._snapshots if step < position]
        later = [step for step in self._snapshots if step > position]
        content = _object_content(obj)
        if earlier:
            parent = max(earlier)
            self._parents[position] = parent
            self._changes[position] = [
                _changed_span(_object_content(self._snapshots[parent]), content, None)
            ]
        else:
            self._parents.pop(position, None)
            self._changes.pop(position, None)
        if later:
            child = min(later)
            self._parents[child] = position
            if child == self._position:
                start, old_end, new_end = reverse_span
                self._changes[child] = [(start, new_end, old_end)]
            else:
                self._changes[child] = [
                    _changed_span(content, _object_content(self._snapshots[child]), None)
                ]

    def apply_range_format(
        self,
        key: str,
        start: int,
        end: int,
        value: float | list[int] | str,
        current_obj: VectorObject,
    ) -> None:
        """Carry a separate range-format command through existing text history."""
        if key not in {
            "tracking_runs",
            "line_height_runs",
            "color_rgba",
            "font_family",
            "font_style",
            "font_size_pt",
            "paragraph_align",
            "paragraph_typography",
            "paragraph_flow",
            "paragraph_columns",
            "named_style",
            "horizontal_scale",
            "vertical_scale",
            "baseline_shift_em",
            "before_space_em",
            "after_space_em",
        }:
            raise ValueError("Unsupported range format")
        self._snapshots[self._position] = clone_text_for_edit(current_obj)
        ranges = [(start, end)]
        position = self._position
        while position in self._parents:
            for change_start, old_end, new_end in reversed(self._changes[position]):
                ranges = _map_surviving_ranges(ranges, change_start, new_end, old_end)
            position = self._parents[position]
            self._format_snapshot(position, key, ranges, value)
        # Formatting does not change the Qt document, so an existing redo
        # branch also needs the values on characters that survive those edits.
        mapped = {self._position: [(start, end)]}
        for position in sorted(self._snapshots):
            parent = self._parents.get(position)
            if position <= self._position or parent not in mapped:
                continue
            ranges = mapped[parent]
            for change_start, old_end, new_end in self._changes[position]:
                ranges = _map_surviving_ranges(ranges, change_start, old_end, new_end)
            mapped[position] = ranges
            self._format_snapshot(position, key, ranges, value)

    def _format_snapshot(self, position, key, ranges, value):
        snapshot = self._snapshots[position]
        payload = snapshot.meta.get(TEXT_META_KEY)
        if not isinstance(payload, dict):
            return
        if key == "paragraph_align":
            for start, end in ranges:
                payload = apply_paragraph_alignment(payload, start, end, value)
            snapshot.meta[TEXT_META_KEY] = payload
            return
        paragraph_keys = {
            "paragraph_typography": "japanese",
            "paragraph_flow": "vertical_flow",
            "paragraph_columns": "paragraph_columns",
        }
        if key in paragraph_keys:
            for start, end in ranges:
                payload = apply_paragraph_style(payload, start, end, {paragraph_keys[key]: value})
            snapshot.meta[TEXT_META_KEY] = payload
            return
        if key == "named_style":
            for start, end in ranges:
                payload = apply_named_style(payload, start, end, value)
            snapshot.meta[TEXT_META_KEY] = payload
            return
        if key in {
            "color_rgba",
            "font_family",
            "font_style",
            "font_size_pt",
            "horizontal_scale",
            "vertical_scale",
            "baseline_shift_em",
            "before_space_em",
            "after_space_em",
        }:
            for start, end in ranges:
                payload = apply_character_override(payload, start, end, key, value)
            snapshot.meta[TEXT_META_KEY] = payload
            return
        merge = merge_tracking_run if key == "tracking_runs" else merge_line_height_run
        runs = list(payload.get(key) or [])
        for start, end in ranges:
            runs = merge(runs, start, end, value, len(_object_content(snapshot)))
        payload[key] = runs

    def apply_annotation_change(self, kind, start, end, options, current_obj: VectorObject) -> None:
        """Carry an annotation command through the existing plain-text history.

        Ruby cannot cross an edit to its parent. Such earlier/later text states
        keep their own annotations instead of acquiring a different word's
        reading. TCY follows its surviving parent range, including insertions.
        """
        if kind not in ANNOTATION_KEYS:
            raise ValueError("Unsupported text annotation")
        self._snapshots[self._position] = clone_text_for_edit(current_obj)
        bounds = annotation_selection(_object_content(current_obj), start, end)
        if bounds is None:
            return
        position = self._position
        mapped_range = bounds
        while position in self._parents and mapped_range is not None:
            for change_start, old_end, new_end in reversed(self._changes[position]):
                if mapped_range is None:
                    break
                mapped_range = map_annotation_range(
                    kind, *mapped_range, change_start, new_end, old_end
                )
            position = self._parents[position]
            self._annotate_snapshot(position, kind, mapped_range, options)
        mapped = {self._position: bounds}
        for position in sorted(self._snapshots):
            parent = self._parents.get(position)
            if position <= self._position or parent not in mapped:
                continue
            mapped_range = mapped[parent]
            for change_start, old_end, new_end in self._changes[position]:
                if mapped_range is None:
                    break
                mapped_range = map_annotation_range(
                    kind, *mapped_range, change_start, old_end, new_end
                )
            mapped[position] = mapped_range
            self._annotate_snapshot(position, kind, mapped_range, options)

    def _annotate_snapshot(self, position, kind, bounds, options):
        if bounds is None:
            return
        snapshot = self._snapshots[position]
        if (
            kind == "ruby_runs"
            and annotation_selection(_object_content(snapshot), *bounds) != bounds
        ):
            return
        payload = snapshot.meta.get(TEXT_META_KEY)
        if isinstance(payload, Mapping):
            snapshot.meta[TEXT_META_KEY] = replace_annotation_range(payload, kind, *bounds, options)


def _map_surviving_ranges(ranges, start, source_end, target_end):
    """Map only surviving characters across one insertion/deletion/replacement."""
    mapped = []
    delta = target_end - source_end
    for left, right in ranges:
        if left < start:
            mapped.append((left, min(right, start)))
        if right > source_end:
            mapped.append((max(left, source_end) + delta, right + delta))
    return [(left, right) for left, right in mapped if left < right]


def _object_content(obj: VectorObject) -> str:
    payload = obj.meta.get(TEXT_META_KEY)
    return extract_text_content(payload) if isinstance(payload, Mapping) else ""


def _restore_edit_ranges(obj: VectorObject, snapshot: VectorObject) -> VectorObject:
    """Restore text-linked data while retaining separately changed typography."""
    restored = clone_text_for_edit(obj)
    raw_payload = restored.meta[TEXT_META_KEY]
    saved = snapshot.meta[TEXT_META_KEY]
    if not isinstance(raw_payload, dict) or not isinstance(saved, Mapping):
        raise ValueError("Text history requires text metadata")
    payload = raw_payload
    payload.pop("inline_preview", None)
    payload["content"] = extract_text_content(saved)
    for key in ("tracking_runs", "line_height_runs"):
        if key in saved:
            payload[key] = deepcopy(saved[key])
        else:
            payload.pop(key, None)
    document = payload.get("document")
    saved_document = saved.get("document")
    if isinstance(document, dict) and isinstance(saved_document, Mapping):
        document["text"] = payload["content"]
        for key in ("runs", "paragraphs", *ANNOTATION_KEYS):
            if key in saved_document:
                document[key] = deepcopy(saved_document[key])
            else:
                document.pop(key, None)
        # Font-panel paragraph changes are separate canvas commands, not Qt
        # text undo entries. Preserve those values when restoring older ranges.
        for key in ("align", "line_height", "vertical_flow"):
            if key in payload and payload.get(key) != saved.get(key):
                for paragraph in document.get("paragraphs", []):
                    if isinstance(paragraph, dict):
                        paragraph[key] = deepcopy(payload[key])
    payload["updated_at"] = time.time()
    return restored
