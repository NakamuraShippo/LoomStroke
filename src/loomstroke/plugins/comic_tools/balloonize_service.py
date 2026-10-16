"""Balloonize integration: canvas inputs → proposal → procedural balloon.

Bridges the pure estimation pipeline (:mod:`balloonize`) to the canvas:
input extraction (selection / layer / vector path), and the confirmed
conversion into a REGULAR comic_tools balloon — the result flows through
the exact same BalloonRecord/command path as hand-created balloons, with
conversion provenance stored in the record metadata.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .balloonize import (
    MODE_CLEAN,
    TAIL_NONE,
    BalloonizeError,
    BalloonizeProposal,
    mask_from_alpha,
    polygon_mask,
    propose_balloon,
)
from .commands import ComicEditCommand
from .model import BalloonRecord, BalloonTail
from .service import ComicPageController, ComicPageState
from .text_frames import build_balloon_text_object

SOURCE_SELECTION = "selection"
SOURCE_LAYER = "layer"
SOURCE_VECTOR = "vector_object"

PROVENANCE_KEY = "balloonize"


@dataclass(frozen=True)
class BalloonizeContext:
    """One pending conversion: proposal + where the input came from."""

    proposal: BalloonizeProposal
    source: str
    source_layer_id: str = ""
    source_object_id: str = ""


def _qimage_to_rgba(image: Any) -> Any:
    from loomstroke.core.document_snapshot_builder import (  # noqa: PLC0415
        DocumentSnapshotBuilder,
    )

    return DocumentSnapshotBuilder.qimage_to_array(image)


def propose_from_selection(canvas: Any, *, padding_px: float = 12.0) -> BalloonizeContext | None:
    """Priority input 1: the single balloon inside the current selection."""

    payload = canvas.get_selection_snapshot()
    if payload is None:
        return None
    mask = mask_from_alpha(_qimage_to_rgba(payload.image))
    x, y, _w, _h = payload.bounds
    proposal = propose_balloon(mask, origin=(float(x), float(y)), padding_px=padding_px)
    return BalloonizeContext(
        proposal=proposal,
        source=SOURCE_SELECTION,
        source_layer_id=str(payload.source_layer_id or ""),
    )


def propose_from_layer(
    canvas: Any, *, layer_id: str | None = None, padding_px: float = 12.0
) -> BalloonizeContext:
    """Priority input 2: the single balloon drawn on one raster layer."""

    layer = None
    if layer_id:
        getter = getattr(canvas, "_get_layer_by_id", None)
        layer = getter(layer_id) if callable(getter) else None
    if layer is None:
        layer = canvas._get_active_layer()  # noqa: SLF001 - canvas-internal accessor
    image = getattr(layer, "image", None)
    if layer is None or image is None or (hasattr(image, "isNull") and image.isNull()):
        raise BalloonizeError("the target layer has no drawable content")
    vector_objects = getattr(layer, "vector_objects", None) or []
    if vector_objects:
        return _propose_from_vector_object(layer, vector_objects[0], padding_px=padding_px)
    mask = mask_from_alpha(_qimage_to_rgba(image))
    proposal = propose_balloon(mask, origin=(0.0, 0.0), padding_px=padding_px)
    return BalloonizeContext(
        proposal=proposal,
        source=SOURCE_LAYER,
        source_layer_id=str(getattr(layer, "id", "") or ""),
    )


def _propose_from_vector_object(layer: Any, obj: Any, *, padding_px: float) -> BalloonizeContext:
    """Priority input 3: use the vector path itself instead of re-rasterizing."""

    paths = getattr(obj, "paths", None) or []
    if not paths:
        raise BalloonizeError("vector object has no paths")
    points = [(float(a.pos[0]), float(a.pos[1])) for a in paths[0].anchors]
    mask, origin = polygon_mask(points)
    proposal = propose_balloon(mask, origin=origin, padding_px=padding_px)
    return BalloonizeContext(
        proposal=proposal,
        source=SOURCE_VECTOR,
        source_layer_id=str(getattr(layer, "id", "") or ""),
        source_object_id=str(getattr(obj, "id", "") or ""),
    )


def build_confirmed_record(
    context: BalloonizeContext,
    *,
    balloon_id: str,
    panel_id: str,
    kind: str | None = None,
    body_rect: tuple[float, float, float, float] | None = None,
    tail_enabled: bool | None = None,
    tail_tip: tuple[float, float] | None = None,
    timestamp: float | None = None,
) -> BalloonRecord:
    """The user-confirmed BalloonRecord (regular comic_tools structure).

    Every argument overrides the proposal — this is the correction surface
    of the semi-automatic flow. ``auto_size`` starts OFF so the recognized
    hand-drawn size is preserved until the user opts into auto sizing.
    """

    proposal = context.proposal
    rect = body_rect or proposal.body_rect
    resolved_tail_enabled = (
        tail_enabled if tail_enabled is not None else proposal.tail.confidence != TAIL_NONE
    )
    tip = tail_tip or proposal.tail.tip
    if not resolved_tail_enabled or tip == (0.0, 0.0):
        tip = (rect[0] + rect[2] * 0.5, rect[1] + rect[3] * 1.2)
    width_ratio = 0.16
    if proposal.tail.width_px > 0 and rect[2] > 0:
        width_ratio = min(0.35, max(0.06, proposal.tail.width_px / rect[2]))
    provenance = {
        "schema_version": 1,
        "source": context.source,
        "source_layer_id": context.source_layer_id,
        "source_object_id": context.source_object_id,
        "source_bbox": [float(v) for v in proposal.source_bbox],
        "conversion_mode": MODE_CLEAN,
        "converted_at": float(timestamp if timestamp is not None else time.time()),
        "tail_confidence": proposal.tail.confidence,
        "suggested_kind": proposal.kind,
    }
    return BalloonRecord(
        balloon_id=balloon_id,
        panel_id=panel_id,
        kind=kind or proposal.kind,
        body_rect=rect,
        tail=BalloonTail(
            enabled=resolved_tail_enabled,
            target=tip,
            width_ratio=width_ratio,
        ),
        text_object_id=f"comic-text-{balloon_id}",
        auto_size=False,
        speaker_id="",
        speaker_name="",
        metadata={PROVENANCE_KEY: provenance},
    )


def balloonize_provenance(record: BalloonRecord) -> Mapping[str, Any] | None:
    payload = record.metadata.get(PROVENANCE_KEY)
    return payload if isinstance(payload, Mapping) else None


def build_convert_command(
    controller: ComicPageController,
    state: ComicPageState,
    record: BalloonRecord,
    *,
    text: str = "",
    vertical: bool = True,
    hide_source_layer: bool = True,
) -> ComicEditCommand:
    """One undo unit: create balloon + text objects, optionally hide the
    hand-drawn source layer. The source is NEVER deleted."""

    from .balloon_objects import build_balloon_object  # noqa: PLC0415

    balloon_obj = build_balloon_object(record)
    text_obj = build_balloon_text_object(record, text, vertical=vertical)
    provenance = balloonize_provenance(record) or {}
    source_layer_id = str(provenance.get("source_layer_id") or "")
    visibility: dict[str, bool] = {}
    comic_layer_ids = {
        state.frames_layer_id,
        state.balloons_layer_id,
        state.text_layer_id,
    }
    if hide_source_layer and source_layer_id and source_layer_id not in comic_layer_ids:
        visibility[source_layer_id] = False
    command = ComicEditCommand(
        label="comic_balloonize",
        ensure_layers=controller._ensure_balloon_layers(state),  # noqa: SLF001
        additions=[
            (controller._balloons_layer_id(state), balloon_obj),  # noqa: SLF001
            (controller._text_layer_id(state), text_obj),  # noqa: SLF001
        ],
        layer_visibility_updates=visibility,
    )
    from .panel_balloon_adoption_plan import adopt_created_balloon  # noqa: PLC0415

    return adopt_created_balloon(controller, state, record, command)


__all__ = [
    "PROVENANCE_KEY",
    "SOURCE_LAYER",
    "SOURCE_SELECTION",
    "SOURCE_VECTOR",
    "BalloonizeContext",
    "balloonize_provenance",
    "build_confirmed_record",
    "build_convert_command",
    "propose_from_layer",
    "propose_from_selection",
]
