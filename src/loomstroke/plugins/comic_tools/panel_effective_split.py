"""Split a merged rectangle by assigning and, where necessary, refining base leaves."""

from dataclasses import replace

from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.regions import resolve_regions

from . import layout_tree as lt


def split_effective_panel(state, panel_id, orientation, ratio, *, page_size):
    if orientation not in {"vertical", "horizontal"} or not 0.05 <= ratio <= 0.95:
        raise PanelFrameError("invalid split orientation or ratio")
    frame = read_frame_layer(state.panel_records[panel_id].metadata)
    rect = state.panel_rects[panel_id]
    axis = 0 if orientation == "vertical" else 1
    gutter = state.layout.gutter_px
    cut = rect[axis] + (rect[axis + 2] - gutter) * ratio + gutter / 2
    low, high = cut - gutter / 2, cut + gutter / 2
    layout = state.layout
    before, after = [], []
    leaves = lt.compute_panel_rects(layout, *page_size)
    for leaf_id in frame.layout_leaf_ids:
        leaf = leaves[leaf_id]
        start, end = leaf[axis], leaf[axis] + leaf[axis + 2]
        if end <= low + 1e-6:
            before.append(leaf_id)
        elif start >= high - 1e-6:
            after.append(leaf_id)
        elif start < low and end > high:
            first_length, last_length = low - start, end - high
            if min(first_length, last_length) < 0.01:
                raise PanelFrameError("split leaves less than a valid base fragment")
            new_id = f"panel-{layout.next_panel_number}"
            node_id = f"n{layout.next_node_number}"

            split = lt.LayoutNode(
                node_id,
                orientation=orientation,
                weights=(first_length, last_length),
                children=(
                    lt.LayoutNode(node_id + "-a", panel_id=leaf_id),
                    lt.LayoutNode(node_id + "-b", panel_id=new_id),
                ),
            )

            layout = replace(
                layout,
                root=_replace_leaf(layout.root, leaf_id, split),
                next_panel_number=layout.next_panel_number + 1,
                next_node_number=layout.next_node_number + 1,
            )
            before.append(leaf_id)
            after.append(new_id)
        else:
            raise PanelFrameError(
                "split overlaps a previous gutter; choose its centre or move the cut"
            )
    if not before or not after:
        raise PanelFrameError("split would produce an empty panel")
    new_panel_id = f"panel-{layout.next_panel_number}"
    owners = {
        pid: read_frame_layer(record.metadata).layout_leaf_ids
        for pid, record in state.panel_records.items()
    }
    owners[panel_id] = tuple(before)
    owners[new_panel_id] = tuple(after)
    order = list(state.reading_order)
    order.insert(order.index(panel_id) + 1, new_panel_id)
    layout = replace(
        layout, reading_order=tuple(order), next_panel_number=layout.next_panel_number + 1
    )
    regions = resolve_regions(lt.compute_panel_rects(layout, *page_size), owners, gutter_px=gutter)
    children = tuple(r for r in regions if r.panel_id in {panel_id, new_panel_id})
    return layout, owners, children


def _replace_leaf(node, leaf_id, replacement):
    if node.is_leaf:
        return replacement if node.panel_id == leaf_id else node
    return replace(
        node, children=tuple(_replace_leaf(child, leaf_id, replacement) for child in node.children)
    )
