"""Speaker assignment uses the document registry and the existing balloon command."""

from copy import deepcopy
from dataclasses import replace

from loomstroke.core.japanese_text.speaker_styles import apply_style, resolve_style


def assign_speaker(
    tool, balloon_id, speaker_id, speaker_name, *, style=None, clear_overrides=False
):
    state = tool.read_state()
    if state is None or balloon_id not in state.balloons:
        return False
    record = replace(state.balloons[balloon_id], speaker_id=speaker_id, speaker_name=speaker_name)
    registry = getattr(tool.host_api.pages, "speaker_styles", None)
    if style is None:
        style = resolve_style(registry.read(), speaker_id, speaker_name) if registry else None
    obj = state.text_objects.get(balloon_id)
    if style is not None and obj is not None:
        payload = apply_style(obj.meta["text_tool"], style, clear_overrides=clear_overrides)
        updated = replace(obj, meta={**deepcopy(obj.meta), "text_tool": payload})
        state = replace(state, text_objects={**state.text_objects, balloon_id: updated})
    elif obj is not None and "speaker_style_ref" in obj.meta["text_tool"]:
        updated = deepcopy(obj)
        updated.meta["text_tool"].pop("speaker_style_ref")
        state = replace(state, text_objects={**state.text_objects, balloon_id: updated})
    command = tool.controller.update_balloon_command(state, record)
    return tool._submit(command)
