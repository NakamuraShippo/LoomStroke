"""Registration: body + tail images → rig data + report — BZ4R spike."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from typing import Any

import numpy as np

from .compose import TailPose
from .guide import AttachmentGuide, build_attachment_guide
from .recipe import (
    CompositingRecipe,
    RasterRigData,
    SourceRef,
    TailPoseData,
    propose_recipe,
)
from .tail_rig import TailRig, build_tail_rig


def rgba_hash(rgba: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(rgba).tobytes()).hexdigest()


@dataclass
class RegistrationReport:
    ok: bool
    reasons: list[str] = field(default_factory=list)  # hard failures (unsupported)
    warnings: list[str] = field(default_factory=list)  # needs user confirmation
    proposals: dict[str, Any] = field(default_factory=dict)


def register_raster_rig(  # noqa: PLR0912 - one validation pipeline
    body_rgba: np.ndarray,
    body_origin: tuple[float, float],
    tail_rgba: np.ndarray,
    tail_origin: tuple[float, float],
    *,
    body_layer_id: str = "body",
    tail_layer_id: str = "tail",
    deform_kind: str | None = None,
    recipe: CompositingRecipe | None = None,
    tail_scale: float = 1.0,
) -> tuple[RasterRigData | None, RegistrationReport]:
    """Build the rig from a complete BODY image and a separate TAIL image.

    Returns ``(data, report)``; ``data`` is None when a structural condition
    is not met (the report says which). Automatic choices are proposals the
    registration UI is expected to show for confirmation.

    ``tail_scale`` is the non-destructive ``source_to_rig_scale``: the tail
    image is never touched; the rig is detected on the source pixels and
    the scale is applied to the rig geometry (anchored at the base centre)
    and folded into the per-frame inverse map. ``1.0`` is bit-identical to
    the unscaled pipeline.
    """

    report = RegistrationReport(ok=True)
    tail_scale = TailRig.validate_scale(tail_scale)
    try:
        guide: AttachmentGuide = build_attachment_guide(body_rgba, body_origin)
    except ValueError as exc:
        report.ok = False
        report.reasons.append(f"body: {exc}")
        return None, report
    if guide.perimeter < 24:
        report.ok = False
        report.reasons.append("body: main perimeter too small")
        return None, report
    try:
        rig: TailRig = build_tail_rig(tail_rgba, tail_origin, guide, deform_kind=deform_kind)
    except ValueError as exc:
        report.ok = False
        report.reasons.append(f"tail: {exc}")
        return None, report
    if tail_scale != 1.0:
        rig = replace(rig, source_to_rig_scale=tail_scale)
    rig_px = rig.rig_space()
    # structural conditions (rig px)
    if rig_px.base_width < 2.0:
        report.ok = False
        report.reasons.append("tail: base width < 2px (no attachable base)")
    if rig.contact_gap > max(24.0, guide.line_width * 8):
        report.warnings.append(
            f"tail: drawn {rig.contact_gap:.1f}px away from the body — attached by overlap"
        )
    for note in rig.notes:
        if "places" in note:
            report.ok = False
            report.reasons.append(f"tail: {note} (multi-attach tails are unsupported)")
        else:
            report.warnings.append(f"tail: {note}")
    if guide.decorations:
        report.warnings.append(
            f"body: {len(guide.decorations)} separated decoration(s) kept off-guide"
        )
    if not report.ok:
        return None, report
    recipe = recipe or propose_recipe(guide, rig_px)
    # registered pose: the drawn placement (root = contact u, tip = drawn tip
    # at the registered scale)
    tip_page = (rig_px.origin[0] + rig_px.tip[0], rig_px.origin[1] + rig_px.tip[1])
    pose = TailPose(root_u=rig.contact_u, tip=tip_page, bend=None)
    data = RasterRigData(
        body_source=SourceRef(
            body_layer_id, body_rgba.shape[1], body_rgba.shape[0], rgba_hash(body_rgba)
        ),
        tail_source=SourceRef(
            tail_layer_id, tail_rgba.shape[1], tail_rgba.shape[0], rgba_hash(tail_rgba)
        ),
        attachment_guide=guide,
        tail_rig=rig,
        attachment=pose.to_data(),
        compositing_recipe=recipe,
        registered_pose=TailPoseData(pose.root_u, pose.tip, None, False),
    )
    report.proposals = {
        "deform_kind": rig.deform_kind,
        "recipe": recipe.to_dict(),
        "fill_ratio": guide.fill_ratio,
        "line_width": guide.line_width,
        "base_width": rig_px.base_width,
        "base_width_source_px": rig.base_width,
        "source_to_rig_scale": rig.source_to_rig_scale,
        "contact_u": rig.contact_u,
    }
    return data, report


def rescale_rig(
    data: RasterRigData,
    scale: float,
    *,
    guide: AttachmentGuide | None = None,
    recipe: CompositingRecipe | None = None,
) -> RasterRigData:
    """New rig data with ``source_to_rig_scale`` = ``scale`` (uniform,
    positive, finite). Always derived from the SOURCE-space rig that the
    registration measured — never from a previously scaled rig — so
    repeated rescaling cannot accumulate error. Measurement-dependent recipe
    parameters are re-proposed from the rig-space measurements unless a
    ``recipe`` is given (a user-confirmed recipe the caller wants to keep).
    The registered pose keeps ``root_u`` (normalised outline position) and
    re-derives its tip from the scaled rig. Sources and their hashes are
    untouched."""

    scale = TailRig.validate_scale(scale)
    src_rig = replace(data.tail_rig, source_to_rig_scale=1.0)
    rig = replace(src_rig, source_to_rig_scale=scale) if scale != 1.0 else src_rig
    rig_px = rig.rig_space()
    guide = guide or data.attachment_guide
    new_recipe = recipe or propose_recipe(guide, rig_px)
    tip_page = (rig_px.origin[0] + rig_px.tip[0], rig_px.origin[1] + rig_px.tip[1])
    reg = TailPoseData(rig.contact_u, tip_page, None, False)
    return replace(
        data,
        tail_rig=rig,
        compositing_recipe=new_recipe,
        attachment=reg,
        registered_pose=reg,
    )


__all__ = ["RegistrationReport", "register_raster_rig", "rescale_rig", "rgba_hash"]
