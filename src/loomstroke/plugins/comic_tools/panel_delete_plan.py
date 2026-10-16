"""Explicit panel deletion preserves or removes content in the same history."""

import copy
from dataclasses import replace

from loomstroke.core.comic_panels.content_delete_command import PanelContentDeleteCommand
from loomstroke.core.comic_panels.content_release_command import PanelContentReleaseCommand
from loomstroke.core.comic_panels.edit_draft import PanelEditCommand
from loomstroke.core.comic_panels.layer_patch_command import PanelLayerPatchCommand
from loomstroke.core.comic_panels.membership import FRONT_KEY, PART_KEY, SHARED_KEY
from loomstroke.core.comic_panels.metadata import read_frame_layer
from loomstroke.core.comic_panels.models import PanelFrameError
from loomstroke.core.comic_panels.shared_metadata import read_shared_content, write_shared_content
from loomstroke.core.commands_layer_tree import ReparentLayerCommand

from . import layout_tree as lt
from .commands import ComicEditCommand
from .model import LAYOUT_LAYER_NAMESPACE
from .panel_convex_geometry import compute_convex_panels, has_inclined_cuts
from .panel_edit_gate import require_editable_parts, require_panel_editable
from .panel_frame_layout_adapter import layout_snapshot
from .panel_shared_plan import _shared_mask
from .panel_source_guard import source_guard


def remove_owned_leaves(layout, leaves):
    """Collapse empty branches and retain valid boundary skew counts."""
    removed = set(leaves)
    if not removed <= set(lt.panel_ids(layout)):
        raise PanelFrameError("deleted panel has missing layout leaves")

    def prune(node):
        if node.is_leaf:
            return None if node.panel_id in removed else node
        children = [(i, child) for i, c in enumerate(node.children) if (child := prune(c))]
        if not children:
            return None
        if len(children) == 1:
            return children[0][1]
        return replace(
            node,
            children=tuple(c for _, c in children),
            weights=tuple(node.weights[i] for i, _ in children),
            cut_skews=(
                tuple(node.cut_skews[i - 1] for i, _ in children[1:]) if node.cut_skews else ()
            ),
        )

    root = prune(layout.root)
    return (
        replace(
            layout,
            root=root,
            reading_order=tuple(p for p in layout.reading_order if p not in removed),
        )
        if root
        else None
    )


def delete_panel_command(state, panel_id, *, page_size, disposition):
    if disposition not in {"keep", "delete"}:
        raise PanelFrameError("choose whether to keep or delete panel content")
    frame = read_frame_layer(state.panel_records[panel_id].metadata)
    if frame is not None:
        require_panel_editable(state, panel_id, geometry=True)
    else:
        require_editable_parts(state, [state.panel_sources[panel_id][0]])
    units = state.panel_contents.get(frame.content_group_id, ()) if frame else ()
    require_editable_parts(state, [u.layer_id for u in units])
    layout = remove_owned_leaves(state.layout, frame.layout_leaf_ids if frame else (panel_id,))
    records = {p: r for p, r in state.panel_records.items() if p != panel_id}
    filtered = replace(
        state,
        panel_records=records,
        panel_sources={p: s for p, s in state.panel_sources.items() if p != panel_id},
        panel_parts={
            key: part
            for key, part in state.panel_parts.items()
            if SHARED_KEY not in (part.values["metadata"] or {})
        },
    )
    commands = [source_guard(state, page_size=page_size)]
    if layout is None:
        metadata = copy.deepcopy(state.frames_layer_metadata)
        metadata.get("plugins", {}).pop(LAYOUT_LAYER_NAMESPACE, None)
        commands.append(
            PanelLayerPatchCommand(
                state.frames_layer_id,
                before={"metadata": state.frames_layer_metadata},
                after={"metadata": metadata},
            )
        )
        rects, polygons = {}, {}
    elif state.panel_polygons or has_inclined_cuts(layout.root):
        from .panel_convex_plan import convex_layout_command  # noqa: PLC0415

        commands.append(
            convex_layout_command(filtered, layout, page_size=page_size, label="Delete panel")
        )
        polygons = compute_convex_panels(layout, page_size)
        rects = {p: shape.rect for p, shape in polygons.items()}
    else:
        from .panel_layout_plan import materialized_layout_command  # noqa: PLC0415

        commands.append(
            materialized_layout_command(filtered, layout, page_size=page_size, label="Delete panel")
        )
        rects = {r.panel_id: r.rect for r in layout_snapshot(layout, page_size, records).regions}
        polygons = {}
    planned = replace(filtered, panel_rects=rects, panel_polygons=polygons)
    # Shared pixels are never owned exclusively by the removed panel. Remove
    # only its display reference; the last view keeps its final ordinary mask.
    for group_id, part in state.panel_parts.items():
        if SHARED_KEY not in (part.values["metadata"] or {}):
            continue
        shared = read_shared_content(part.values["metadata"], group_id=group_id)
        targets = tuple(p for p in shared.panel_ids if p != panel_id)
        metadata = copy.deepcopy(part.values["metadata"])
        mask = part.values["layer_mask"]
        if targets:
            metadata = write_shared_content(metadata, replace(shared, panel_ids=targets))
            mask = _shared_mask(planned, targets, tuple(map(int, page_size)))
        else:
            metadata.pop(SHARED_KEY)
        commands.append(
            PanelLayerPatchCommand(
                group_id,
                before={
                    "metadata": part.values["metadata"],
                    "layer_mask": part.values["layer_mask"],
                },
                after={"metadata": metadata, "layer_mask": mask},
            )
        )
    commands.extend(dispose_panel_parts(state, panel_id, frame, units, disposition))
    return PanelEditCommand(commands, label=f"Delete panel and {disposition} content")


def dispose_panel_parts(state, panel_id, frame, units, disposition):
    if frame is None:
        return [ComicEditCommand(removals=[state.panel_sources[panel_id][1].id])]
    commands = release_content_groups(state, frame) if disposition == "keep" else []
    for unit in units:
        part = state.panel_parts[unit.layer_id]
        if FRONT_KEY not in (part.values["metadata"] or {}):
            if disposition == "delete":
                commands.append(PanelContentDeleteCommand(unit.layer_id))
            continue
        commands.append(
            ReparentLayerCommand(unit.layer_id, None)
            if disposition == "keep"
            else PanelContentDeleteCommand(unit.layer_id)
        )
    commands.extend(retire_frame_commands(state, panel_id, frame))
    return commands


def retire_frame_commands(state, panel_id, frame):
    # Another split fragment may reference this panel's hidden restoration
    # source. Keep frame parts out of display rather than leaving dangling IDs.
    commands = []
    source_id = state.panel_sources[panel_id][0]
    for layer_id, role in ((source_id, "retired_carrier"), (frame.panel_group_id, "retired_panel")):
        part = state.panel_parts[layer_id]
        metadata = copy.deepcopy(part.values["metadata"])
        metadata[PART_KEY]["role"] = role
        commands.append(
            PanelLayerPatchCommand(
                layer_id,
                before={"metadata": part.values["metadata"], "visible": part.values["visible"]},
                after={"metadata": metadata, "visible": False},
            )
        )
    return commands


def release_content_groups(state, frame):
    # Keep the original clip/display group as an ordinary page group. This
    # preserves its composite appearance, pixels and all user masks.
    commands = []
    for child_id in state.panel_parts[frame.panel_group_id].children:
        part = state.panel_parts[child_id]
        role = (part.values["metadata"] or {}).get(PART_KEY, {}).get("role")
        if role not in {"content", "overflow"} or not part.children:
            continue
        metadata = copy.deepcopy(part.values["metadata"])
        metadata.pop(PART_KEY)
        commands.extend(
            [
                PanelLayerPatchCommand(
                    child_id,
                    before={"metadata": part.values["metadata"]},
                    after={"metadata": metadata},
                ),
                PanelContentReleaseCommand(child_id, None),
            ]
        )
    return commands
