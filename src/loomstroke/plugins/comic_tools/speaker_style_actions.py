"""Command-backed actions for the speaker style panel."""

from copy import deepcopy
from uuid import uuid4

from loomstroke.core.japanese_text.speaker_styles import capture_text_style

from .speaker_style_library import export_defaults, import_defaults
from .speaker_styles import assign_speaker


def perform_style_action(tool, action, style_id, fields):  # noqa: PLR0912 - explicit user actions
    service = tool.host_api.pages.speaker_styles
    registry = service.read()
    styles = registry.setdefault("styles", [])
    selected = next((s for s in styles if s["style_id"] == style_id), None)
    if action == "export":
        export_defaults(registry)
        return
    if action == "import":
        if not service.update(import_defaults()):
            raise ValueError("The library could not be applied.")
        return
    state = tool.read_state()
    bid = tool.selected_balloon_id
    obj = state.text_objects.get(bid) if state else None
    if action in {"apply", "clear"}:
        if selected is None or obj is None:
            raise ValueError("Select a style and a balloon.")
        record = state.balloons[bid]
        speaker = selected.get("speaker", {})
        if not assign_speaker(
            tool,
            bid,
            speaker.get("speaker_id", record.speaker_id),
            speaker.get("speaker_name", record.speaker_name),
            style=selected,
            clear_overrides=action == "clear",
        ):
            raise ValueError("The style could not be applied.")
        return
    if action in {"register", "update", "details"}:
        if (action != "details" and obj is None) or (action != "register" and selected is None):
            raise ValueError("Select formatted text.")
        updated = {
            **deepcopy(selected or {}),
            "style_id": selected["style_id"] if action != "register" else uuid4().hex,
            "schema_version": 1,
            "display_name": fields["display_name"],
            "speaker": {"speaker_id": fields["speaker_id"], "speaker_name": fields["speaker_name"]},
            "color_tag": fields.get("color_tag", ""),
            "text": (
                deepcopy(selected["text"])
                if action == "details"
                else capture_text_style(obj.meta["text_tool"])
            ),
        }
        if action != "register":
            styles[styles.index(selected)] = updated
        else:
            styles.append(updated)
        if fields["default"]:
            registry["default_style_id"] = updated["style_id"]
        elif registry.get("default_style_id") == updated["style_id"]:
            registry.pop("default_style_id", None)
    elif selected is None:
        raise ValueError("Select a style.")
    elif action == "delete":
        styles.remove(selected)
        if registry.get("default_style_id") == selected["style_id"]:
            registry.pop("default_style_id", None)
    elif action == "duplicate":
        styles.append({**deepcopy(selected), "style_id": uuid4().hex, "speaker": {}})
    if not service.update(registry):
        raise ValueError("The registry could not be updated.")
