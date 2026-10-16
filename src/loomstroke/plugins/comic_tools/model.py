"""Comic semantic model: panel/balloon metadata carried on vector objects.

Vector objects are the authoritative, persisted record (CT-01/CT-04 Phase 1
contract): the `.lstroke` format already round-trips `VectorObject.meta`
verbatim, so no save-format change is needed and old readers still show the
frames/balloons as plain vector art.

- Panel frame objects carry ``meta["loomstroke:comic_panel"]``.
- Balloon body objects (parametric speech shapes with a separate tail) carry
  ``meta["loomstroke:comic_balloon"]``.
- Balloon text objects (regular Text Tool area-text objects) carry
  ``meta["loomstroke:comic_balloon_text"]``.
- The page layout tree lives in the frames LAYER metadata under
  ``plugins["loomstroke.comictools.panel_layout"]``.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

PANEL_META_KEY = "loomstroke:comic_panel"
BALLOON_META_KEY = "loomstroke:comic_balloon"
BALLOON_TEXT_META_KEY = "loomstroke:comic_balloon_text"
LAYOUT_LAYER_NAMESPACE = "loomstroke.comictools.panel_layout"
COMIC_LAYER_ROLE_NAMESPACE = "loomstroke.comictools.comic_layer"

PANEL_META_SCHEMA_VERSION = 1
BALLOON_META_SCHEMA_VERSION = 1

#: ``BalloonRecord.renderer["kind"]`` of a balloon composed by the raster
#: rig (body/tail crops + attachment pose; see ``raster_rig_commit``)
RENDERER_RASTER_RIG = "raster_rig"

#: managed layer roles
LAYER_ROLE_FRAMES = "panel_frames"
LAYER_ROLE_BALLOONS = "balloons"
LAYER_ROLE_BALLOON_TEXT = "balloon_text"

BALLOON_KINDS = ("speech_round", "speech_rect", "speech_cloud", "spiky_balloon")

#: body geometry modes: "parametric" = existing speech shapes rebuilt from
#: params; "hand_drawn" = the user's own outline is the geometry authority
#: (never fitted to an ellipse), stored as points normalized to body_rect so
#: move/scale stay non-destructive.
BODY_GEOMETRY_PARAMETRIC = "parametric"
BODY_GEOMETRY_HAND_DRAWN = "hand_drawn"
#: tail geometry modes: "straight" = parametric anchor-to-target triangle;
#: "hand_drawn" = the drawn tail outline (normalized to tail_rect), which is
#: an INDEPENDENT geometry - it never has to touch the body.
TAIL_GEOMETRY_STRAIGHT = "straight"
TAIL_GEOMETRY_HAND_DRAWN = "hand_drawn"
#: raster preserve (Phase BZ3): the ORIGINAL drawn pixels are the geometry
#: authority. The record embeds the cropped RGBA (PNG base64); outlines
#: are demoted to derived helpers (hit test / preview / nominal root+tip).
BODY_GEOMETRY_RASTER = "raster_preserve"
TAIL_GEOMETRY_RASTER = "raster_preserve"

BALLOON_STATUS_EMPTY = "empty"
BALLOON_STATUS_FILLED = "filled"


@dataclass(frozen=True)
class PanelRecord:
    """Semantic info of one panel frame vector object."""

    panel_id: str
    reading_order: int = 0
    gutter_px: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_meta(self) -> dict[str, Any]:
        return {
            "schema_version": PANEL_META_SCHEMA_VERSION,
            "panel_id": self.panel_id,
            "reading_order": int(self.reading_order),
            "gutter_px": float(self.gutter_px),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_meta(payload: Any) -> PanelRecord | None:
        if not isinstance(payload, Mapping):
            return None
        panel_id = str(payload.get("panel_id") or "")
        if not panel_id:
            return None
        try:
            return PanelRecord(
                panel_id=panel_id,
                reading_order=int(payload.get("reading_order", 0)),
                gutter_px=float(payload.get("gutter_px", 0.0)),
                metadata=dict(payload.get("metadata") or {}),
            )
        except (TypeError, ValueError):
            return None


@dataclass(frozen=True)
class BalloonTail:
    """The tail is an independent structure, not baked pixels.

    ``target`` is the VISUAL tail tip in page coordinates. It is decoupled
    from the logical speaker: assigning a speaker never snaps the tip.
    ``anchor_side`` / ``curvature`` / ``join`` are carried for the future
    curved/multi-tail extension and the speaker-based tail suggestions.
    """

    enabled: bool = True
    target: tuple[float, float] = (0.0, 0.0)
    width_ratio: float = 0.16
    anchor_side: str = "auto"
    curvature: float = 0.0
    join: str = "miter"

    def to_payload(self) -> dict[str, Any]:
        return {
            "enabled": bool(self.enabled),
            "target": [float(self.target[0]), float(self.target[1])],
            "width_ratio": float(self.width_ratio),
            "anchor_side": str(self.anchor_side),
            "curvature": float(self.curvature),
            "join": str(self.join),
        }

    @staticmethod
    def from_payload(payload: Any) -> BalloonTail:
        if not isinstance(payload, Mapping):
            return BalloonTail()
        raw_target = payload.get("target") or [0.0, 0.0]
        try:
            target = (float(raw_target[0]), float(raw_target[1]))
        except (TypeError, ValueError, IndexError):
            target = (0.0, 0.0)
        return BalloonTail(
            enabled=bool(payload.get("enabled", True)),
            target=target,
            width_ratio=float(payload.get("width_ratio", 0.16) or 0.16),
            anchor_side=str(payload.get("anchor_side") or "auto"),
            curvature=float(payload.get("curvature", 0.0) or 0.0),
            join=str(payload.get("join") or "miter"),
        )


@dataclass(frozen=True)
class BalloonRecord:
    """Semantic info of one balloon body vector object.

    The body geometry itself is procedural (parametric speech shape params on
    the same object); this record carries the comic semantics: identity,
    panel membership, the text-object link, sizing policy, and the speaker.
    ``speaker_id`` is the LOGICAL speaker; the visual tail target lives in
    ``tail.target`` and is never overwritten by speaker assignment.
    """

    balloon_id: str
    panel_id: str = ""
    kind: str = "speech_round"
    body_rect: tuple[float, float, float, float] = (0.0, 0.0, 200.0, 140.0)
    tail: BalloonTail = field(default_factory=BalloonTail)
    text_object_id: str = ""
    auto_size: bool = True
    padding_px: float = 18.0
    speaker_id: str = ""
    speaker_name: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    # -- hand-drawn geometry (Phase BZ2) --
    #: outline points normalized to body_rect (0..1); empty = parametric
    body_geometry_mode: str = BODY_GEOMETRY_PARAMETRIC
    body_outline: tuple[tuple[float, float], ...] = ()
    #: detached hand-drawn tail: its OWN page-space bbox + normalized outline.
    #: tail.target stays the derived nominal tip (compat with speaker/tail
    #: suggestions); the outline is the authority and is never replaced by a
    #: straight tail when the nominal tip moves.
    tail_geometry_mode: str = TAIL_GEOMETRY_STRAIGHT
    tail_rect: tuple[float, float, float, float] | None = None
    tail_outline: tuple[tuple[float, float], ...] = ()
    #: text frame normalized to body_rect (hand-drawn bodies store their
    #: inscribed frame here; parametric bodies derive it from kind+padding)
    text_frame_rel: tuple[float, float, float, float] | None = None
    #: raster preserve authority: the cropped source RGBA as PNG base64.
    #: body_rect / tail_rect are the page-space placements of these crops;
    #: moving/scaling only changes the rects, never the pixels.
    body_raster_png: str = ""
    tail_raster_png: str = ""
    #: procedural tail connection (楕円テンプレートと同じ操作性): the drawn
    #: tail crop carries its own base/point anchors in CROP-LOCAL coords.
    #: At render time the crop is similarity-transformed so the base snaps
    #: to the body boundary toward ``tail.target`` and the point follows the
    #: target — the connection deforms procedurally while the pixels stay
    #: the hand-drawn art.
    tail_local_root: tuple[float, float] | None = None
    tail_local_tip: tuple[float, float] | None = None
    #: 2-point boundary attachment (ユーザー提案): the tail base corners as
    #: normalized ARC-LENGTH PARAMS (0..1) along the body outline — they
    #: slide on the silhouette and follow body move/resize automatically.
    #: ``tail_local_base`` holds the matching crop-local base corner points;
    #: rendering maps (base_a_local, base_b_local, tip_local) affinely onto
    #: (outline(param_a), outline(param_b), tail.target).
    tail_base_params: tuple[float, float] | None = None
    tail_local_base: tuple[tuple[float, float], tuple[float, float]] | None = None
    #: base FOLLOWS the tip: the effective attachment centre is the outline
    #: point facing tail.target plus this manual arc offset (set by the base
    #: handle); the pair spacing is kept. None = plain nearest-to-tip.
    tail_base_offset: float = 0.0
    #: mid-axis bend anchor as (along 0..1, across in px, perpendicular);
    #: None = straight axis. Drives a quadratic-bezier warp of the crop.
    tail_bend: tuple[float, float] | None = None
    #: OPEN body outline (the drawn stroke does not close where the tail
    #: attaches): the gap endpoints as outline params. At render time the
    #: stroke ends are warped so the opening travels with the tail base —
    #: the opening/tail relationship is preserved wherever the tail goes.
    body_opening_params: tuple[float, float] | None = None
    #: Distance (page px, along the base→tip axis) the drawn tail's base sits
    #: AWAY from the balloon outline. Kept constant while the tail moves so
    #: a deliberately drawn gap between balloon and tail is preserved.
    tail_base_gap: float = 0.0
    #: Procedural OPENING toggle: True = the body outline is cut open where
    #: the tail attaches (and a gap drawn elsewhere is closed); False = the
    #: outline is always closed (a drawn gap is filled). Set at conversion
    #: from what was drawn; the user can flip it any time.
    body_open: bool = False
    #: ── root authority (単一の正本) ──
    #: Absolute outline param of the tail ROOT centre (also the opening
    #: centre) and the half span in PAGE PX. Calibrated at conversion from
    #: where the tail was actually drawn; edited by the green anchors.
    #: ``tail_follow_tip``: True = the centre is proposed from the tip
    #: (ray from the body centre through the tip, + ``tail_base_offset``);
    #: False = pinned to ``tail_root_param`` no matter where the tip goes.
    tail_root_param: float | None = None
    tail_root_half_px: float | None = None
    tail_follow_tip: bool = True
    #: Per-corner offsets of the tail ROOT corners, in the straight tail
    #: frame (along-axis px, across-axis px), relative to where the plain
    #: axis-stretch places them. Lets each blue root anchor move
    #: independently (the crop is then mapped by the affine through the two
    #: corners + tip). None / zeros = plain axis-only stretch.
    tail_root_corner_offsets: tuple[tuple[float, float], tuple[float, float]] | None = None
    #: Auto-join: bridge the tail's stroke ends to the balloon outline at
    #: render time (Hermite bridge, width/colour matched from the pixels) so
    #: the two read as one drawn line even when the root sits a little off.
    tail_auto_join: bool = True
    #: Optional renderer payload (own ``kind`` / ``version``): how the
    #: balloon's pixels are composed from its stored crops. None = the
    #: legacy Raster Preserve / parametric / hand-drawn rendering. Readers
    #: that do not know the kind keep the stored part-layer pixels as a
    #: static raster (the payload is carried through untouched).
    renderer: dict[str, Any] | None = None

    def to_meta(self) -> dict[str, Any]:
        return {
            "schema_version": BALLOON_META_SCHEMA_VERSION,
            "balloon_id": self.balloon_id,
            "panel_id": self.panel_id,
            "kind": self.kind,
            "body_rect": [float(v) for v in self.body_rect],
            "tail": self.tail.to_payload(),
            "text_object_id": self.text_object_id,
            "auto_size": bool(self.auto_size),
            "padding_px": float(self.padding_px),
            "speaker_id": self.speaker_id,
            "speaker_name": self.speaker_name,
            "metadata": dict(self.metadata),
            "body_geometry_mode": self.body_geometry_mode,
            "body_outline": [[float(x), float(y)] for x, y in self.body_outline],
            "tail_geometry_mode": self.tail_geometry_mode,
            "tail_rect": (
                [float(v) for v in self.tail_rect] if self.tail_rect is not None else None
            ),
            "tail_outline": [[float(x), float(y)] for x, y in self.tail_outline],
            "text_frame_rel": (
                [float(v) for v in self.text_frame_rel] if self.text_frame_rel is not None else None
            ),
            "body_raster_png": self.body_raster_png,
            "tail_raster_png": self.tail_raster_png,
            "tail_local_root": (
                [float(v) for v in self.tail_local_root]
                if self.tail_local_root is not None
                else None
            ),
            "tail_local_tip": (
                [float(v) for v in self.tail_local_tip] if self.tail_local_tip is not None else None
            ),
            "tail_base_params": (
                [float(v) for v in self.tail_base_params]
                if self.tail_base_params is not None
                else None
            ),
            "tail_local_base": (
                [[float(v) for v in point] for point in self.tail_local_base]
                if self.tail_local_base is not None
                else None
            ),
            "tail_base_offset": float(self.tail_base_offset),
            "tail_bend": (
                [float(v) for v in self.tail_bend] if self.tail_bend is not None else None
            ),
            "body_opening_params": (
                [float(v) for v in self.body_opening_params]
                if self.body_opening_params is not None
                else None
            ),
            "tail_base_gap": float(self.tail_base_gap),
            "body_open": bool(self.body_open),
            "tail_root_param": (
                float(self.tail_root_param) if self.tail_root_param is not None else None
            ),
            "tail_root_half_px": (
                float(self.tail_root_half_px) if self.tail_root_half_px is not None else None
            ),
            "tail_follow_tip": bool(self.tail_follow_tip),
            "tail_auto_join": bool(self.tail_auto_join),
            "tail_root_corner_offsets": (
                [[float(v) for v in pt] for pt in self.tail_root_corner_offsets]
                if self.tail_root_corner_offsets is not None
                else None
            ),
            "renderer": (dict(self.renderer) if isinstance(self.renderer, Mapping) else None),
        }

    @staticmethod
    def from_meta(payload: Any) -> BalloonRecord | None:
        if not isinstance(payload, Mapping):
            return None
        balloon_id = str(payload.get("balloon_id") or "")
        if not balloon_id:
            return None
        raw_rect = payload.get("body_rect") or [0.0, 0.0, 200.0, 140.0]
        try:
            body_rect = (
                float(raw_rect[0]),
                float(raw_rect[1]),
                max(1.0, float(raw_rect[2])),
                max(1.0, float(raw_rect[3])),
            )
        except (TypeError, ValueError, IndexError):
            body_rect = (0.0, 0.0, 200.0, 140.0)
        kind = str(payload.get("kind") or "speech_round")
        if kind not in BALLOON_KINDS:
            kind = "speech_round"
        return BalloonRecord(
            balloon_id=balloon_id,
            panel_id=str(payload.get("panel_id") or ""),
            kind=kind,
            body_rect=body_rect,
            tail=BalloonTail.from_payload(payload.get("tail")),
            text_object_id=str(payload.get("text_object_id") or ""),
            auto_size=bool(payload.get("auto_size", True)),
            padding_px=float(payload.get("padding_px", 18.0) or 18.0),
            speaker_id=str(payload.get("speaker_id") or ""),
            speaker_name=str(payload.get("speaker_name") or ""),
            metadata=dict(payload.get("metadata") or {}),
            body_geometry_mode=_geometry_mode(
                payload.get("body_geometry_mode"),
                allowed=(BODY_GEOMETRY_HAND_DRAWN, BODY_GEOMETRY_RASTER),
                default=BODY_GEOMETRY_PARAMETRIC,
            ),
            body_outline=_points_from_payload(payload.get("body_outline")),
            tail_geometry_mode=_geometry_mode(
                payload.get("tail_geometry_mode"),
                allowed=(TAIL_GEOMETRY_HAND_DRAWN, TAIL_GEOMETRY_RASTER),
                default=TAIL_GEOMETRY_STRAIGHT,
            ),
            tail_rect=_rect_from_payload(payload.get("tail_rect")),
            tail_outline=_points_from_payload(payload.get("tail_outline")),
            text_frame_rel=_rect_from_payload(payload.get("text_frame_rel")),
            body_raster_png=str(payload.get("body_raster_png") or ""),
            tail_raster_png=str(payload.get("tail_raster_png") or ""),
            tail_local_root=_point_from_payload(payload.get("tail_local_root")),
            tail_local_tip=_point_from_payload(payload.get("tail_local_tip")),
            tail_base_params=_point_from_payload(payload.get("tail_base_params")),
            tail_local_base=_point_pair_from_payload(payload.get("tail_local_base")),
            tail_base_offset=float(payload.get("tail_base_offset", 0.0) or 0.0),
            tail_bend=_point_from_payload(payload.get("tail_bend")),
            body_opening_params=_point_from_payload(payload.get("body_opening_params")),
            tail_base_gap=float(payload.get("tail_base_gap", 0.0) or 0.0),
            body_open=bool(payload.get("body_open", False)),
            tail_root_param=(
                float(payload["tail_root_param"])
                if payload.get("tail_root_param") is not None
                else None
            ),
            tail_root_half_px=(
                float(payload["tail_root_half_px"])
                if payload.get("tail_root_half_px") is not None
                else None
            ),
            tail_follow_tip=bool(payload.get("tail_follow_tip", True)),
            tail_root_corner_offsets=_point_pair_from_payload(
                payload.get("tail_root_corner_offsets")
            ),
            tail_auto_join=bool(payload.get("tail_auto_join", True)),
            renderer=(
                dict(payload["renderer"]) if isinstance(payload.get("renderer"), Mapping) else None
            ),
        )


def is_raster_rig(record: BalloonRecord | None) -> bool:
    """True when the balloon is composed by the raster rig renderer."""

    renderer = getattr(record, "renderer", None)
    return isinstance(renderer, Mapping) and renderer.get("kind") == RENDERER_RASTER_RIG


def _geometry_mode(value: Any, *, allowed: tuple[str, ...], default: str) -> str:
    text = str(value or "")
    return text if text in allowed else default


def _points_from_payload(value: Any) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    points: list[tuple[float, float]] = []
    for item in value:
        try:
            points.append((float(item[0]), float(item[1])))
        except (TypeError, ValueError, IndexError):
            return ()
    return tuple(points)


def _point_from_payload(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    try:
        return (float(value[0]), float(value[1]))
    except (TypeError, ValueError):
        return None


def _point_pair_from_payload(
    value: Any,
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        return None
    a = _point_from_payload(value[0])
    b = _point_from_payload(value[1])
    if a is None or b is None:
        return None
    return (a, b)


def _rect_from_payload(value: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return (float(value[0]), float(value[1]), float(value[2]), float(value[3]))
    except (TypeError, ValueError):
        return None


def denormalize_outline(
    outline: tuple[tuple[float, float], ...],
    rect: tuple[float, float, float, float],
) -> list[tuple[float, float]]:
    x, y, w, h = rect
    return [(x + px * w, y + py * h) for px, py in outline]


def normalize_outline(
    points: Any,
    rect: tuple[float, float, float, float],
) -> tuple[tuple[float, float], ...]:
    x, y, w, h = rect
    w = max(1e-6, w)
    h = max(1e-6, h)
    return tuple((float((px - x) / w), float((py - y) / h)) for px, py in points)


def scale_balloon_record(
    record: BalloonRecord,
    factor: float,
    *,
    center: tuple[float, float] | None = None,
) -> BalloonRecord:
    """Scale the whole balloon (body + detached tail + text frame together).

    Hand-drawn outlines are stored normalized, so scaling the rects scales
    the shapes without touching a single outline point (non-destructive).
    """

    from dataclasses import replace as _replace  # noqa: PLC0415

    factor = float(factor)
    bx, by, bw, bh = record.body_rect
    cx, cy = center if center is not None else (bx + bw * 0.5, by + bh * 0.5)

    def _scale_point(point: tuple[float, float]) -> tuple[float, float]:
        return (cx + (point[0] - cx) * factor, cy + (point[1] - cy) * factor)

    def _scale_rect(rect: tuple[float, float, float, float]):
        x, y = _scale_point((rect[0], rect[1]))
        return (x, y, rect[2] * factor, rect[3] * factor)

    return _replace(
        record,
        body_rect=_scale_rect(record.body_rect),
        tail_rect=_scale_rect(record.tail_rect) if record.tail_rect is not None else None,
        tail=BalloonTail(
            enabled=record.tail.enabled,
            target=_scale_point(record.tail.target),
            width_ratio=record.tail.width_ratio,
            anchor_side=record.tail.anchor_side,
            curvature=record.tail.curvature,
            join=record.tail.join,
        ),
    )


def move_balloon_record(record: BalloonRecord, dx: float, dy: float) -> BalloonRecord:
    """Move body + tail + nominal tip together - a detached tail is part of
    the same balloon and travels with it."""

    from dataclasses import replace as _replace  # noqa: PLC0415

    x, y, w, h = record.body_rect
    tail_rect = record.tail_rect
    renderer = record.renderer
    if is_raster_rig(record):
        # the rig pose's tip is a page point: it travels with the balloon
        # (root_u is relative to the outline and needs no change)
        renderer = dict(renderer or {})
        pose = dict(renderer.get("pose") or {})
        tip = pose.get("tip")
        if isinstance(tip, (list, tuple)) and len(tip) >= 2:
            pose["tip"] = [float(tip[0]) + dx, float(tip[1]) + dy]
            renderer["pose"] = pose
    return _replace(
        record,
        renderer=renderer,
        body_rect=(x + dx, y + dy, w, h),
        tail_rect=(
            (tail_rect[0] + dx, tail_rect[1] + dy, tail_rect[2], tail_rect[3])
            if tail_rect is not None
            else None
        ),
        tail=BalloonTail(
            enabled=record.tail.enabled,
            target=(record.tail.target[0] + dx, record.tail.target[1] + dy),
            width_ratio=record.tail.width_ratio,
            anchor_side=record.tail.anchor_side,
            curvature=record.tail.curvature,
            join=record.tail.join,
        ),
    )


# ── vector-object meta helpers ───────────────────────────────────────────


def panel_record_from_object(obj: Any) -> PanelRecord | None:
    meta = getattr(obj, "meta", None)
    if not isinstance(meta, Mapping):
        return None
    return PanelRecord.from_meta(meta.get(PANEL_META_KEY))


def balloon_record_from_object(obj: Any) -> BalloonRecord | None:
    meta = getattr(obj, "meta", None)
    if not isinstance(meta, Mapping):
        return None
    return BalloonRecord.from_meta(meta.get(BALLOON_META_KEY))


def balloon_text_link_from_object(obj: Any) -> str | None:
    """The balloon_id a comic text object belongs to, if any."""

    meta = getattr(obj, "meta", None)
    if not isinstance(meta, Mapping):
        return None
    payload = meta.get(BALLOON_TEXT_META_KEY)
    if not isinstance(payload, Mapping):
        return None
    balloon_id = str(payload.get("balloon_id") or "")
    return balloon_id or None


def is_comic_panel_object(obj: Any) -> bool:
    return panel_record_from_object(obj) is not None


def is_comic_balloon_object(obj: Any) -> bool:
    return balloon_record_from_object(obj) is not None


# ── layer role markers ───────────────────────────────────────────────────


def layer_role_marker(role: str) -> dict[str, Any]:
    return {"plugins": {COMIC_LAYER_ROLE_NAMESPACE: {"role": role, "schema_version": 1}}}


def layer_comic_role(layer: Any) -> str | None:
    metadata = getattr(layer, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    plugins = metadata.get("plugins")
    if not isinstance(plugins, Mapping):
        return None
    entry = plugins.get(COMIC_LAYER_ROLE_NAMESPACE)
    if not isinstance(entry, Mapping):
        return None
    role = str(entry.get("role") or "")
    return role or None


def read_layer_layout_payload(layer: Any) -> dict[str, Any] | None:
    metadata = getattr(layer, "metadata", None)
    if not isinstance(metadata, Mapping):
        return None
    plugins = metadata.get("plugins")
    if not isinstance(plugins, Mapping):
        return None
    payload = plugins.get(LAYOUT_LAYER_NAMESPACE)
    return dict(payload) if isinstance(payload, Mapping) else None


def merged_layer_metadata_with_layout(
    metadata: Mapping[str, Any] | None, layout_payload: Mapping[str, Any]
) -> dict[str, Any]:
    """Copy-on-write merge of the layout namespace into layer metadata."""

    root = dict(metadata or {})
    plugins = dict(root.get("plugins") or {})
    plugins[LAYOUT_LAYER_NAMESPACE] = dict(layout_payload)
    root["plugins"] = plugins
    return root


def balloon_status(text: str) -> str:
    return BALLOON_STATUS_FILLED if str(text or "").strip() else BALLOON_STATUS_EMPTY


__all__ = [
    "BALLOON_KINDS",
    "BALLOON_META_KEY",
    "BALLOON_META_SCHEMA_VERSION",
    "BALLOON_STATUS_EMPTY",
    "BALLOON_STATUS_FILLED",
    "BALLOON_TEXT_META_KEY",
    "BODY_GEOMETRY_HAND_DRAWN",
    "BODY_GEOMETRY_PARAMETRIC",
    "BODY_GEOMETRY_RASTER",
    "COMIC_LAYER_ROLE_NAMESPACE",
    "LAYER_ROLE_BALLOONS",
    "LAYER_ROLE_BALLOON_TEXT",
    "LAYER_ROLE_FRAMES",
    "LAYOUT_LAYER_NAMESPACE",
    "PANEL_META_KEY",
    "PANEL_META_SCHEMA_VERSION",
    "RENDERER_RASTER_RIG",
    "TAIL_GEOMETRY_HAND_DRAWN",
    "TAIL_GEOMETRY_RASTER",
    "TAIL_GEOMETRY_STRAIGHT",
    "BalloonRecord",
    "BalloonTail",
    "PanelRecord",
    "balloon_record_from_object",
    "balloon_status",
    "balloon_text_link_from_object",
    "denormalize_outline",
    "is_comic_balloon_object",
    "is_comic_panel_object",
    "is_raster_rig",
    "layer_comic_role",
    "layer_role_marker",
    "merged_layer_metadata_with_layout",
    "move_balloon_record",
    "normalize_outline",
    "panel_record_from_object",
    "read_layer_layout_payload",
    "scale_balloon_record",
]
