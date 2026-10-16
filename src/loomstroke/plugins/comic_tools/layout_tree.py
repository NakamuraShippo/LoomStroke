"""Comic panel layout tree: pure geometry/structure model (Qt-free).

The panel layout of one page is a split tree ("レイアウトツリーを分割していく"
方式): every leaf is a panel, every inner node splits its area horizontally
or vertically into weighted children separated by the page gutter. Panel
rects are always DERIVED from the tree, so splits, merges, ratio drags, and
gutter changes stay consistent by construction. Diagonal/polygon panels are a
future extension of the materialized vector path, not of this tree.

Reading order is explicit: the layout carries an ordered list of panel ids
(default Japanese manga order: right-to-left, top-to-bottom). Automatic
ordering only fills in panels missing from the manual list — it never
silently reorders a manually confirmed sequence.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Literal

LAYOUT_SCHEMA_VERSION = 1
DEFAULT_GUTTER_PX = 24.0
DEFAULT_PAGE_MARGIN_PX = 0.0
_MIN_PANEL_PX = 16.0

Orientation = Literal["horizontal", "vertical"]

Rect = tuple[float, float, float, float]  # x, y, w, h


class ComicLayoutError(ValueError):
    """Raised for structurally invalid layout operations."""


@dataclass(frozen=True)
class LayoutNode:
    """One node of the split tree.

    ``panel_id`` is set on leaves. Inner nodes have an ``orientation``
    ("vertical" = children side by side, split by vertical cuts;
    "horizontal" = children stacked top to bottom), positive ``weights``
    (one per child), and ``children``.
    """

    node_id: str
    panel_id: str | None = None
    orientation: Orientation | None = None
    weights: tuple[float, ...] = ()
    children: tuple[LayoutNode, ...] = ()
    cut_skews: tuple[float, ...] = ()

    @property
    def is_leaf(self) -> bool:
        return self.panel_id is not None


@dataclass(frozen=True)
class ComicPageLayout:
    """The authoritative panel structure of one page."""

    root: LayoutNode
    gutter_px: float = DEFAULT_GUTTER_PX
    margin_px: float = DEFAULT_PAGE_MARGIN_PX
    reading_order: tuple[str, ...] = ()
    next_panel_number: int = 2
    next_node_number: int = 2
    schema_version: int = LAYOUT_SCHEMA_VERSION
    metadata: dict[str, Any] = field(default_factory=dict)


# ── construction ─────────────────────────────────────────────────────────


def create_single_panel_layout(*, gutter_px: float = DEFAULT_GUTTER_PX) -> ComicPageLayout:
    root = LayoutNode(node_id="n1", panel_id="panel-1")
    return ComicPageLayout(
        root=root,
        gutter_px=float(gutter_px),
        reading_order=("panel-1",),
    )


# ── serialization (page-metadata friendly plain JSON) ────────────────────


def layout_to_payload(layout: ComicPageLayout) -> dict[str, Any]:
    return {
        "schema_version": layout.schema_version,
        "gutter_px": float(layout.gutter_px),
        "margin_px": float(layout.margin_px),
        "reading_order": list(layout.reading_order),
        "next_panel_number": int(layout.next_panel_number),
        "next_node_number": int(layout.next_node_number),
        "tree": _node_to_payload(layout.root),
        "metadata": dict(layout.metadata),
    }


def layout_from_payload(payload: Mapping[str, Any] | None) -> ComicPageLayout | None:
    if not isinstance(payload, Mapping):
        return None
    tree = payload.get("tree")
    root = _node_from_payload(tree)
    if root is None:
        return None
    try:
        return ComicPageLayout(
            root=root,
            gutter_px=max(0.0, float(payload.get("gutter_px", DEFAULT_GUTTER_PX))),
            margin_px=max(0.0, float(payload.get("margin_px", DEFAULT_PAGE_MARGIN_PX))),
            reading_order=tuple(
                str(pid) for pid in payload.get("reading_order", []) if isinstance(pid, str)
            ),
            next_panel_number=max(1, int(payload.get("next_panel_number", 2))),
            next_node_number=max(1, int(payload.get("next_node_number", 2))),
            schema_version=int(payload.get("schema_version", LAYOUT_SCHEMA_VERSION)),
            metadata=dict(payload.get("metadata") or {}),
        )
    except (TypeError, ValueError):
        return None


def _node_to_payload(node: LayoutNode) -> dict[str, Any]:
    if node.is_leaf:
        return {"node_id": node.node_id, "panel_id": node.panel_id}
    payload = {
        "node_id": node.node_id,
        "orientation": node.orientation,
        "weights": [float(w) for w in node.weights],
        "children": [_node_to_payload(child) for child in node.children],
    }
    if node.cut_skews:
        payload["cut_skews"] = list(node.cut_skews)
    return payload


def _node_from_payload(payload: Any) -> LayoutNode | None:  # noqa: PLR0911 - codec rejection states
    if not isinstance(payload, Mapping):
        return None
    node_id = str(payload.get("node_id") or "")
    if not node_id:
        return None
    panel_id = payload.get("panel_id")
    if isinstance(panel_id, str) and panel_id:
        return LayoutNode(node_id=node_id, panel_id=panel_id)
    orientation = payload.get("orientation")
    if orientation not in ("horizontal", "vertical"):
        return None
    children = [_node_from_payload(child) for child in payload.get("children", [])]
    if not children or any(child is None for child in children):
        return None
    raw_weights = payload.get("weights", [])
    weights = tuple(max(0.01, float(w)) for w in raw_weights)
    if len(weights) != len(children):
        weights = tuple(1.0 for _ in children)
    raw_skews = payload.get("cut_skews", ())
    if not isinstance(raw_skews, (list, tuple)) or any(
        isinstance(v, bool) or not isinstance(v, (int, float)) for v in raw_skews
    ):
        return None
    skews = tuple(float(value) for value in raw_skews)
    if skews and (len(skews) != len(children) - 1 or not all(math.isfinite(v) for v in skews)):
        return None
    return LayoutNode(
        node_id=node_id,
        orientation=orientation,
        weights=weights,
        children=tuple(child for child in children if child is not None),
        cut_skews=skews,
    )


# ── derived geometry ─────────────────────────────────────────────────────


def compute_panel_rects(
    layout: ComicPageLayout,
    page_width: float,
    page_height: float,
) -> dict[str, Rect]:
    """Derive every panel's rect (page px) from the tree + gutter."""

    from .layout_geometry_space import layout_page_rect  # noqa: PLC0415

    rect = layout_page_rect(layout, (page_width, page_height))
    result: dict[str, Rect] = {}
    _fill_rects(layout.root, rect, max(0.0, float(layout.gutter_px)), result)
    return result


def _fill_rects(node: LayoutNode, rect: Rect, gutter: float, out: dict[str, Rect]) -> None:
    if node.is_leaf:
        assert node.panel_id is not None
        out[node.panel_id] = rect
        return
    x, y, w, h = rect
    count = len(node.children)
    total_weight = sum(node.weights) or float(count)
    if node.orientation == "vertical":
        # side-by-side children (vertical cut lines)
        usable = max(1.0, w - gutter * (count - 1))
        cursor = x
        for weight, child in zip(node.weights, node.children, strict=True):
            child_w = usable * (weight / total_weight)
            _fill_rects(child, (cursor, y, child_w, h), gutter, out)
            cursor += child_w + gutter
    else:
        usable = max(1.0, h - gutter * (count - 1))
        cursor = y
        for weight, child in zip(node.weights, node.children, strict=True):
            child_h = usable * (weight / total_weight)
            _fill_rects(child, (x, cursor, w, child_h), gutter, out)
            cursor += child_h + gutter


def panel_ids(layout: ComicPageLayout) -> tuple[str, ...]:
    ids: list[str] = []
    _collect_panel_ids(layout.root, ids)
    return tuple(ids)


def _collect_panel_ids(node: LayoutNode, out: list[str]) -> None:
    if node.is_leaf:
        assert node.panel_id is not None
        out.append(node.panel_id)
        return
    for child in node.children:
        _collect_panel_ids(child, out)


# ── structural edits (all pure: return a new layout) ─────────────────────


def split_panel(
    layout: ComicPageLayout,
    panel_id: str,
    orientation: Orientation,
    *,
    ratio: float = 0.5,
    count: int = 2,
) -> ComicPageLayout:
    """Split one panel into ``count`` panels along ``orientation``.

    ``ratio`` applies to the 2-way split (first child share); n-way splits
    are even. New panels get fresh ids and slot into the reading order right
    after the panel they came from.
    """

    if count < 2:
        raise ComicLayoutError("split count must be >= 2")
    ratio = min(0.95, max(0.05, float(ratio)))
    new_panel_ids = [f"panel-{layout.next_panel_number + i}" for i in range(count - 1)]
    node_id = f"n{layout.next_node_number}"
    leaf_ids = [f"n{layout.next_node_number + 1 + i}" for i in range(count)]

    def _rebuild(node: LayoutNode) -> LayoutNode:
        if node.is_leaf:
            if node.panel_id != panel_id:
                return node
            children = [LayoutNode(node_id=leaf_ids[0], panel_id=panel_id)]
            children += [
                LayoutNode(node_id=leaf_ids[i + 1], panel_id=new_panel_ids[i])
                for i in range(count - 1)
            ]
            weights = (ratio, 1.0 - ratio) if count == 2 else tuple(1.0 for _ in range(count))
            return LayoutNode(
                node_id=node_id,
                orientation=orientation,
                weights=weights,
                children=tuple(children),
            )
        return replace(node, children=tuple(_rebuild(child) for child in node.children))

    if panel_id not in panel_ids(layout):
        raise ComicLayoutError(f"unknown panel: {panel_id}")
    new_root = _rebuild(layout.root)
    order = list(layout.reading_order)
    if panel_id in order:
        at = order.index(panel_id) + 1
        order[at:at] = new_panel_ids
    else:
        order.extend([panel_id, *new_panel_ids])
    return replace(
        layout,
        root=new_root,
        reading_order=tuple(order),
        next_panel_number=layout.next_panel_number + (count - 1),
        next_node_number=layout.next_node_number + 1 + count,
    )


def remove_panel(layout: ComicPageLayout, panel_id: str) -> ComicPageLayout:
    """Delete a panel; its area is absorbed by the remaining siblings."""

    if layout.root.is_leaf:
        raise ComicLayoutError("cannot remove the last panel")

    def _rebuild(node: LayoutNode) -> LayoutNode | None:
        if node.is_leaf:
            return None if node.panel_id == panel_id else node
        kept: list[LayoutNode] = []
        kept_weights: list[float] = []
        for weight, child in zip(node.weights, node.children, strict=True):
            rebuilt = _rebuild(child)
            if rebuilt is not None:
                kept.append(rebuilt)
                kept_weights.append(weight)
        if not kept:
            return None
        if len(kept) == 1:
            return kept[0]
        return replace(node, children=tuple(kept), weights=tuple(kept_weights))

    if panel_id not in panel_ids(layout):
        raise ComicLayoutError(f"unknown panel: {panel_id}")
    new_root = _rebuild(layout.root)
    if new_root is None:
        raise ComicLayoutError("cannot remove the last panel")
    order = tuple(pid for pid in layout.reading_order if pid != panel_id)
    return replace(layout, root=new_root, reading_order=order)


def merge_panels(layout: ComicPageLayout, panel_a: str, panel_b: str) -> ComicPageLayout:
    """Merge two ADJACENT SIBLING panels into one (keeps ``panel_a``).

    Only leaves that are neighbouring children of the same split can merge —
    that is exactly the "隣接コマとの結合" the tree can express losslessly.
    """

    def _rebuild(node: LayoutNode) -> LayoutNode:
        if node.is_leaf:
            return node
        ids = [child.panel_id for child in node.children]
        if panel_a in ids and panel_b in ids:
            ia, ib = ids.index(panel_a), ids.index(panel_b)
            if abs(ia - ib) != 1:
                raise ComicLayoutError("panels are not adjacent siblings")
            first, second = min(ia, ib), max(ia, ib)
            merged_weight = node.weights[first] + node.weights[second]
            merged_leaf = LayoutNode(node_id=node.children[first].node_id, panel_id=panel_a)
            children = list(node.children)
            weights = list(node.weights)
            children[first : second + 1] = [merged_leaf]
            weights[first : second + 1] = [merged_weight]
            if len(children) == 1:
                return merged_leaf
            return replace(node, children=tuple(children), weights=tuple(weights))
        return replace(node, children=tuple(_rebuild(child) for child in node.children))

    ids = panel_ids(layout)
    if panel_a not in ids or panel_b not in ids:
        raise ComicLayoutError("unknown panel")
    new_root = _rebuild(layout.root)
    if set(panel_ids(replace(layout, root=new_root))) == set(ids):
        raise ComicLayoutError("panels are not adjacent siblings")
    order = tuple(pid for pid in layout.reading_order if pid != panel_b)
    return replace(layout, root=new_root, reading_order=order)


def set_gutter(layout: ComicPageLayout, gutter_px: float) -> ComicPageLayout:
    return replace(layout, gutter_px=max(0.0, float(gutter_px)))


def set_boundary_ratio(
    layout: ComicPageLayout,
    node_id: str,
    boundary_index: int,
    position: float,
) -> ComicPageLayout:
    """Move the boundary after child ``boundary_index`` of split ``node_id``.

    ``position`` is the boundary's normalized location (0..1) along the
    node's full extent; the two adjacent children exchange weight, all other
    children keep theirs.
    """

    def _rebuild(node: LayoutNode) -> LayoutNode:
        if node.is_leaf:
            return node
        if node.node_id == node_id:
            if not 0 <= boundary_index < len(node.children) - 1:
                raise ComicLayoutError("invalid boundary index")
            weights = list(node.weights)
            total = sum(weights)
            before = sum(weights[: boundary_index + 1])
            pair = weights[boundary_index] + weights[boundary_index + 1]
            # normalized start of the adjacent pair
            pair_start = (before - weights[boundary_index]) / total
            target = min(
                pair_start + pair / total - 0.02,
                max(pair_start + 0.02, float(position)),
            )
            first_share = (target - pair_start) * total
            weights[boundary_index] = max(0.02, first_share)
            weights[boundary_index + 1] = max(0.02, pair - weights[boundary_index])
            return replace(node, weights=tuple(weights))
        return replace(node, children=tuple(_rebuild(child) for child in node.children))

    return replace(layout, root=_rebuild(layout.root))


def set_split_ratio(layout: ComicPageLayout, panel_id: str, ratio: float) -> ComicPageLayout:
    """Set the 2-way ratio of the split that directly contains ``panel_id``."""

    ratio = min(0.95, max(0.05, float(ratio)))

    def _rebuild(node: LayoutNode) -> LayoutNode:
        if node.is_leaf:
            return node
        ids = [child.panel_id for child in node.children]
        if panel_id in ids and len(node.children) == 2:
            index = ids.index(panel_id)
            weights = (ratio, 1.0 - ratio) if index == 0 else (1.0 - ratio, ratio)
            return replace(node, weights=weights)
        return replace(node, children=tuple(_rebuild(child) for child in node.children))

    return replace(layout, root=_rebuild(layout.root))


# ── reading order ────────────────────────────────────────────────────────


def auto_reading_order(
    rects: Mapping[str, Rect],
    *,
    direction: str = "rtl",
) -> tuple[str, ...]:
    """Suggest a manga reading order from panel positions.

    "rtl" (Japanese manga): rows top-to-bottom, right-to-left within a row.
    "ltr": rows top-to-bottom, left-to-right.
    """

    items = sorted(rects.items(), key=lambda kv: kv[1][1])
    rows: list[list[tuple[str, Rect]]] = []
    for pid, rect in items:
        placed = False
        for row in rows:
            _, (_rx, ry, _rw, rh) = row[0]
            if rect[1] < ry + rh * 0.5 and ry < rect[1] + rect[3] * 0.5:
                row.append((pid, rect))
                placed = True
                break
        if not placed:
            rows.append([(pid, rect)])
    ordered: list[str] = []
    for row in rows:
        reverse = direction == "rtl"
        row.sort(key=lambda kv: kv[1][0], reverse=reverse)
        ordered.extend(pid for pid, _ in row)
    return tuple(ordered)


def normalized_reading_order(
    layout: ComicPageLayout,
    rects: Mapping[str, Rect],
    *,
    direction: str = "rtl",
) -> tuple[str, ...]:
    """The effective reading order: manual entries first (existing panels
    only, in their manual sequence), auto-positioned panels appended."""

    existing = set(rects)
    manual = [pid for pid in layout.reading_order if pid in existing]
    missing = [pid for pid in auto_reading_order(rects, direction=direction) if pid not in manual]
    return tuple([*manual, *missing])


def move_reading_order(layout: ComicPageLayout, panel_id: str, delta: int) -> ComicPageLayout:
    order = list(layout.reading_order)
    if panel_id not in order:
        raise ComicLayoutError(f"panel not in reading order: {panel_id}")
    index = order.index(panel_id)
    target = min(len(order) - 1, max(0, index + delta))
    order.insert(target, order.pop(index))
    return replace(layout, reading_order=tuple(order))


# ── boundary hit test (for drag UX) ──────────────────────────────────────


@dataclass(frozen=True)
class BoundaryHit:
    node_id: str
    boundary_index: int
    orientation: Orientation
    #: normalized position along the node extent (feed to set_boundary_ratio)
    axis_start: float
    axis_extent: float


def boundary_hit_test(
    layout: ComicPageLayout,
    page_width: float,
    page_height: float,
    point: tuple[float, float],
    *,
    tolerance: float = 8.0,
) -> BoundaryHit | None:
    from .layout_geometry_space import layout_page_rect  # noqa: PLC0415

    rect = layout_page_rect(layout, (page_width, page_height))
    return _hit_node(layout.root, rect, max(0.0, float(layout.gutter_px)), point, tolerance)


def _hit_node(
    node: LayoutNode,
    rect: Rect,
    gutter: float,
    point: tuple[float, float],
    tolerance: float,
) -> BoundaryHit | None:
    if node.is_leaf:
        return None
    x, y, w, h = rect
    px, py = point
    count = len(node.children)
    total_weight = sum(node.weights) or float(count)
    if node.orientation == "vertical":
        usable = max(1.0, w - gutter * (count - 1))
        cursor = x
        for index, (weight, child) in enumerate(zip(node.weights, node.children, strict=True)):
            child_w = usable * (weight / total_weight)
            child_rect: Rect = (cursor, y, child_w, h)
            if index < count - 1:
                boundary_x = cursor + child_w + gutter * 0.5
                if abs(px - boundary_x) <= tolerance + gutter * 0.5 and y <= py <= y + h:
                    return BoundaryHit(node.node_id, index, "vertical", x, w)
            hit = _hit_node(child, child_rect, gutter, point, tolerance)
            if hit is not None:
                return hit
            cursor += child_w + gutter
    else:
        usable = max(1.0, h - gutter * (count - 1))
        cursor = y
        for index, (weight, child) in enumerate(zip(node.weights, node.children, strict=True)):
            child_h = usable * (weight / total_weight)
            child_rect = (x, cursor, w, child_h)
            if index < count - 1:
                boundary_y = cursor + child_h + gutter * 0.5
                if abs(py - boundary_y) <= tolerance + gutter * 0.5 and x <= px <= x + w:
                    return BoundaryHit(node.node_id, index, "horizontal", y, h)
            hit = _hit_node(child, child_rect, gutter, point, tolerance)
            if hit is not None:
                return hit
            cursor += child_h + gutter
    return None


# ── templates ────────────────────────────────────────────────────────────


def template_layout(  # noqa: PLR0911 - one return per preset
    template_id: str, *, gutter_px: float = DEFAULT_GUTTER_PX
) -> ComicPageLayout:
    """Build a preset layout. Known ids: single, 2v, 2h, 3, 4, yonkoma, 6."""

    layout = create_single_panel_layout(gutter_px=gutter_px)
    if template_id in ("single", "1"):
        return layout
    if template_id == "2v":
        return split_panel(layout, "panel-1", "vertical")
    if template_id == "2h":
        return split_panel(layout, "panel-1", "horizontal")
    if template_id == "3":
        layout = split_panel(layout, "panel-1", "horizontal", ratio=0.4)
        return split_panel(layout, "panel-2", "vertical")
    if template_id == "4":
        layout = split_panel(layout, "panel-1", "horizontal")
        layout = split_panel(layout, "panel-1", "vertical")
        return split_panel(layout, "panel-2", "vertical")
    if template_id == "yonkoma":
        layout = split_panel(layout, "panel-1", "horizontal", count=4)
        return layout
    if template_id == "6":
        layout = split_panel(layout, "panel-1", "horizontal", count=3)
        layout = split_panel(layout, "panel-1", "vertical")
        layout = split_panel(layout, "panel-2", "vertical")
        return split_panel(layout, "panel-3", "vertical")
    raise ComicLayoutError(f"unknown template: {template_id}")


TEMPLATE_IDS: tuple[str, ...] = ("single", "2v", "2h", "3", "4", "yonkoma", "6")


__all__ = [
    "DEFAULT_GUTTER_PX",
    "LAYOUT_SCHEMA_VERSION",
    "TEMPLATE_IDS",
    "BoundaryHit",
    "ComicLayoutError",
    "ComicPageLayout",
    "LayoutNode",
    "auto_reading_order",
    "boundary_hit_test",
    "compute_panel_rects",
    "create_single_panel_layout",
    "layout_from_payload",
    "layout_to_payload",
    "merge_panels",
    "move_reading_order",
    "normalized_reading_order",
    "panel_ids",
    "remove_panel",
    "set_boundary_ratio",
    "set_gutter",
    "set_split_ratio",
    "split_panel",
    "template_layout",
]
