"""Phase BZ2: Body/Tail 2 レイヤー指定の Preserve Hand-drawn 変換.

The user explicitly names a Balloon Body source and a Tail source (raster
layer, or vector layer/object — the path is used directly, no raster
round-trip). Both hand-drawn outlines are kept as the geometry authority
and registered as a REGULAR comic_tools balloon:

- Body and Tail stay independent geometries linked only logically through
  the BalloonRecord — they never have to touch (a gap, an overlap, or a
  loose root are all normal states, never validation errors).
- Nothing is fitted to an ellipse/parametric shape and the two outlines are
  never boolean-unioned.
- Nominal tail root/tip/direction are DERIVED helper metadata for the
  existing speaker/tail-suggestion surfaces; the drawn outline stays the
  authority and is never replaced by a straight tail.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

from .balloonize import (
    BalloonizeError,
    extract_hand_drawn_outline,
    inscribed_rect_for_mask,
    mask_from_alpha,
)
from .balloonize_service import PROVENANCE_KEY, _qimage_to_rgba
from .commands import ComicEditCommand
from .model import (
    BODY_GEOMETRY_HAND_DRAWN,
    BODY_GEOMETRY_RASTER,
    TAIL_GEOMETRY_HAND_DRAWN,
    TAIL_GEOMETRY_RASTER,
    BalloonRecord,
    BalloonTail,
    normalize_outline,
)
from .service import ComicPageController, ComicPageState
from .text_frames import build_balloon_text_object

CONVERSION_TYPE_SEPARATED = "separated_body_tail"
MODE_PRESERVE = "preserve"
MODE_PRESERVE_RASTER = "preserve_raster"

_SIMPLIFY_SPEC = "approxPolyDP(eps=clamp(perimeter*0.35%, 0.75px, 6px))"


@dataclass(frozen=True)
class SourceOutline:
    """One extracted hand-drawn shape (page coordinates).

    For raster sources ``raster_png`` carries the cropped source RGBA —
    the AUTHORITY of the preserve conversion; ``points`` is a derived
    helper contour (hit test / preview / nominal root+tip only).
    """

    points: tuple[tuple[float, float], ...]
    bbox: tuple[float, float, float, float]
    layer_id: str
    object_id: str = ""
    source_kind: str = "raster"  # raster | vector
    raster_png: str = ""


@dataclass(frozen=True)
class PreserveProposal:
    """The confirmation payload for the 2-layer preserve conversion."""

    body: SourceOutline
    tail: SourceOutline | None
    text_frame_rect: tuple[float, float, float, float]
    #: derived helper metadata only — NOT geometry validity conditions
    nominal_tail_root: tuple[float, float] | None = None
    nominal_tail_tip: tuple[float, float] | None = None
    body_tail_gap_px: float | None = None


def extract_source_outline(layer: Any, *, fill: bool = True) -> SourceOutline:
    """Outline of ONE layer's drawn shape; vector paths are used directly."""

    if layer is None:
        raise BalloonizeError("layer not found")
    layer_id = str(getattr(layer, "id", "") or "")
    vector_objects = getattr(layer, "vector_objects", None) or []
    if vector_objects:
        obj = vector_objects[0]
        paths = getattr(obj, "paths", None) or []
        if not paths:
            raise BalloonizeError("vector object has no paths")
        points = tuple((float(anchor.pos[0]), float(anchor.pos[1])) for anchor in paths[0].anchors)
        if len(points) < 3:
            raise BalloonizeError("vector path needs at least 3 anchors")
        xs = [px for px, _ in points]
        ys = [py for _, py in points]
        bbox = (min(xs), min(ys), max(1.0, max(xs) - min(xs)), max(1.0, max(ys) - min(ys)))
        return SourceOutline(
            points=points,
            bbox=bbox,
            layer_id=layer_id,
            object_id=str(getattr(obj, "id", "") or ""),
            source_kind="vector",
        )
    image = getattr(layer, "image", None)
    if image is None or (hasattr(image, "isNull") and image.isNull()):
        raise BalloonizeError("the layer has no drawable content")
    from .raster_parts import encode_qimage_png, tight_alpha_crop  # noqa: PLC0415

    cropped = tight_alpha_crop(image)
    if cropped is None:
        raise BalloonizeError("the layer has no drawn pixels")
    crop, crop_bbox = cropped
    mask = mask_from_alpha(_qimage_to_rgba(image))
    points, _outline_bbox = extract_hand_drawn_outline(mask, origin=(0.0, 0.0), fill=fill)
    # the crop bbox is the placement authority; the contour is helper-only
    return SourceOutline(
        points=tuple(points),
        bbox=crop_bbox,
        layer_id=layer_id,
        source_kind="raster",
        raster_png=encode_qimage_png(crop),
    )


def _nearest_pair(
    body_points: tuple[tuple[float, float], ...],
    tail_points: tuple[tuple[float, float], ...],
) -> tuple[tuple[float, float], tuple[float, float], float]:
    best = (body_points[0], tail_points[0], float("inf"))
    for tx, ty in tail_points:
        for bx, by in body_points:
            distance = math.hypot(tx - bx, ty - by)
            if distance < best[2]:
                best = ((bx, by), (tx, ty), distance)
    return best


def _derive_tail_metadata(
    body: SourceOutline, tail: SourceOutline
) -> tuple[tuple[float, float], tuple[float, float], float]:
    """(nominal root, nominal tip, gap). Helper info only — a detached tail
    (gap > 0) is exactly as valid as a touching or overlapping one."""

    body_cx = body.bbox[0] + body.bbox[2] * 0.5
    body_cy = body.bbox[1] + body.bbox[3] * 0.5
    _body_point, root, gap = _nearest_pair(body.points, tail.points)
    tip = max(
        tail.points,
        key=lambda point: (point[0] - body_cx) ** 2 + (point[1] - body_cy) ** 2,
    )
    return root, tip, gap


def propose_preserve(
    canvas: Any,
    *,
    body_layer_id: str,
    tail_layer_id: str | None = None,
    padding_px: float = 12.0,
) -> PreserveProposal:
    """Extract both sources. No connectivity requirement of any kind."""

    getter = getattr(canvas, "_get_layer_by_id", None)
    body_layer = getter(body_layer_id) if callable(getter) else None
    body = extract_source_outline(body_layer)

    tail: SourceOutline | None = None
    if tail_layer_id:
        tail_layer = getter(tail_layer_id) if callable(getter) else None
        tail = extract_source_outline(tail_layer)

    if body.source_kind == "raster":
        image = getattr(body_layer, "image", None)
        text_frame = inscribed_rect_for_mask(
            mask_from_alpha(_qimage_to_rgba(image)), padding_px=padding_px
        )
    else:
        # vector body: inscribed rect from the rasterized polygon (only the
        # text frame uses this raster; the geometry stays the original path)
        from .balloonize import polygon_mask  # noqa: PLC0415

        mask, origin = polygon_mask(body.points)
        text_frame = inscribed_rect_for_mask(mask, origin=origin, padding_px=padding_px)

    root = tip = None
    gap = None
    if tail is not None:
        root, tip, gap = _derive_tail_metadata(body, tail)
    return PreserveProposal(
        body=body,
        tail=tail,
        text_frame_rect=text_frame,
        nominal_tail_root=root,
        nominal_tail_tip=tip,
        body_tail_gap_px=gap,
    )


def _tail_local_anchors(
    tail: SourceOutline,
    nominal_root: tuple[float, float],
    nominal_tip: tuple[float, float],
) -> tuple[tuple[float, float], tuple[float, float], tuple[float, float]] | None:
    """(base_a, base_b, tip) in CROP-LOCAL coords from the tail outline.

    The base corners are the perpendicular extremes of the outline band
    nearest the root side — much more robust than a single nearest point,
    and they define the attachment span on the balloon boundary.
    """

    ox, oy = tail.bbox[0], tail.bbox[1]
    points = [(px - ox, py - oy) for px, py in tail.points]
    if len(points) < 3:
        return None
    root = (nominal_root[0] - ox, nominal_root[1] - oy)
    tip = (nominal_tip[0] - ox, nominal_tip[1] - oy)
    ax_x, ax_y = tip[0] - root[0], tip[1] - root[1]
    length = math.hypot(ax_x, ax_y)
    if length < 4.0:
        return None
    ux, uy = ax_x / length, ax_y / length
    projections = [(px - root[0]) * ux + (py - root[1]) * uy for px, py in points]
    lo = min(projections)
    hi = max(projections)
    band_limit = lo + max(3.0, (hi - lo) * 0.22)
    band = [point for point, proj in zip(points, projections, strict=True) if proj <= band_limit]
    if len(band) < 2:
        return None
    perpendicular = [(-uy * px_ + ux * py_) for px_, py_ in [(0.0, 0.0)]]  # placeholder
    del perpendicular
    perp_values = [(px - root[0]) * -uy + (py - root[1]) * ux for px, py in band]
    base_a = band[perp_values.index(min(perp_values))]
    base_b = band[perp_values.index(max(perp_values))]
    tip_local = points[projections.index(hi)]
    if math.hypot(base_b[0] - base_a[0], base_b[1] - base_a[1]) < 3.0:
        return None
    return (base_a, base_b, tip_local)


def straighten_outline_base(
    points: list[tuple[float, float]],
    base_a: tuple[float, float],
    base_b: tuple[float, float],
    tip: tuple[float, float],
) -> list[tuple[float, float]]:
    """Replace the outline arc between the two base corners (the arc that
    does NOT contain the tip) with a straight edge — the tail's root is a
    flat line between its two attachment points, not the silhouette dip
    between two drawn strokes."""

    n = len(points)
    if n < 4:
        return list(points)

    def _nearest(target: tuple[float, float]) -> int:
        return min(
            range(n),
            key=lambda i: (points[i][0] - target[0]) ** 2 + (points[i][1] - target[1]) ** 2,
        )

    ia, ib, it = _nearest(base_a), _nearest(base_b), _nearest(tip)
    if ia == ib:
        return list(points)

    def _arc(start: int, end: int) -> list[int]:
        out = [start]
        i = start
        while i != end:
            i = (i + 1) % n
            out.append(i)
        return out

    arc_ab = _arc(ia, ib)  # ia -> ib forward
    arc_ba = _arc(ib, ia)  # ib -> ia forward
    # keep the arc that contains the tip; the other one becomes one edge
    if it in arc_ab and it not in arc_ba[1:-1]:
        keep = arc_ab
    elif it in arc_ba:
        keep = arc_ba
    else:
        keep = arc_ab if len(arc_ab) >= len(arc_ba) else arc_ba
    return [points[i] for i in keep]


def build_preserve_record(  # noqa: PLR0912, PLR0915 - one record assembly
    proposal: PreserveProposal,
    *,
    balloon_id: str,
    panel_id: str,
    timestamp: float | None = None,
) -> BalloonRecord:
    """The confirmed BalloonRecord: hand-drawn geometry as the authority."""

    body_rect = proposal.body.bbox
    bx, by, bw, bh = body_rect
    tail = proposal.tail
    if tail is not None:
        tail_payload = BalloonTail(
            enabled=True,
            target=proposal.nominal_tail_tip or (bx + bw * 0.5, by + bh * 1.2),
        )
    else:
        tail_payload = BalloonTail(enabled=False)
    tx, ty, tw, th = proposal.text_frame_rect
    body_is_raster = bool(proposal.body.raster_png)
    tail_is_raster = bool(tail is not None and tail.raster_png)
    provenance = {
        "schema_version": 2,
        "conversion_type": CONVERSION_TYPE_SEPARATED,
        "conversion_mode": MODE_PRESERVE_RASTER if body_is_raster else MODE_PRESERVE,
        "source": CONVERSION_TYPE_SEPARATED,
        "body_source_layer_id": proposal.body.layer_id,
        "body_source_object_id": proposal.body.object_id,
        "body_source_kind": proposal.body.source_kind,
        "body_source_bbox": [float(v) for v in proposal.body.bbox],
        "tail_source_layer_id": tail.layer_id if tail else "",
        "tail_source_object_id": tail.object_id if tail else "",
        "tail_source_kind": tail.source_kind if tail else "",
        "tail_source_bbox": [float(v) for v in tail.bbox] if tail else None,
        "body_tail_gap_px": (
            float(proposal.body_tail_gap_px) if proposal.body_tail_gap_px is not None else None
        ),
        "nominal_tail_root": (
            [float(v) for v in proposal.nominal_tail_root] if proposal.nominal_tail_root else None
        ),
        "converted_at": float(timestamp if timestamp is not None else time.time()),
        "simplification": _SIMPLIFY_SPEC,
        "body_raster_size": (
            [int(proposal.body.bbox[2]), int(proposal.body.bbox[3])] if body_is_raster else None
        ),
        "tail_raster_size": ([int(tail.bbox[2]), int(tail.bbox[3])] if tail_is_raster else None),
    }
    record = BalloonRecord(
        balloon_id=balloon_id,
        panel_id=panel_id,
        kind="speech_round",  # display hint only; geometry mode overrides it
        body_rect=body_rect,
        tail=tail_payload,
        text_object_id=f"comic-text-{balloon_id}",
        auto_size=False,
        metadata={PROVENANCE_KEY: provenance},
        # raster sources: the PIXELS are the authority; contours below are
        # derived helpers. Vector sources keep the vector-preserve mode.
        body_geometry_mode=(BODY_GEOMETRY_RASTER if body_is_raster else BODY_GEOMETRY_HAND_DRAWN),
        body_outline=normalize_outline(proposal.body.points, body_rect),
        tail_geometry_mode=(
            (TAIL_GEOMETRY_RASTER if tail_is_raster else TAIL_GEOMETRY_HAND_DRAWN)
            if tail
            else "straight"
        ),
        tail_rect=tail.bbox if tail else None,
        tail_outline=normalize_outline(tail.points, tail.bbox) if tail else (),
        body_raster_png=proposal.body.raster_png,
        tail_raster_png=tail.raster_png if tail else "",
        tail_local_root=(
            (
                proposal.nominal_tail_root[0] - tail.bbox[0],
                proposal.nominal_tail_root[1] - tail.bbox[1],
            )
            if tail is not None and tail_is_raster and proposal.nominal_tail_root
            else None
        ),
        tail_local_tip=(
            (
                proposal.nominal_tail_tip[0] - tail.bbox[0],
                proposal.nominal_tail_tip[1] - tail.bbox[1],
            )
            if tail is not None and tail_is_raster and proposal.nominal_tail_tip
            else None
        ),
        text_frame_rel=(
            (tx - bx) / max(1e-6, bw),
            (ty - by) / max(1e-6, bh),
            tw / max(1e-6, bw),
            th / max(1e-6, bh),
        ),
    )
    # 2-point boundary attachment defaults (付け根の両端を外周 param 化)
    if (
        tail is not None
        and tail_is_raster
        and proposal.nominal_tail_root is not None
        and proposal.nominal_tail_tip is not None
    ):
        anchors = _tail_local_anchors(tail, proposal.nominal_tail_root, proposal.nominal_tail_tip)
        if anchors is not None:
            from dataclasses import replace as _replace  # noqa: PLC0415

            from .raster_parts import (  # noqa: PLC0415
                _body_outline_points,
                nearest_outline_param,
            )

            base_a, base_b, tip_local = anchors
            outline_pts = _body_outline_points(record)
            perimeter = sum(
                math.hypot(
                    outline_pts[(i + 1) % len(outline_pts)][0] - outline_pts[i][0],
                    outline_pts[(i + 1) % len(outline_pts)][1] - outline_pts[i][1],
                )
                for i in range(len(outline_pts))
            )
            center_param = nearest_outline_param(record, proposal.nominal_tail_root)
            base_width = math.hypot(base_b[0] - base_a[0], base_b[1] - base_a[1])
            half = min(0.2, (base_width * 0.5) / max(1.0, perimeter))
            # helper outline: the base edge between the two corners is a
            # STRAIGHT segment (the silhouette dips between two drawn lines)
            straight_outline = record.tail_outline
            if record.tail_rect is not None and len(record.tail_outline) >= 3:
                tw, th = record.tail_rect[2], record.tail_rect[3]
                local_pts = [(px * tw, py * th) for px, py in record.tail_outline]
                local_pts = straighten_outline_base(local_pts, base_a, base_b, tip_local)
                straight_outline = tuple(
                    (px / max(1e-6, tw), py / max(1e-6, th)) for px, py in local_pts
                )
            record = _replace(
                record,
                tail_base_params=((center_param - half) % 1.0, (center_param + half) % 1.0),
                tail_local_base=(base_a, base_b),
                tail_local_tip=tip_local,
                tail_outline=straight_outline,
                tail_root_param=center_param,
                tail_root_half_px=max(0.5, base_width * 0.5),
            )
    # OPEN outline: remember the gap ends; the tail base sits on the gap
    if body_is_raster and record.tail.enabled and len(record.body_outline) >= 8:
        from .raster_parts import (  # noqa: PLC0415
            decode_png_qimage,
            detect_body_opening,
            nearest_outline_param,
        )

        crop = decode_png_qimage(record.body_raster_png)
        if crop is not None:
            from loomstroke.core.document_snapshot_builder import (  # noqa: PLC0415
                DocumentSnapshotBuilder,
            )

            alpha = DocumentSnapshotBuilder.qimage_to_array(crop)[:, :, 3]
            local_outline = [
                (px * crop.width(), py * crop.height()) for px, py in record.body_outline
            ]
            opening = detect_body_opening(alpha, local_outline)
            if opening is not None:
                from dataclasses import replace as _replace2  # noqa: PLC0415

                from .raster_parts import closed_outline_for_open_stroke  # noqa: PLC0415

                # An open stroke's silhouette outline is a thin loop hugging
                # the line — useless as a balloon perimeter. Replace the
                # helper outline with the CLOSED shape (ink hull sealed
                # across the gap) so params/base/tail all live on a real
                # perimeter that spans the opening.
                closed_local = closed_outline_for_open_stroke(alpha, opening)
                bx0, by0 = record.body_rect[0], record.body_rect[1]
                if closed_local:
                    page_pts = [(bx0 + px, by0 + py) for px, py in closed_local]
                    record = _replace2(
                        record,
                        body_outline=normalize_outline(page_pts, record.body_rect),
                    )
                end_a = (bx0 + opening[0][0], by0 + opening[0][1])
                end_b = (bx0 + opening[1][0], by0 + opening[1][1])
                pa = nearest_outline_param(record, end_a)
                pb = nearest_outline_param(record, end_b)
                from .raster_parts import outline_perimeter  # noqa: PLC0415

                delta = (pb - pa) % 1.0
                if delta > 0.5:
                    delta -= 1.0
                centre = (pa + delta * 0.5) % 1.0
                half = min(0.2, abs(delta) * 0.5)
                base = ((centre - half) % 1.0, (centre + half) % 1.0)
                has_root = record.tail_base_params is not None
                record = _replace2(
                    record,
                    body_opening_params=(pa, pb),
                    body_open=True,  # drawn open -> stays open (user can flip)
                    # the tail attaches AT the opening: root = gap centre,
                    # span = the drawn gap
                    tail_base_params=base if has_root else None,
                    tail_root_param=centre if has_root else record.tail_root_param,
                    tail_root_half_px=(
                        max(0.5, half * outline_perimeter(record))
                        if has_root
                        else record.tail_root_half_px
                    ),
                )
    # calibrate the root to where the tail was DRAWN (closed and open
    # drawings alike): the root centre is the outline point nearest the
    # crop's base midpoint (so there is no lateral jump), the follow-tip
    # offset makes the tip-derived proposal land exactly there right after
    # conversion, and the gap is the remaining distance along the axis.
    if (
        record.tail_root_param is not None
        and record.tail_local_base is not None
        and record.tail_rect is not None
    ):
        from dataclasses import replace as _replace3  # noqa: PLC0415

        from .raster_parts import (  # noqa: PLC0415
            nearest_outline_param,
            outline_param_facing,
            tail_anchor_mid,
        )

        la, lb = record.tail_local_base
        tx0, ty0 = record.tail_rect[0], record.tail_rect[1]
        base_mid = (tx0 + (la[0] + lb[0]) * 0.5, ty0 + (la[1] + lb[1]) * 0.5)
        if record.body_opening_params is None:
            # closed drawing: the root IS where the crop base sits
            centre = nearest_outline_param(record, base_mid)
        else:
            centre = float(record.tail_root_param)
        facing = outline_param_facing(record, record.tail.target)
        record = _replace3(
            record,
            tail_root_param=centre,
            tail_base_offset=(centre - facing + 0.5) % 1.0 - 0.5,
        )
        anchor = tail_anchor_mid(_replace3(record, tail_base_gap=0.0), record.tail.target)
        if anchor is not None:
            la, lb = record.tail_local_base
            tx0, ty0 = record.tail_rect[0], record.tail_rect[1]
            base_mid = (tx0 + (la[0] + lb[0]) * 0.5, ty0 + (la[1] + lb[1]) * 0.5)
            ax, ay = record.tail.target[0] - anchor[0], record.tail.target[1] - anchor[1]
            length = math.hypot(ax, ay)
            if length > 1e-6:
                ux, uy = ax / length, ay / length
                dx, dy = base_mid[0] - anchor[0], base_mid[1] - anchor[1]
                gap = dx * ux + dy * uy  # along the axis → tail_base_gap
                across = -dx * uy + dy * ux  # lateral → rigid corner offset
                offsets = record.tail_root_corner_offsets
                if abs(across) > 0.5:
                    # the drawn root sits sideways of the root centre (e.g. an
                    # open drawing whose tail is off the gap centre): keep it
                    # exactly there via an equal offset on both corners
                    offsets = ((0.0, across), (0.0, across))
                record = _replace3(
                    record, tail_base_gap=max(0.0, gap), tail_root_corner_offsets=offsets
                )
    return record


def build_preserve_command(
    controller: ComicPageController,
    state: ComicPageState,
    record: BalloonRecord,
    *,
    text: str = "",
    vertical: bool = True,
    hide_source_layers: bool = True,
) -> ComicEditCommand:
    """One undo unit: balloon + text creation + BOTH source layers hidden
    (never deleted; visibility restored by undo)."""

    from .balloon_objects import build_balloon_object  # noqa: PLC0415

    balloon_obj = build_balloon_object(record)
    if balloon_obj is None:
        raise BalloonizeError("hand-drawn outline could not be materialized")
    text_obj = build_balloon_text_object(record, text, vertical=vertical)
    provenance = record.metadata.get(PROVENANCE_KEY) or {}
    visibility: dict[str, bool] = {}
    comic_layer_ids = {
        state.frames_layer_id,
        state.balloons_layer_id,
        state.text_layer_id,
    }
    if hide_source_layers:
        for key in ("body_source_layer_id", "tail_source_layer_id"):
            layer_id = str(provenance.get(key) or "")
            if layer_id and layer_id not in comic_layer_ids:
                visibility[layer_id] = False
    from .raster_parts import is_raster_preserve  # noqa: PLC0415

    command = ComicEditCommand(
        label="comic_balloonize_preserve",
        ensure_layers=controller._ensure_balloon_layers(state),  # noqa: SLF001
        additions=[
            (controller._balloons_layer_id(state), balloon_obj),  # noqa: SLF001
            (controller._text_layer_id(state), text_obj),  # noqa: SLF001
        ],
        layer_visibility_updates=visibility,
        raster_parts=[record] if is_raster_preserve(record) else [],
    )
    from .panel_balloon_adoption_plan import adopt_created_balloon  # noqa: PLC0415

    return adopt_created_balloon(controller, state, record, command)


__all__ = [
    "CONVERSION_TYPE_SEPARATED",
    "MODE_PRESERVE",
    "MODE_PRESERVE_RASTER",
    "PreserveProposal",
    "SourceOutline",
    "build_preserve_command",
    "build_preserve_record",
    "extract_source_outline",
    "propose_preserve",
]
