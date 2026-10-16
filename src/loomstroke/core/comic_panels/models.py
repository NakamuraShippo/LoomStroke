"""Value records separating panel identity, frame parts and content references."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

Point = tuple[float, float]
Rect = tuple[float, float, float, float]
Side = Literal["top", "right", "bottom", "left"]
ResizePolicy = Literal["keep_pixels", "stretch_tangent"]


class PanelFrameError(ValueError):
    """Invalid or unsupported frame data; callers must retain the original data."""


def validate_rect(rect: Rect) -> None:
    if len(rect) != 4 or not all(math.isfinite(v) for v in rect) or min(rect[2:]) <= 0:
        raise PanelFrameError("panel rect must be finite and have positive dimensions")


@dataclass(frozen=True, slots=True)
class BoundaryRef:
    kind: Literal["page", "cut"]
    side: str
    node_id: str = ""
    index: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.node_id, str):
            raise PanelFrameError("boundary node identity must be a string")
        if self.kind == "page":
            valid = self.side in {"top", "right", "bottom", "left"} and not self.node_id
        else:
            valid = self.kind == "cut" and self.side in {"before", "after"} and bool(self.node_id)
        if not valid or type(self.index) is not int or self.index < 0:
            raise PanelFrameError("invalid boundary reference")


@dataclass(frozen=True, slots=True)
class RasterAnchor:
    source_xy_px: Point
    edge_t: float
    base_edge_length_px: float
    source_angle_rad: float = 0.0

    def __post_init__(self) -> None:
        values = (*self.source_xy_px, self.edge_t, self.base_edge_length_px, self.source_angle_rad)
        if len(self.source_xy_px) != 2 or not all(math.isfinite(v) for v in values):
            raise PanelFrameError("invalid raster anchor")
        if not 0 <= self.edge_t <= 1 or self.base_edge_length_px <= 0:
            raise PanelFrameError("invalid raster anchor range")


@dataclass(frozen=True, slots=True)
class FrameEdge:
    edge_id: str
    boundary_ref: BoundaryRef
    render_mode: Literal["vector", "raster"]
    part_layer_id: str
    owned_corners: tuple[str, ...] = ()
    raster_anchor: RasterAnchor | None = None
    raster_resize_policy: ResizePolicy = "keep_pixels"
    vector_restore_ref: str = ""
    side: str = ""
    span: tuple[float, float] = (0.0, 1.0)

    def __post_init__(self) -> None:
        if self.side not in {"", "top", "right", "bottom", "left"}:
            raise PanelFrameError("invalid rectangular edge side")
        if (
            len(self.span) != 2
            or not all(math.isfinite(v) for v in self.span)
            or not 0 <= self.span[0] < self.span[1] <= 1
        ):
            raise PanelFrameError("invalid rectangular edge span")
        if not isinstance(self.vector_restore_ref, str):
            raise PanelFrameError("restore identity must be a string")
        if (
            not self.edge_id
            or not self.part_layer_id
            or self.render_mode not in {"vector", "raster"}
        ):
            raise PanelFrameError("invalid frame edge")
        if self.render_mode == "raster" and self.raster_anchor is None:
            raise PanelFrameError("raster edge requires a source anchor")
        if self.raster_resize_policy not in {"keep_pixels", "stretch_tangent"}:
            raise PanelFrameError("unsupported raster resize policy")
        corners = {"top_left", "top_right", "bottom_left", "bottom_right"}
        if any(
            c not in corners and not (c.startswith("vertex:") and c[7:].isdigit())
            for c in self.owned_corners
        ) or len(set(self.owned_corners)) != len(self.owned_corners):
            raise PanelFrameError("invalid corner ownership")


@dataclass(frozen=True, slots=True)
class FrameLayer:
    """Persisted references only; region geometry and pixels have other authorities."""

    layout_leaf_ids: tuple[str, ...]
    panel_group_id: str
    content_group_id: str
    edges: tuple[FrameEdge, ...]
    clip_inset_px: float = 0.0
    region_version: int = 0
    clip_mask_region_version: int = 0
    geometry_mode: Literal["layout_tree_rect", "layout_tree_convex"] = "layout_tree_rect"

    def __post_init__(self) -> None:
        if self.geometry_mode not in {"layout_tree_rect", "layout_tree_convex"}:
            raise PanelFrameError("unsupported frame geometry mode")
        if not self.layout_leaf_ids or any(not p for p in self.layout_leaf_ids):
            raise PanelFrameError("frame requires layout leaves")
        if len(set(self.layout_leaf_ids)) != len(self.layout_leaf_ids):
            raise PanelFrameError("duplicate layout leaf")
        if not self.panel_group_id or not self.content_group_id:
            raise PanelFrameError("frame requires distinct panel and content groups")
        if self.panel_group_id == self.content_group_id:
            raise PanelFrameError("panel and content group must differ")
        if not math.isfinite(self.clip_inset_px) or self.clip_inset_px < 0:
            raise PanelFrameError("invalid clip inset")
        if any(
            type(v) is not int or v < 0
            for v in (self.region_version, self.clip_mask_region_version)
        ):
            raise PanelFrameError("invalid region version")
        if len({e.edge_id for e in self.edges}) != len(self.edges):
            raise PanelFrameError("duplicate edge identity")
        if len({e.part_layer_id for e in self.edges}) != len(self.edges):
            raise PanelFrameError("duplicate edge layer reference")
        if any(e.part_layer_id in {self.panel_group_id, self.content_group_id} for e in self.edges):
            raise PanelFrameError("edge layer must differ from panel/content group")
        owned = [corner for edge in self.edges for corner in edge.owned_corners]
        if len(set(owned)) != len(owned):
            raise PanelFrameError("corner has more than one owning edge")


@dataclass(frozen=True, slots=True)
class PanelRegion:
    panel_id: str
    layout_leaf_ids: tuple[str, ...]
    rect: Rect

    def __post_init__(self) -> None:
        validate_rect(self.rect)
        if not self.panel_id or not self.layout_leaf_ids:
            raise PanelFrameError("panel requires identity and leaves")


@dataclass(frozen=True, slots=True)
class ContentBounds:
    """One allocation unit; a balloon/group is never expanded into its parts."""

    item_id: str
    bounds: Rect | None

    def __post_init__(self) -> None:
        if not self.item_id:
            raise PanelFrameError("content requires identity")
        if self.bounds is not None:
            validate_rect(self.bounds)


@dataclass(frozen=True, slots=True)
class SharedContent:
    """One page-fixed source group; panel IDs are view references, not owners."""

    page_id: str
    group_id: str
    panel_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if (
            not self.page_id
            or not self.group_id
            or not self.panel_ids
            or any(not p for p in self.panel_ids)
        ):
            raise PanelFrameError("shared content requires page, source and targets")
        if len(set(self.panel_ids)) != len(self.panel_ids):
            raise PanelFrameError("duplicate shared display reference")
