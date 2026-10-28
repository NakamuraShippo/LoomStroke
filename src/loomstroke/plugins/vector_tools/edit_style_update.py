"""Apply explicit path-tool style settings to the committed selected object."""

from __future__ import annotations

from typing import Any

from loomstroke.core.native_cmyk.vector_paints import without_paint

from .common import VectorReplaceCommand, VectorStyle, copy_vector_object, normalize_rgb

_STYLE_KEYS = frozenset(("stroke_color", "stroke_width", "fill_enabled", "fill_color"))


def committed_selection_object(tool: Any) -> Any | None:
    """Use the same layer/object lookup as direct-width edits, without preview."""
    selection = tool._selected
    canvas = getattr(getattr(tool, "host_api", None), "canvas", None)
    if selection is None or canvas is None:
        return None
    snapshot_getter = getattr(canvas, "get_vector_state_snapshot", None)
    if callable(snapshot_getter):
        layer = snapshot_getter().layer(selection.layer_id)
        if layer is None:
            return None
        return next((obj for obj in layer.vector_objects if obj.id == selection.vec_id), None)
    for layer_id, value in tool._vector_store():
        obj = value[1] if isinstance(value, tuple) else value
        if layer_id == selection.layer_id and getattr(obj, "id", None) == selection.vec_id:
            return obj
    return None


def set_style_settings(tool: Any, settings: dict[str, object]) -> None:
    settings = dict(settings or {})
    if "edit_width_on_canvas" in settings:
        tool._width_editor.set_enabled(bool(settings.pop("edit_width_on_canvas")))
    tool._hydrate_selection_from_canvas(committed=True)
    merged = dict(tool._style_settings)
    merged.update(settings)
    merged["stroke_color"] = normalize_rgb(merged.get("stroke_color")) or (0, 0, 0)
    merged["fill_color"] = normalize_rgb(merged.get("fill_color"))
    if bool(merged.get("fill_enabled")) and merged.get("fill_color") is None:
        merged["fill_color"] = (0, 0, 0)
    if merged.get("fill_color") is None:
        merged["fill_enabled"] = False
    clear_roles = tuple(role for role in ("stroke", "fill") if f"{role}_color" in settings)
    tool._style_settings = merged
    apply_style_settings_to_selection(
        tool, clear_roles=clear_roles, changed_keys=_STYLE_KEYS.intersection(settings)
    )


def set_style_setting(tool: Any, key: str, value: object) -> None:
    if key not in tool._style_settings:
        return
    tool._hydrate_selection_from_canvas(committed=True)
    changed_keys = {key}
    if key == "stroke_color":
        normalized = normalize_rgb(value)
        if normalized is None:
            return
        tool._style_settings[key] = normalized
    elif key == "fill_color":
        normalized = normalize_rgb(value)
        tool._style_settings[key] = normalized
        tool._style_settings["fill_enabled"] = normalized is not None
        changed_keys.add("fill_enabled")
    elif key == "fill_enabled":
        tool._style_settings[key] = bool(value)
    elif key == "stroke_width":
        try:
            tool._style_settings[key] = max(0.1, min(128.0, float(value)))
        except (TypeError, ValueError):
            return
    else:
        tool._style_settings[key] = value
    apply_style_settings_to_selection(
        tool,
        clear_roles=(key.removesuffix("_color"),) if key.endswith("_color") else (),
        changed_keys=changed_keys,
    )


def apply_style_settings_to_selection(
    tool: Any,
    *,
    clear_roles: tuple[str, ...] = (),
    changed_keys: set[str] | frozenset[str] = _STYLE_KEYS,
) -> None:
    if not tool._selected:
        tool._hydrate_selection_from_canvas(committed=True)
    obj = committed_selection_object(tool)
    if obj is None:
        return
    style = getattr(obj, "style", None)
    values = {
        key: tool._style_settings[key]
        for key in _STYLE_KEYS.intersection(changed_keys)
        if key in tool._style_settings
    }
    fill_color = values.get("fill_color", getattr(style, "fill_color", None))
    fill_enabled = bool(values.get("fill_enabled", getattr(style, "fill_enabled", False)))
    if "fill_color" in values and fill_color is None:
        fill_enabled = False
    if "fill_enabled" in values and fill_enabled and fill_color is None:
        fill_color = (0, 0, 0)
    style_updated = VectorStyle(
        stroke_color=tuple(values.get("stroke_color", getattr(style, "stroke_color", (0, 0, 0)))),
        stroke_width=(
            float(values["stroke_width"] or 2.0)
            if "stroke_width" in values
            else getattr(style, "stroke_width", 2.0)
        ),
        fill_enabled=fill_enabled,
        fill_color=fill_color,
        join=getattr(style, "join", "miter"),
        cap=getattr(style, "cap", "butt"),
        dash=getattr(style, "dash", ()),
        miter_limit=getattr(style, "miter_limit", 4.0),
    )
    if isinstance(style, VectorStyle) and style_updated == style:
        tool._editing_object = copy_vector_object(obj)
        tool._selected_object_cache = obj
        tool._update_style_settings_from_object(obj)
        if not tool._dragging and not tool._transform_mode:
            tool._update_canvas_selection(
                tool._selected.vec_id,
                tool._selected.path_idx,
                tool._selected.anchor_idx,
                tool._selected.layer_id,
                preview=False,
                transform_bounds=tool._compute_bbox(obj),
                mode=None,
                notify=False,
            )
        return
    meta = dict(getattr(obj, "meta", {}) or {})
    for role in clear_roles:
        if getattr(style_updated, f"{role}_color") != getattr(style, f"{role}_color", None):
            meta = without_paint(meta, role)
    new_obj = type(obj)(
        id=getattr(obj, "id", None),
        paths=list(getattr(obj, "paths", []) or []),
        style=style_updated,
        meta=meta,
        preview=getattr(obj, "preview", False),
    )
    cmd = VectorReplaceCommand(tool._selected.vec_id, new_obj, layer_id=tool._selected.layer_id)
    try:
        success = tool.host_api.canvas.submit(cmd, merge_key=None) if tool.host_api else False
    except Exception:
        success = False
    if not success:
        tool._pending_commands.append(cmd)
    tool._editing_object = copy_vector_object(new_obj)
    tool._update_style_settings_from_object(new_obj)
    tool._update_canvas_selection(
        tool._selected.vec_id,
        tool._selected.path_idx,
        tool._selected.anchor_idx,
        tool._selected.layer_id,
        preview=False,
        transform_bounds=tool._compute_bbox(new_obj),
        mode=None,
    )
