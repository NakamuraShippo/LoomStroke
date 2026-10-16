"""Remap frame references on an already detached, complete page snapshot."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from .content_identity_metadata import remap_content_identity
from .membership import BALLOON_KEY, FRONT_KEY, PANEL_KEY, PART_KEY, SHARED_KEY, TEXT_KEY, subtree
from .metadata import read_frame_layer
from .models import PanelFrameError
from .reference_remap import FrameReferenceRemap, remap_frame_metadata

LAYOUT_KEY = "loomstroke.comictools.panel_layout"


def remap_copied_page(  # noqa: PLR0912, PLR0915 - complete detached reference transaction
    source_layers, copied_layers, *, page_id
):
    """Plain pages retain their existing page-local vector identity contract.

    Materialized pages receive new comic/layout/part identities. No pixel,
    geometry, unknown metadata or original snapshot is changed here.
    """
    sources = [node for root in source_layers for node in subtree(root)]
    copies = [node for root in copied_layers for node in subtree(root)]
    if len(sources) != len(copies):
        raise PanelFrameError("copied page tree differs from its source")
    frames = []
    for node in sources:
        for obj in node.vector_objects or ():
            payload = obj.meta.get(PANEL_KEY)
            if payload and read_frame_layer(payload.get("metadata")):
                frames.append(payload)
    if not frames:
        return
    layer_ids = {old.id: new.id for old, new in zip(sources, copies, strict=True)}
    panels, nodes, edges, objects, balloons = {}, {}, {}, {}, {}

    def identity(mapping, value):
        if value:
            mapping.setdefault(value, f"comic-copy-{uuid4().hex}")
        return mapping.get(value, value)

    def gather_tree(node):
        identity(nodes, node["node_id"])
        identity(panels, node.get("panel_id"))
        for child in node.get("children", ()):
            gather_tree(child)

    for source in sources:
        layout = (source.metadata or {}).get("plugins", {}).get(LAYOUT_KEY)
        if layout:
            gather_tree(layout["tree"])
        marker = (source.metadata or {}).get(PART_KEY, {})
        identity(edges, marker.get("edge_id"))
        for obj in source.vector_objects or ():
            # All objects are page-local on this complete copy; linked text
            # stories must remap every member together, including ordinary text.
            identity(objects, obj.id)
            for key in (PANEL_KEY, BALLOON_KEY, TEXT_KEY):
                payload = obj.meta.get(key)
                if payload:
                    identity(panels, payload.get("panel_id"))
                    identity(balloons, payload.get("balloon_id"))
    for payload in frames:
        frame = read_frame_layer(payload["metadata"])
        for panel_id in frame.layout_leaf_ids:
            identity(panels, panel_id)
        for edge in frame.edges:
            identity(edges, edge.edge_id)
            identity(objects, edge.vector_restore_ref)
            identity(nodes, edge.boundary_ref.node_id)
        for edge in payload["metadata"]["frame_layer"].get("retired_edges", ()):
            identity(edges, edge["edge_id"])
            identity(objects, edge.get("vector_restore_ref"))
            identity(nodes, edge["boundary_ref"].get("node_id"))
    ids = FrameReferenceRemap(panels, layer_ids, edges, nodes, objects)

    def mapped(mapping, value):
        if value and value not in mapping:
            raise PanelFrameError(f"copied page has an unresolved comic reference: {value}")
        return mapping.get(value, value)

    def map_tree(node):
        node["node_id"] = mapped(nodes, node["node_id"])
        if node.get("panel_id"):
            node["panel_id"] = mapped(panels, node["panel_id"])
        for child in node.get("children", ()):
            map_tree(child)

    for copy in copies:
        metadata = remap_content_identity(copy.metadata, balloons)
        copy.metadata = metadata
        marker = metadata.get(PART_KEY, {})
        gate = metadata.get("loomstroke.comictools.panel_gate")
        if gate:
            metadata["loomstroke.comictools.panel_gate"] = {
                mapped(layer_ids, key): value for key, value in gate.items()
            }
        if marker.get("edge_id"):
            marker["edge_id"] = mapped(edges, marker["edge_id"])
        for key in ("paint_source_layer_ids", "raster_source_layer_ids"):
            if key in marker:
                marker[key] = [mapped(layer_ids, value) for value in marker[key]]
        front = metadata.get(FRONT_KEY)
        if front:
            front["panel_id"] = mapped(panels, front["panel_id"])
        shared = metadata.get(SHARED_KEY)
        if shared:
            shared["page_id"] = page_id
            if "group_id" in shared:
                shared["group_id"] = copy.id
            shared["panel_ids"] = [mapped(panels, p) for p in shared["panel_ids"]]
        layout = metadata.get("plugins", {}).get(LAYOUT_KEY)
        if layout:
            map_tree(layout["tree"])
            layout["reading_order"] = [mapped(panels, p) for p in layout["reading_order"]]
            angles = layout.get("metadata", {}).get("inclined_splits")
            if angles:
                layout["metadata"]["inclined_splits"] = {
                    mapped(nodes, node_id): value for node_id, value in angles.items()
                }
        updated = []
        for obj in copy.vector_objects or ():
            meta = obj.meta
            meta["layer_id"] = copy.id
            for key in (PANEL_KEY, BALLOON_KEY, TEXT_KEY):
                payload = meta.get(key)
                if not payload:
                    continue
                for field, mapping in (
                    ("panel_id", panels),
                    ("balloon_id", balloons),
                    ("text_object_id", objects),
                ):
                    if payload.get(field):
                        payload[field] = mapped(mapping, payload[field])
                if key == PANEL_KEY and read_frame_layer(payload.get("metadata")):
                    payload["metadata"] = remap_frame_metadata(payload["metadata"], ids)
            story = (meta.get("text_tool") or {}).get("linked_story")
            if story:
                if story.get("scope") == "document":
                    raise PanelFrameError("frame page copy requires a page-local linked story")
                story["id"] = identity(balloons, story["id"])
                story["member"] = mapped(objects, story["member"])
                for frame in story["frames"]:
                    frame["id"] = mapped(objects, frame["id"])
                    if frame.get("object_id"):
                        frame["object_id"] = mapped(objects, frame["object_id"])
                    if frame.get("layer_id"):
                        frame["layer_id"] = mapped(layer_ids, frame["layer_id"])
            updated.append(replace(obj, id=mapped(objects, obj.id), meta=meta))
        copy.vector_objects = updated
