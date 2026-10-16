"""Raster rig → document: records, part-layer compositing and the commit command.

A rig balloon is an ordinary ``BalloonRecord`` (balloon list / text frame /
speaker / dialogue / selection / undo all reuse the comic_tools structure)
whose pixels are composed by the raster rig:

* the body / tail crops are the authority and live in the record
  (``body_raster_png`` / ``tail_raster_png`` at ``body_rect`` / ``tail_rect``
  — the same contract as Raster Preserve, so copy / paste / save / reload
  carry everything);
* ``record.renderer`` (``kind="raster_rig"``, own ``version``) holds the
  attachment pose (``root_u`` / ``tip``) and the compositing parameters
  (scale / mode / fill inset / attach depth / deform kind);
* the visible pixels are the composer's canvas-size output written to the
  balloon's part layer (``raster_parts.compose_part_layer_image`` dispatches
  here), exactly like Raster Preserve — so export / flatten see real pixels
  and undo restores the previous part image.

Registration is deterministic from the crops, so a session is rebuilt from
the record whenever it is needed (re-edit, recompose after undo/redo/reload);
the last few registered sessions are cached by their inputs.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time
from collections import OrderedDict
from dataclasses import replace
from typing import Any

import numpy as np
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage

from .commands import ComicEditCommand
from .model import (
    BODY_GEOMETRY_RASTER,
    RENDERER_RASTER_RIG,
    TAIL_GEOMETRY_RASTER,
    BalloonRecord,
    BalloonTail,
    is_raster_rig,
)

logger = logging.getLogger(__name__)

RIG_RENDERER_VERSION = 1
CONVERSION_TYPE_RASTER_RIG = "raster_rig"
#: transparent margin kept around a captured layer crop (registration runs
#: morphology near the silhouette; a tight crop would clip it)
CROP_PAD_PX = 8
_SESSION_CACHE_SIZE = 4


# ── pixel helpers (straight alpha, array-exact round trip) ───────────────
def rgba_to_png_b64(rgba: np.ndarray) -> str:
    arr = np.ascontiguousarray(np.asarray(rgba, dtype=np.uint8))
    h, w = arr.shape[:2]
    image = QImage(arr.data, w, h, w * 4, QImage.Format.Format_RGBA8888).copy()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return base64.b64encode(bytes(buffer.data())).decode("ascii")


def png_b64_to_rgba(data: str) -> np.ndarray | None:
    if not data:
        return None
    image = QImage()
    if not image.loadFromData(base64.b64decode(data.encode("ascii")), "PNG"):
        return None
    converted = image.convertToFormat(QImage.Format.Format_RGBA8888)
    h, w = converted.height(), converted.width()
    ptr = converted.constBits()
    arr = np.frombuffer(ptr, np.uint8, count=converted.bytesPerLine() * h).reshape(
        h, converted.bytesPerLine()
    )[:, : w * 4]
    return arr.reshape(h, w, 4).copy()


def tight_crop_rgba(
    rgba: np.ndarray, origin: tuple[float, float], *, pad: int = CROP_PAD_PX
) -> tuple[np.ndarray, tuple[float, float]] | None:
    """Crop to the drawn pixels (+ ``pad`` transparent px); None = empty."""

    alpha = np.asarray(rgba)[:, :, 3]
    ys, xs = np.nonzero(alpha > 0)
    if xs.size == 0:
        return None
    h, w = alpha.shape
    x0, x1 = max(0, int(xs.min()) - pad), min(w, int(xs.max()) + 1 + pad)
    y0, y1 = max(0, int(ys.min()) - pad), min(h, int(ys.max()) + 1 + pad)
    crop = np.ascontiguousarray(np.asarray(rgba)[y0:y1, x0:x1])
    return crop, (float(origin[0]) + x0, float(origin[1]) + y0)


# ── renderer payload ─────────────────────────────────────────────────────
def rig_renderer_payload(session: Any) -> dict[str, Any]:
    """``BalloonRecord.renderer`` for the session's current pose/params."""

    pose = session.pose
    return {
        "kind": RENDERER_RASTER_RIG,
        "version": RIG_RENDERER_VERSION,
        "pose": {
            "root_u": float(pose.root_u),
            "tip": [float(pose.tip[0]), float(pose.tip[1])],
            "bend": ([float(v) for v in pose.bend] if pose.bend is not None else None),
        },
        "scale": float(session.scale),
        "mode": str(session.mode),
        **(
            {"fidelity": float(session.fidelity), "tip_fidelity": float(session.tip_fidelity)}
            if session.mode == "contour_rebuild"
            else {}
        ),
        "inset_px": float(session.inset_px),
        "overlap_px": (None if session.overlap_px is None else float(session.overlap_px)),
        "deform_kind": session.deform_kind,
    }


def renderer_pose(record: BalloonRecord) -> tuple[float, tuple[float, float]] | None:
    """(root_u, tip) stored in the record's renderer payload."""

    if not is_raster_rig(record):
        return None
    pose = (record.renderer or {}).get("pose") or {}
    try:
        tip = pose.get("tip")
        return float(pose["root_u"]), (float(tip[0]), float(tip[1]))
    except (KeyError, TypeError, ValueError, IndexError):
        return None


def with_renderer_pose(record: BalloonRecord, session: Any) -> BalloonRecord:
    """Record carrying the session's current pose/params (nominal tail tip
    kept in sync for speaker / dialogue features)."""

    payload = rig_renderer_payload(session)
    tip = tuple(payload["pose"]["tip"])
    return replace(
        record,
        renderer=payload,
        tail=replace(record.tail, enabled=True, target=(float(tip[0]), float(tip[1]))),
    )


# ── session <-> record ───────────────────────────────────────────────────
def _apply_renderer_params(session: Any, renderer: dict[str, Any]) -> None:
    from .raster_rig import TailPose  # noqa: PLC0415

    kind = renderer.get("deform_kind")
    if kind != session.deform_kind:
        session.set_deform_kind(kind)  # re-registers from the same crops
    try:
        scale = float(renderer.get("scale") or 1.0)
    except (TypeError, ValueError):
        scale = 1.0
    if scale > 0 and abs(scale - float(session.scale)) > 1e-9:
        session.set_scale(scale)
    mode = str(renderer.get("mode") or session.mode)
    if mode != session.mode:
        session.set_mode(mode)
    session.set_fidelity(renderer.get("fidelity", 0.0))
    session.set_tip_fidelity(renderer.get("tip_fidelity", 0.0))
    inset = renderer.get("inset_px")
    if inset is not None and float(inset) != session.inset_px:
        session.set_inset(float(inset))
    overlap = renderer.get("overlap_px")
    if overlap is not None:
        if session.overlap_px is None or float(overlap) != session.overlap_px:
            session.set_overlap(float(overlap))
    elif session.overlap_px is not None:
        session.set_overlap(None)
    pose = renderer.get("pose") or {}
    try:
        tip = pose.get("tip")
        bend = pose.get("bend")
        session.pose = TailPose(
            float(pose["root_u"]),
            (float(tip[0]), float(tip[1])),
            (float(bend[0]), float(bend[1])) if bend else None,
        )
    except (KeyError, TypeError, ValueError, IndexError):
        pass


_session_cache: OrderedDict[tuple[Any, ...], Any] = OrderedDict()


def _cache_key(record: BalloonRecord, canvas_size: tuple[int, int]) -> tuple[Any, ...]:
    renderer = record.renderer or {}
    digest = hashlib.sha1()
    digest.update(record.body_raster_png.encode("ascii"))
    digest.update(b"|")
    digest.update(record.tail_raster_png.encode("ascii"))
    return (
        digest.hexdigest(),
        tuple(round(float(v), 3) for v in record.body_rect[:2]),
        tuple(round(float(v), 3) for v in (record.tail_rect or (0.0, 0.0))[:2]),
        (int(canvas_size[0]), int(canvas_size[1])),
        round(float(renderer.get("scale") or 1.0), 6),
        renderer.get("deform_kind"),
        renderer.get("mode", "open_ink_fill"),
    )


def rig_session_from_record(
    record: BalloonRecord,
    canvas_size: tuple[int, int],
    *,
    backdrop_rgba: np.ndarray | None = None,
    use_cache: bool = True,
) -> Any | None:
    """Registered session reproducing the record (crops → registration →
    stored pose/params). None when the record is not a rig balloon or the
    crops cannot be registered any more."""

    if not is_raster_rig(record) or record.tail_rect is None:
        return None
    key = _cache_key(record, canvas_size)
    session = _session_cache.get(key) if use_cache else None
    if session is None:
        from .raster_rig_proto import RasterRigProtoSession  # noqa: PLC0415

        body = png_b64_to_rgba(record.body_raster_png)
        tail = png_b64_to_rgba(record.tail_raster_png)
        if body is None or tail is None:
            return None
        renderer = record.renderer or {}
        session = RasterRigProtoSession(
            body_rgba=body,
            body_origin=(float(record.body_rect[0]), float(record.body_rect[1])),
            tail_rgba=tail,
            tail_origin=(float(record.tail_rect[0]), float(record.tail_rect[1])),
            canvas_size=(int(canvas_size[0]), int(canvas_size[1])),
        )
        session.deform_kind = renderer.get("deform_kind")
        session.mode = str(renderer.get("mode") or "open_ink_fill")
        try:
            scale = float(renderer.get("scale") or 1.0)
        except (TypeError, ValueError):
            scale = 1.0
        session.scale = scale if scale > 0 else 1.0
        if not session.register():
            logger.info("rig record %s could not be re-registered", record.balloon_id)
            return None
        if use_cache:
            _session_cache[key] = session
            while len(_session_cache) > _SESSION_CACHE_SIZE:
                _session_cache.popitem(last=False)
    session.backdrop_rgba = backdrop_rgba
    _apply_renderer_params(session, record.renderer or {})
    return session


def compose_rig_part_image(record: BalloonRecord, width: int, height: int) -> QImage:
    """Canvas-size part-layer image of the rig balloon (settle quality).
    When the crops cannot be registered any more (unexpected pixels) the
    crops are blitted statically so the balloon never disappears."""

    from loomstroke.core.document_snapshot_builder import (  # noqa: PLC0415
        DocumentSnapshotBuilder,
    )

    size = (int(width), int(height))
    session = rig_session_from_record(record, size)
    if session is None or session.composer is None:
        return DocumentSnapshotBuilder.array_to_qimage_native_size(static_crops_rgba(record, size))
    session.preview_quality = "settle"
    session.update_preview()
    return DocumentSnapshotBuilder.array_to_qimage_native_size(
        np.ascontiguousarray(session.composer.output)
    )


def static_crops_rgba(record: BalloonRecord, size: tuple[int, int]) -> np.ndarray:
    """Body + tail crops blitted at their rects (no junction compositing)."""

    from .raster_rig.compose import _over  # noqa: PLC0415

    w, h = int(size[0]), int(size[1])
    out = np.zeros((h, w, 4), np.uint8)
    for png, rect in (
        (record.body_raster_png, record.body_rect),
        (record.tail_raster_png, record.tail_rect),
    ):
        rgba = png_b64_to_rgba(png) if rect is not None else None
        if rgba is None:
            continue
        ox, oy = round(rect[0]), round(rect[1])
        x0, y0 = max(0, ox), max(0, oy)
        x1, y1 = min(w, ox + rgba.shape[1]), min(h, oy + rgba.shape[0])
        if x1 <= x0 or y1 <= y0:
            continue
        _over(out[y0:y1, x0:x1], rgba[y0 - oy : y1 - oy, x0 - ox : x1 - ox])
    return out


# ── record + command ─────────────────────────────────────────────────────
def text_frame_rect_for(
    body_rgba: np.ndarray, body_origin: tuple[float, float], *, padding_px: float = 12.0
) -> tuple[float, float, float, float]:
    from .balloonize import inscribed_rect_for_mask, mask_from_alpha  # noqa: PLC0415

    return inscribed_rect_for_mask(
        mask_from_alpha(body_rgba),
        origin=(float(body_origin[0]), float(body_origin[1])),
        padding_px=padding_px,
    )


def build_rig_record(
    session: Any,
    *,
    balloon_id: str,
    panel_id: str = "",
    body_source_layer_id: str = "",
    tail_source_layer_id: str = "",
    timestamp: float | None = None,
) -> BalloonRecord:
    """The confirmed BalloonRecord of a registered session."""

    if session.pose is None or session.data is None:
        raise ValueError("session is not registered")
    bh, bw = session.body_rgba.shape[:2]
    th, tw = session.tail_rgba.shape[:2]
    bx, by = float(session.body_origin[0]), float(session.body_origin[1])
    body_rect = (bx, by, float(bw), float(bh))
    tail_rect = (float(session.tail_origin[0]), float(session.tail_origin[1]), float(tw), float(th))
    tx, ty, tfw, tfh = text_frame_rect_for(session.body_rgba, (bx, by))
    from .balloonize_service import PROVENANCE_KEY  # noqa: PLC0415

    provenance = {
        "schema_version": 2,
        "conversion_type": CONVERSION_TYPE_RASTER_RIG,
        "conversion_mode": CONVERSION_TYPE_RASTER_RIG,
        "source": "separated_body_tail",
        "body_source_layer_id": str(body_source_layer_id or ""),
        "tail_source_layer_id": str(tail_source_layer_id or ""),
        "body_source_kind": "raster",
        "tail_source_kind": "raster",
        "body_source_bbox": [float(v) for v in body_rect],
        "tail_source_bbox": [float(v) for v in tail_rect],
        "converted_at": float(timestamp if timestamp is not None else time.time()),
        "body_raster_size": [int(bw), int(bh)],
        "tail_raster_size": [int(tw), int(th)],
    }
    record = BalloonRecord(
        balloon_id=balloon_id,
        panel_id=panel_id,
        kind="speech_round",  # display hint only; the renderer overrides it
        body_rect=body_rect,
        tail=BalloonTail(
            enabled=True, target=(float(session.pose.tip[0]), float(session.pose.tip[1]))
        ),
        text_object_id=f"comic-text-{balloon_id}",
        auto_size=False,
        metadata={PROVENANCE_KEY: provenance},
        body_geometry_mode=BODY_GEOMETRY_RASTER,
        tail_geometry_mode=TAIL_GEOMETRY_RASTER,
        tail_rect=tail_rect,
        text_frame_rel=(
            (tx - bx) / max(1e-6, float(bw)),
            (ty - by) / max(1e-6, float(bh)),
            tfw / max(1e-6, float(bw)),
            tfh / max(1e-6, float(bh)),
        ),
        body_raster_png=rgba_to_png_b64(session.body_rgba),
        tail_raster_png=rgba_to_png_b64(session.tail_rgba),
        renderer=rig_renderer_payload(session),
    )
    return record


def build_rig_command(
    controller: Any,
    state: Any,
    record: BalloonRecord,
    *,
    text: str = "",
    vertical: bool = True,
    hide_source_layers: bool = True,
) -> ComicEditCommand:
    """One undo unit: balloon + text creation, part layer composed from the
    record, both source layers hidden (never deleted; undo restores)."""

    from .balloon_objects import build_balloon_object  # noqa: PLC0415
    from .balloonize_service import PROVENANCE_KEY  # noqa: PLC0415
    from .text_frames import build_balloon_text_object  # noqa: PLC0415

    balloon_obj = build_balloon_object(record)
    if balloon_obj is None:
        raise ValueError("rig balloon object could not be built")
    text_obj = build_balloon_text_object(record, text, vertical=vertical)
    provenance = record.metadata.get(PROVENANCE_KEY) or {}
    visibility: dict[str, bool] = {}
    comic_layer_ids = {state.frames_layer_id, state.balloons_layer_id, state.text_layer_id}
    if hide_source_layers:
        for key in ("body_source_layer_id", "tail_source_layer_id"):
            layer_id = str(provenance.get(key) or "")
            if layer_id and layer_id not in comic_layer_ids:
                visibility[layer_id] = False
    command = ComicEditCommand(
        label="comic_balloonize_raster_rig",
        ensure_layers=controller._ensure_balloon_layers(state),  # noqa: SLF001
        additions=[
            (controller._balloons_layer_id(state), balloon_obj),  # noqa: SLF001
            (controller._text_layer_id(state), text_obj),  # noqa: SLF001
        ],
        layer_visibility_updates=visibility,
        raster_parts=[record],
    )
    from .panel_balloon_adoption_plan import adopt_created_balloon  # noqa: PLC0415

    return adopt_created_balloon(controller, state, record, command)


__all__ = [
    "CONVERSION_TYPE_RASTER_RIG",
    "CROP_PAD_PX",
    "RIG_RENDERER_VERSION",
    "build_rig_command",
    "build_rig_record",
    "compose_rig_part_image",
    "png_b64_to_rgba",
    "renderer_pose",
    "rgba_to_png_b64",
    "rig_renderer_payload",
    "rig_session_from_record",
    "static_crops_rgba",
    "text_frame_rect_for",
    "tight_crop_rgba",
    "with_renderer_pose",
]
