"""Detached same-page story snapshots; frames retain their own layer ownership."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

from ..path_text.metadata import path_settings
from ..vector_text_layout import TextLayoutCalculator as Layout
from .annotations import ANNOTATION_KEYS
from .frame_flow import payload_flow
from .line_breaker import JapaneseLineBreaker
from .paragraph_styles import paragraph_style
from .story_metadata import validated_story
from .story_shapes import frame_shape


def story_of(obj):
    meta = getattr(obj, "meta", None)
    payload = meta.get("text_tool", {}) if isinstance(meta, Mapping) else {}
    value = payload.get("linked_story") if isinstance(payload, Mapping) else None
    return validated_story(value)


def slice_payload(payload, start, end):
    result = deepcopy(payload)
    text = Layout.text_content_from_payload(payload)
    result["content"] = text[start:end]
    document = result.setdefault("document", {})
    document["text"] = text[start:end]
    for container, keys in (
        (document, ("runs", "paragraphs", *ANNOTATION_KEYS)),
        (result, ("tracking_runs", "line_height_runs")),
    ):
        for key in keys:
            if key not in container:
                continue
            records = []
            for run in container[key]:
                if (
                    not isinstance(run, Mapping)
                    or not isinstance(run.get("start"), int)
                    or not isinstance(run.get("end"), int)
                ):
                    continue
                left, right = max(start, run["start"]), min(end, run["end"])
                if left < right:
                    records.append(dict(run, start=left - start, end=right - start))
            container[key] = records
    return result


def combine_payloads(payloads):
    result = deepcopy(payloads[0])
    text, runs, paragraphs = "", [], []
    annotations = {key: [] for key in ANNOTATION_KEYS}
    ranges = {key: [] for key in ("tracking_runs", "line_height_runs")}
    for payload in payloads:
        part = Layout.text_content_from_payload(payload)
        offset = len(text)
        if text and part:
            text += "\n"
            offset += 1
        text += part
        defaults = dict(Layout.text_style_defaults(payload))
        defaults.update(
            {
                key: payload[key]
                for key in ("font_family", "font_style", "font_size_pt", "color_rgba")
                if key in payload
            }
        )
        document = payload.get("document", {})
        source_runs = document.get("runs", []) or [{"start": 0, "end": len(part)}]
        cursor = 0
        for run in source_runs:
            if not isinstance(run, Mapping):
                continue
            left, right = run.get("start", 0), run.get("end", 0)
            if cursor < left:
                runs.append(dict(defaults, start=offset + cursor, end=offset + left))
            runs.append(
                dict(
                    defaults,
                    **{k: v for k, v in run.items() if k not in {"start", "end"}},
                    start=offset + left,
                    end=offset + right,
                )
            )
            cursor = right
        if cursor < len(part):
            runs.append(dict(defaults, start=offset + cursor, end=offset + len(part)))
        from .typography import typography_from_payload  # noqa: PLC0415

        for start, _end, following in JapaneseLineBreaker.paragraphs(part):
            local = paragraph_style(payload, start)
            previous = next(
                (
                    record
                    for record in document.get("paragraphs", [])
                    if isinstance(record, Mapping) and record.get("start") == start
                ),
                {},
            )
            paragraphs.append(
                {
                    **deepcopy(previous),
                    "start": start + offset,
                    "end": following + offset,
                    "style_overrides": {
                        **previous.get("style_overrides", {}),
                        "align": local.get("align", Layout.text_align_from_payload(payload)),
                        "vertical_flow": local.get(
                            "vertical_flow", Layout.text_vertical_flow_from_payload(payload)
                        ),
                        "japanese": local.get("typography", typography_from_payload(payload)),
                    },
                }
            )
        for key, destination in annotations.items():
            destination.extend(
                dict(run, start=run["start"] + offset, end=run["end"] + offset)
                for run in document.get(key, [])
                if isinstance(run, Mapping) and "start" in run and "end" in run
            )
        for key, destination in ranges.items():
            field = "tracking" if key == "tracking_runs" else "line_height"
            default = (
                Layout.text_tracking_from_payload(payload)
                if field == "tracking"
                else Layout.text_line_height_from_payload(payload)
            )
            source_ranges = sorted(
                (
                    run
                    for run in payload.get(key, [])
                    if isinstance(run, Mapping)
                    and isinstance(run.get("start"), int)
                    and isinstance(run.get("end"), int)
                ),
                key=lambda run: run["start"],
            )
            cursor = 0
            for run in source_ranges:
                if cursor < run["start"]:
                    destination.append(
                        {"start": cursor + offset, "end": run["start"] + offset, field: default}
                    )
                destination.append(dict(run, start=run["start"] + offset, end=run["end"] + offset))
                cursor = run["end"]
            if cursor < len(part):
                destination.append(
                    {"start": cursor + offset, "end": len(part) + offset, field: default}
                )
    result["content"] = text
    result["document"] = {
        **result.get("document", {}),
        "text": text,
        "runs": runs,
        "paragraphs": paragraphs,
        **annotations,
    }
    result.update(ranges)
    return result


def story_objects(canvas, ids):
    entries = {
        str(obj.id): (layer_id, obj)
        for layer_id, obj in canvas.get_vector_objects(include_hidden=True)
    }
    if any(key not in entries for key in ids):
        raise ValueError("A linked text frame is missing.")
    result = [entries[key] for key in ids]
    for layer_id, obj in result:
        layer = canvas._get_layer_by_id(layer_id)
        if layer is None or _locked(canvas, layer):
            raise ValueError("A linked text frame is locked.")
        payload = (getattr(obj, "meta", None) or {}).get("text_tool")
        if not isinstance(payload, Mapping):
            raise ValueError("A linked text frame is missing.")
        if path_settings(payload) is not None:
            raise ValueError("The text frames could not be linked.")
    return result


def _locked(canvas, layer):
    seen = set()
    while layer is not None:
        if layer.id in seen or getattr(layer, "locked", False):
            return True
        seen.add(layer.id)
        parent = getattr(layer, "parent_id", None)
        layer = canvas._get_layer_by_id(parent) if parent else None
    return False


def assign_story(entries, source, story_id=None):
    frames = frame_entries(entries)
    story_id = story_id or uuid4().hex
    result = []
    for layer_id, obj in entries:
        payload = deepcopy(obj.meta["text_tool"])
        for key in ("content", "document", "style_map", "tracking_runs", "line_height_runs"):
            if key in source:
                if key == "document":
                    payload[key] = {
                        **deepcopy(payload.get(key, {})),
                        **{
                            name: deepcopy(source[key][name])
                            for name in ("text", "runs", "paragraphs", *ANNOTATION_KEYS)
                            if name in source[key]
                        },
                    }
                elif key == "style_map":
                    payload[key] = {**deepcopy(payload.get(key, {})), **deepcopy(source[key])}
                else:
                    payload[key] = deepcopy(source[key])
        payload["linked_story"] = {
            "id": story_id,
            "member": str(obj.id),
            "frames": deepcopy(frames),
        }
        payload["word_wrap"], payload["auto_size"], payload["text_mode"] = True, False, "area"
        payload.setdefault("frame", {})["auto_size"] = False
        payload.setdefault("layout", {})["wrap_mode"] = (
            "shape" if payload.get("shape_frame") else "word"
        )
        result.append((layer_id, replace(obj, meta={**deepcopy(obj.meta), "text_tool": payload})))
    return result


def frame_entries(entries):
    frames = []
    for _layer_id, obj in entries:
        payload = obj.meta["text_tool"]
        bbox = Layout.text_bbox_from_payload(payload)
        top, right, bottom, left = Layout.text_padding_from_payload(payload)
        frames.append(
            {
                "id": str(obj.id),
                "rect": [
                    bbox[0] + left,
                    bbox[1] + top,
                    max(0.01, bbox[2] - bbox[0] - left - right),
                    max(0.01, bbox[3] - bbox[1] - top - bottom),
                ],
                "flow": payload_flow(payload),
                **frame_shape(payload),
            }
        )
    return frames
