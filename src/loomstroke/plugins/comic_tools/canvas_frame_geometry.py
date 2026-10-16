"""Resolve polygon provenance for the existing whole-page transform transaction."""

from loomstroke.core.comic_panels.page_geometry_registry import PanelPageGeometry

from .canvas_layout_transform import transform_layout_metadata
from .layout_tree import layout_from_payload
from .model import LAYOUT_LAYER_NAMESPACE
from .panel_convex_geometry import compute_convex_panels


def resolve_transformed_panels(payload, mode, page_size):
    metadata = {"plugins": {LAYOUT_LAYER_NAMESPACE: payload}}
    changed = transform_layout_metadata(metadata, mode, *page_size)
    if changed is None:
        return None
    size = page_size[::-1] if mode in {"rot90", "rot-90"} else page_size
    before = compute_convex_panels(layout_from_payload(payload), page_size)
    after = compute_convex_panels(
        layout_from_payload(changed["plugins"][LAYOUT_LAYER_NAMESPACE]), size
    )
    return {
        panel_id: PanelPageGeometry(
            polygon.points, after[panel_id].points, after[panel_id].boundaries
        )
        for panel_id, polygon in before.items()
    }
