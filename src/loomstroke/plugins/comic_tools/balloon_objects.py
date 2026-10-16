"""Procedural balloon/panel vector-object builders.

A balloon is never a flat picture: the body (parametric speech shape) and
the tail (params: bbox anchor → target tip, width) are separate structures
composed into one outline at build time, and every edit (move, resize, tail
drag, auto-size) regenerates the outline from the parameters.
"""

from __future__ import annotations

from typing import Any

from loomstroke.plugins.vector_tools.common import (
    Anchor,
    VectorObject,
    VectorPath,
    VectorStyle,
)
from loomstroke.plugins.vector_tools.parametric_shapes import (
    build_parametric_shape_object,
    tail_params_for_tip,
)

from .model import (
    BALLOON_META_KEY,
    BODY_GEOMETRY_HAND_DRAWN,
    BODY_GEOMETRY_RASTER,
    PANEL_META_KEY,
    TAIL_GEOMETRY_HAND_DRAWN,
    BalloonRecord,
    PanelRecord,
    denormalize_outline,
)

#: raster-preserve balloons draw their pixels on the part layer; the vector
#: object is only the record carrier + hit-test helper, so it renders nothing
_INVISIBLE_STYLE = VectorStyle(stroke_width=0.0, fill_enabled=False)

PANEL_STYLE = VectorStyle(
    stroke_color=(17, 17, 17),
    stroke_width=5.0,
    fill_enabled=False,
    join="miter",
    cap="butt",
)

BALLOON_STYLE = VectorStyle(
    stroke_color=(17, 17, 17),
    stroke_width=3.0,
    fill_enabled=True,
    fill_color=(255, 255, 255),
    join="round",
    cap="round",
)


def build_panel_object(
    record: PanelRecord,
    rect: tuple[float, float, float, float],
    *,
    style: VectorStyle = PANEL_STYLE,
) -> VectorObject:
    """A panel frame object. The path is the geometry authority — a rect for
    now, polygon-capable by construction (any closed anchor loop works)."""

    x, y, w, h = rect
    path = VectorPath(
        anchors=[
            Anchor((x, y)),
            Anchor((x + w, y)),
            Anchor((x + w, y + h)),
            Anchor((x, y + h)),
        ],
        closed=True,
    )
    return VectorObject(
        id=f"comic-panel-{record.panel_id}",
        paths=[path],
        style=style,
        meta={PANEL_META_KEY: record.to_meta()},
    )


def panel_rect_from_object(obj: Any) -> tuple[float, float, float, float] | None:
    paths = getattr(obj, "paths", None)
    if not paths:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for anchor in paths[0].anchors:
        xs.append(float(anchor.pos[0]))
        ys.append(float(anchor.pos[1]))
    if not xs:
        return None
    return (min(xs), min(ys), max(xs) - min(xs), max(ys) - min(ys))


def build_balloon_object(
    record: BalloonRecord,
    *,
    style: VectorStyle = BALLOON_STYLE,
) -> VectorObject | None:
    """Compose body + tail into the balloon outline object."""

    if record.body_geometry_mode == BODY_GEOMETRY_RASTER:
        return _build_raster_marker_object(record)
    if record.body_geometry_mode == BODY_GEOMETRY_HAND_DRAWN:
        return _build_hand_drawn_balloon_object(record, style=style)
    x, y, w, h = record.body_rect
    bbox = (x, y, x + w, y + h)
    params: dict[str, float | int] = {}
    if record.tail.enabled:
        params.update(tail_params_for_tip(bbox, record.tail.target))
        params["tail_width"] = float(record.tail.width_ratio)
    else:
        # a tail tip inside the body renders no visible tail
        params.update({"tail_x": 0.0, "tail_y": 0.0})
    obj = build_parametric_shape_object(
        record.kind,
        bbox,
        params,
        style=style,
        object_id=f"comic-balloon-{record.balloon_id}",
        extra_meta={BALLOON_META_KEY: record.to_meta()},
    )
    return obj


def _build_raster_marker_object(record: BalloonRecord) -> VectorObject:
    """Raster Preserve: the ORIGINAL pixels (on the part layer) are the only
    visual. This object carries the record and nothing else — the renderer
    floors stroke widths at 1px, so it must have NO paths or a contour would
    be drawn over the hand-drawn art. Hit testing uses the record rects.
    Helper outlines stay available in the record meta (body/tail_outline)."""

    return VectorObject(
        id=f"comic-balloon-{record.balloon_id}",
        paths=[],
        style=_INVISIBLE_STYLE,
        meta={BALLOON_META_KEY: record.to_meta()},
    )


def _build_hand_drawn_balloon_object(
    record: BalloonRecord,
    *,
    style: VectorStyle,
) -> VectorObject | None:
    """Preserve mode: the user's own outlines ARE the geometry.

    Body and tail are separate closed subpaths regenerated from normalized
    points — never unioned, never fitted to a parametric shape, and never
    required to touch each other (detached tails are a normal state).
    """

    if len(record.body_outline) < 3:
        return None
    paths = [
        VectorPath(
            anchors=[
                Anchor((float(px), float(py)))
                for px, py in denormalize_outline(record.body_outline, record.body_rect)
            ],
            closed=True,
        )
    ]
    if (
        record.tail.enabled
        and record.tail_geometry_mode == TAIL_GEOMETRY_HAND_DRAWN
        and record.tail_rect is not None
        and len(record.tail_outline) >= 3
    ):
        paths.append(
            VectorPath(
                anchors=[
                    Anchor((float(px), float(py)))
                    for px, py in denormalize_outline(record.tail_outline, record.tail_rect)
                ],
                closed=True,
            )
        )
    return VectorObject(
        id=f"comic-balloon-{record.balloon_id}",
        paths=paths,
        style=style,
        meta={BALLOON_META_KEY: record.to_meta()},
    )


__all__ = [
    "BALLOON_STYLE",
    "PANEL_STYLE",
    "build_balloon_object",
    "build_panel_object",
    "panel_rect_from_object",
]
