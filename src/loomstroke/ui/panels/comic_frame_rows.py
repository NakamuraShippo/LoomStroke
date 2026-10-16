"""Logical comic rows retain real content/edge IDs and hide registry backups."""

from loomstroke.core.comic_panels.membership import FRONT_KEY, PANEL_KEY, PART_KEY, SHARED_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.ui.translations import translated_literal_domain

PARTS_ROW = "comic_frame_parts"
_SIDE_ROW_PREFIX = "comic-frame-side:"
_SIDE_LABELS = {"top": "Top", "right": "Right", "bottom": "Bottom", "left": "Left"}


def ts(source):
    return translated_literal_domain("ComicPanelFrames", source, use_fallback=True)


def _name(entry, source, **values):
    entry._comic_name = (source, values)
    entry.name = ts(source).format(**values)


def _badge(entry, source, symbol):
    entry._comic_badge = (source, symbol)
    entry.badge = symbol


def apply_comic_row_labels(widget, entry):
    name = getattr(entry, "_comic_name", None)
    if name:
        entry.name = ts(name[0]).format(**name[1])
        widget.name_label.setText(entry.name)
    badge = getattr(entry, "_comic_badge", None)
    if badge:
        widget._set_badge(badge[1], ts(badge[0]))
    button = getattr(widget, "_comic_source_button", None)
    if button:
        button.setText(ts("Go to source"))


def refresh_comic_rows(panel):
    for key, entry in panel._all_entries.items():
        widget = panel._widget_map.get(key)
        if widget:
            apply_comic_row_labels(widget, entry)


def _is_frame_expansion_row(panel, key):
    name = getattr(panel._all_entries.get(key), "_comic_name", None)
    return key.startswith(_SIDE_ROW_PREFIX) or (name is not None and name[0] == "Panel {number}")


def capture_frame_expansion(panel):
    return {
        key: item.isExpanded()
        for key, item in panel._item_map.items()
        if _is_frame_expansion_row(panel, key)
    }


def restore_frame_expansion(panel, expanded):
    for key, item in panel._item_map.items():
        if _is_frame_expansion_row(panel, key):
            item.setExpanded(expanded.get(key, False))


def _side_rows(layer_id, edges, entry_type, geometry_mode):
    if geometry_mode != "layout_tree_rect":
        return edges
    result = []
    for side, source in _SIDE_LABELS.items():
        pieces = [row for row in edges if row._comic_name[0] == source]
        if len(pieces) <= 1:
            result.extend(pieces)
            continue
        row = entry_type(
            f"{_SIDE_ROW_PREFIX}{layer_id}:{side}", "", layer_type=PARTS_ROW, children=pieces
        )
        _name(row, source)
        result.append(row)
    return result


def decorate_frame_entry(entry, layer, build_entry, entry_type, lookup=None):
    marker = (getattr(layer, "metadata", None) or {}).get(PART_KEY, {})
    if marker.get("role") in {"overflow", "page_front"}:
        _name(entry, "Overflow" if marker["role"] == "overflow" else "Page front")
    if marker.get("role") != "panel":
        return entry
    payload = next(
        (
            obj.meta[PANEL_KEY]
            for child in layer.children
            if (child.metadata or {}).get(PART_KEY, {}).get("role") == "carrier"
            for obj in child.vector_objects
            if PANEL_KEY in obj.meta
        ),
        None,
    )
    if payload is None:
        return entry
    try:
        frame = read_frame_layer(payload.get("metadata"))
    except PanelFrameError:
        # Keep the ordinary tree available for inspection/recovery; only the
        # specialized decoration is unavailable for unknown or invalid data.
        return entry
    if frame is None:
        return entry
    _name(entry, "Panel {number}", number=payload.get("reading_order", 0) + 1)
    _badge(entry, "Frame", "▣")
    children = {child.id: child for child in layer.children}
    if frame.content_group_id not in children:
        return entry
    contents = build_entry(children[frame.content_group_id])
    _name(contents, "Contents")
    edges = []
    for index, edge in enumerate(frame.edges):
        part = children.get(edge.part_layer_id)
        if part is None:
            continue
        row = build_entry(part)
        side = edge.side or ("top", "right", "bottom", "left")[index % 4]
        if frame.geometry_mode == "layout_tree_convex":
            _name(
                row,
                (
                    "Page boundary {number}"
                    if edge.boundary_ref.kind == "page"
                    else "Split boundary {number}"
                ),
                number=index + 1,
            )
        else:
            _name(row, _SIDE_LABELS[side])
        _badge(
            row,
            "Pixels" if edge.render_mode == "raster" else "Vector",
            "P" if edge.render_mode == "raster" else "V",
        )
        edges.append(row)
    edges = _side_rows(layer.id, edges, entry_type, frame.geometry_mode)
    parts = entry_type(
        f"comic-frame-parts:{layer.id}", ts("Frame lines"), layer_type=PARTS_ROW, children=edges
    )
    _name(parts, "Frame lines")
    known = {frame.content_group_id, *(edge.part_layer_id for edge in frame.edges)}
    known.update(
        e["part_layer_id"] for e in payload["metadata"]["frame_layer"].get("retired_edges", ())
    )
    extras = [
        build_entry(child)
        for child in reversed(layer.children)
        if child.id not in known
        and (child.metadata or {}).get(PART_KEY, {}).get("role")
        not in {"carrier", "restore", "retired_panel", "retired_carrier"}
    ]
    references = []
    for source in (lookup or {}).values():
        metadata = source.metadata or {}
        front = metadata.get(FRONT_KEY, {})
        shared = metadata.get(SHARED_KEY, {})
        if front.get("panel_id") == payload["panel_id"] or payload["panel_id"] in shared.get(
            "panel_ids", ()
        ):
            reference = entry_type(
                f"comic-frame-reference:{layer.id}:{source.id}",
                str(source.name),
                layer_type="comic_frame_reference",
                comic_source_id=source.id,
            )
            _badge(reference, "Page front" if front else "Shared source", "↗" if front else "∞")
            references.append(reference)
    entry.children = [contents, *extras, *references, parts]
    return entry
