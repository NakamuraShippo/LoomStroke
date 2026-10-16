"""Prepare detached edge pixels in the geometry worker, without GUI objects."""

from loomstroke.core.canvas_layer_model import _Layer
from loomstroke.core.comic_panels.membership import PART_KEY
from loomstroke.core.snapshot_vector_rasterizer import SnapshotVectorRasterizer

from .panel_render_preview_plan import geometry_preview_parts


def prepare_preview_vectors(repair):
    updates, insertions = geometry_preview_parts(repair)
    incoming = {part.id: part for part in insertions}
    rasterizer = SnapshotVectorRasterizer(width=repair.page_size[0], height=repair.page_size[1])
    results = {}
    for layer_id, changes in updates.items():
        if "vector_objects" not in changes:
            continue
        source = repair.state.panel_parts.get(layer_id)
        values = source.values if source else vars(incoming[layer_id])
        values = {**values, **changes}
        values.setdefault("name", layer_id)
        if (values.get("metadata") or {}).get(PART_KEY, {}).get("role") == "carrier":
            results[layer_id] = None
            continue
        if (
            values["layer_type"] != "vector"
            or not values["visible"]
            or (values.get("metadata") or {}).get(PART_KEY, {}).get("role") != "edge"
            or any("text_tool" in obj.meta for obj in values["vector_objects"])
        ):
            continue
        part = _Layer(
            id=layer_id,
            **{
                key: value
                for key, value in values.items()
                if key not in {"id", "parent_id", "children"}
            },
        )
        results[layer_id] = rasterizer.rasterize_for_preview(part, scale=1)
    repair.prepared_vector_rasters = results
    repair.prepared_vector_plan = repair.geometry_command()
    return repair
