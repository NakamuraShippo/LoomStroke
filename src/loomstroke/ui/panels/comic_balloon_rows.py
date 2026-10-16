"""Present a real, editable balloon unit as one logical layer row."""

from collections.abc import Mapping

from loomstroke.core.comic_panels.membership import UNIT_KEY
from loomstroke.plugins.comic_tools.model import (
    LAYER_ROLE_BALLOON_TEXT,
    LAYER_ROLE_BALLOONS,
    layer_comic_role,
)

from .comic_frame_rows import _badge, _name, ts


def balloon_id(layer):
    marker = (getattr(layer, "metadata", None) or {}).get(UNIT_KEY)
    if isinstance(marker, Mapping) and marker.get("version") == 1:
        value = marker.get("balloon_id")
        return value if isinstance(value, str) and value else None
    return None


def row_children(layer):
    return [] if balloon_id(layer) else (getattr(layer, "children", None) or [])


def visible_row(layer):
    return not (
        layer_comic_role(layer) in {LAYER_ROLE_BALLOONS, LAYER_ROLE_BALLOON_TEXT}
        and not getattr(layer, "vector_objects", None)
        and not getattr(layer, "children", None)
    )


def logical_layer_id(lookup, layer_id):
    layer = lookup.get(layer_id)
    seen = set()
    while layer is not None and layer.id not in seen:
        seen.add(layer.id)
        if balloon_id(layer):
            return layer.id
        layer = lookup.get(getattr(layer, "parent_id", None))
    return layer_id


def decorate_balloon_entry(entry, layer, panel):
    identity = balloon_id(layer)
    if not identity:
        return entry
    entry.children = None
    _badge(entry, "Balloon", "B")
    if entry.name in {"Balloon", identity}:
        _name(entry, "Balloon")
    # Reuse the visible part's cached thumbnail without rendering the whole page.
    image_part = next(
        (child for child in layer.children if getattr(child, "image", None) is not None), None
    )
    if image_part is not None:
        entry.thumbnail = panel._layer_thumbnail(image_part)
    return entry


def edit_balloon(panel, layer):
    identity = balloon_id(layer)
    if not identity:
        return False
    host = panel._host_api
    manager = getattr(host, "_tool_manager", None) or getattr(host, "tool_manager", None)
    state = getattr(manager, "_tools", {}).get("comic_tool")
    tool = getattr(state, "plugin", state)
    select = getattr(tool, "select_balloon", None)
    if callable(select):
        select(identity)
    return True


def add_legacy_group_action(menu, panel, layer_id):
    from loomstroke.plugins.comic_tools.balloon_layer_units import (  # noqa: PLC0415
        group_legacy_balloons_command,
        legacy_balloon_ids,
    )

    getter = getattr(panel, "_get_canvas", None)
    canvas = getter() if callable(getter) else None
    if canvas is None or not legacy_balloon_ids(canvas, layer_id):
        return
    action = menu.addAction(ts("Group Balloon Objects"))

    def group():
        command = group_legacy_balloons_command(canvas, layer_id)
        if canvas.submit(command):
            panel._add_default_layers()
        else:
            from PySide6.QtWidgets import QMessageBox  # noqa: PLC0415

            QMessageBox.warning(
                panel,
                ts("Balloon"),
                ts("Cannot group these balloons. The original layers have been kept."),
            )

    action.triggered.connect(group)
