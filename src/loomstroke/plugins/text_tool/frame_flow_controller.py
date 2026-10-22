"""Expose linked-frame operations through the existing text selection and commands."""

from loomstroke.core.japanese_text.linked_story import story_objects, story_of
from loomstroke.core.japanese_text.story_commands import LinkTextFramesCommand
from loomstroke.core.path_text.metadata import path_settings

from .typography_widget import _ts


def prepare_inline_frame(tool):
    state = tool._inline_state
    if state is None:
        return
    from .common import ensure_text_vector_object  # noqa: PLC0415

    if state.object_ref is None:
        state.object_ref = ensure_text_vector_object(
            object_id=state.object_id or "text-inline-preview",
            content=tool._inline_editor.toPlainText(),
            rect=state.rect,
            settings=tool._inline_effective_settings(),
            word_wrap=True,
            rotation_deg=state.rotation_deg,
        )
    state.word_wrap = True


def story_allows_edit(tool):
    state = tool._inline_state
    story = story_of(state.object_ref) if state is not None else None
    if story is None:
        return True
    try:
        if story.get("scope") == "document":
            service = getattr(tool.host_api.pages, "document_stories", None)
            if service is None:
                return False
            service.edit_entries(state.object_ref)
            return True
        story_objects(tool.host_api.canvas, [entry["id"] for entry in story["frames"]])
    except ValueError:
        return False
    return True


def link_targets(tool, current):
    canvas = getattr(tool.host_api, "canvas", None)
    getter = getattr(canvas, "get_vector_objects", None)
    if current is None or not callable(getter):
        return []
    if path_settings(current.meta.get("text_tool")) is not None:
        return []
    service = getattr(getattr(tool.host_api, "pages", None), "document_stories", None)
    if service is not None:
        return [
            (dict(page_id=key[0], layer_id=key[1], object_id=key[2]), f"{key[0]}: {text}")
            for key, text in service.targets(current)
        ]
    story = story_of(current)
    excluded = {entry["id"] for entry in story["frames"]} if story else {str(current.id)}
    result = []
    for _layer_id, obj in getter(include_hidden=True):
        payload = (getattr(obj, "meta", None) or {}).get("text_tool")
        if (
            isinstance(payload, dict)
            and str(obj.id) not in excluded
            and path_settings(payload) is None
        ):
            text = str(payload.get("content", "")).replace("\n", " ")[:32]
            result.append((str(obj.id), text))
    return result


def apply_link_request(tool, request):
    if isinstance(request, dict) and request.get("action") == "pick":
        from .link_picker import picker  # noqa: PLC0415

        picker(tool).begin()
        return
    if isinstance(request, dict) and request.get("action") == "ordered":
        from .link_order_dialog import link_selection  # noqa: PLC0415

        link_selection(tool)
        return
    hit = tool._selected_text_hit()
    if hit is None or not isinstance(request, dict):
        return
    if hasattr(tool, "_link_picker"):
        tool._link_picker.cancel()
    if tool._inline_editor is not None and tool._inline_editor.preedit_text():
        return
    source_id, layer_id = str(hit.obj.id), hit.layer_id
    tool._finish_inline_edit(commit=True)
    target = request.get("target") if request.get("action") == "link" else None
    if request.get("action") not in {"link", "unlink"} or (
        request.get("action") == "link" and not target
    ):
        return
    service = getattr(getattr(tool.host_api, "pages", None), "document_stories", None)
    if service is not None and (
        isinstance(target, dict) or (story_of(hit.obj) or {}).get("scope") == "document"
    ):
        source = (tool.host_api.pages.get_active_page_id(), layer_id, source_id)
        destination = (
            (target["page_id"], target["layer_id"], target["object_id"]) if target else None
        )
        if not service.link(source, destination):
            tool._show_status(_ts("The text frames could not be linked."))
        tool._properties.sync(force=True)
        return
    command = LinkTextFramesCommand(source_id, target, layer_id=layer_id)
    canvas = getattr(tool.host_api, "canvas", None)
    if canvas is not None and not canvas.submit(command):
        tool._show_status(_ts(command.error or "The text frames could not be linked."))
    tool._properties.sync(force=True)
