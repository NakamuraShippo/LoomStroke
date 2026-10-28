"""Raster rig session: registration + pose + preview compositing.

The session is the engine behind "Body/Tail レイヤーからフキダシ化": it is
built from two raster layers (pixels copied read-only), composes the
junction + tail for the interactive preview (the comic tool pushes the
result into the transient compositor override) and, on confirm, its pose /
parameters are written into a ``BalloonRecord`` (``raster_rig_commit``).
The same session type is rebuilt from the record for re-editing and for
recomposing the balloon's part layer.

The session object is pure state + composition (fully unit-testable
without widgets); it never writes document pixels itself.
"""

from __future__ import annotations

import logging
import statistics
import time
from dataclasses import dataclass, field, replace
from functools import partial
from typing import Any

import numpy as np

from .raster_rig import (
    RigComposer,
    TailPose,
    register_raster_rig,
    rescale_rig,
)
from .raster_rig.contour_compose import CONTOUR_MODE
from .raster_rig.contour_session import drag_contour, rebuild_contour
from .raster_rig.contour_tail_curve import middle_point

logger = logging.getLogger(__name__)

#: compositing modes exposed in the prototype UI: (stable value, needs
#: opening override). Display names go through Qt tr() in the dialog.
COMPOSITING_MODES = ("registration_default", "open_forced", "open_ink_fill", CONTOUR_MODE)

HANDLE_SCREEN_PX = 12.0  # zoom-independent hit target (screen px)


def _load_ink_fill_composer() -> Any | None:
    """The image-preserving local composer for the existing default mode."""

    from .raster_rig.local_fill import LocalFillComposer  # noqa: PLC0415

    return LocalFillComposer


@dataclass
class PerfSample:
    pose_ms: float
    compose_ms: float
    convert_ms: float
    patch_w: int
    patch_h: int
    bytes_uploaded: int


@dataclass
class RasterRigProtoSession:
    """Runtime-only rig session: registration + pose + preview compositing.

    Never mutates the source arrays; every preview frame is recomputed from
    the pristine sources by the composer. There is no document Command —
    the session is PREVIEW-ONLY (see the report: undo integration is out of
    scope for this prototype and is not claimed).
    """

    body_rgba: np.ndarray
    body_origin: tuple[float, float]
    tail_rgba: np.ndarray
    tail_origin: tuple[float, float]
    canvas_size: tuple[int, int]
    #: approximate composite of the OTHER layers (everything except the
    #: body/tail layers) at canvas size, used to paint what a TRANSPARENT
    #: opening reveals — the overlay can only draw on top, so "erased"
    #: outline pixels are previewed by drawing this backdrop over them.
    #: None -> plain white paper is assumed. Approximation only (layer
    #: blend modes are ignored); the real erasure happens at bake time.
    backdrop_rgba: np.ndarray | None = None

    data: Any = field(default=None, init=False)
    report: Any = field(default=None, init=False)
    composer: Any = field(default=None, init=False)
    pose: TailPose | None = field(default=None, init=False)
    mode: str = field(default="open_ink_fill", init=False)
    scale: float = field(default=1.0, init=False)
    fidelity: float = field(default=0.0, init=False)
    tip_fidelity: float = field(default=0.0, init=False)
    inset_px: float = field(default=0.0, init=False)
    #: user override of ``CompositingRecipe.overlap_px`` (how far the tail
    #: base is pushed INTO the body along the inward normal; negative =
    #: outside the outline); None = the registration's value
    overlap_px: float | None = field(default=None, init=False)
    deform_kind: str | None = field(default=None, init=False)
    composer_kind: str = field(default="standard", init=False)
    #: preview quality of the LAST composed frame: "interactive" (coarse
    #: band during a drag) | "settle" (exact, after release / on change)
    preview_quality: str = field(default="settle", init=False)
    #: monotonically increasing pose/parameter revision; every composed
    #: frame carries the revision it was computed for (stale-frame guard)
    revision: int = field(default=0, init=False)
    frame_revision: int = field(default=-1, init=False)
    perf: list[PerfSample] = field(default_factory=list, init=False)
    dirty: tuple[int, int, int, int] | None = field(default=None, init=False)
    _drag: dict[str, Any] | None = field(default=None, init=False)
    _source_data: Any = field(default=None, init=False)

    # ── registration ──────────────────────────────────────────────────
    def register(self) -> bool:
        data, report = register_raster_rig(
            self.body_rgba,
            self.body_origin,
            self.tail_rgba,
            self.tail_origin,
            deform_kind=self.deform_kind,
            tail_scale=self.scale,
        )
        self.report = report
        if data is None:
            self.data = self.composer = self.pose = None
            return False
        self._source_data = data
        self.data = data
        self.pose = TailPose.from_data(data.registered_pose)
        self.inset_px = max(1.5, min(12.0, data.attachment_guide.line_width))  # 1.0 x body lw
        self._contour_registered = False
        try:
            self._rebuild_composer()
        except ValueError as exc:
            if self.mode != CONTOUR_MODE:
                raise
            self.report.ok = False
            self.report.reasons.append(str(exc))
            self.data = self.composer = self.pose = None
            return False
        return True

    def _debug_dump(self) -> None:
        """Keep junction diagnostics only after an explicit image opt-in."""
        from loomstroke.platform.image_diagnostics import write_image_diagnostic  # noqa: PLC0415

        def build_payloads() -> tuple[bytes | None, bytes]:
            import json  # noqa: PLC0415
            from io import BytesIO  # noqa: PLC0415

            from PIL import Image  # noqa: PLC0415

            patch = self.preview_patch()
            png = None
            if patch is not None:
                _rect, rgba = patch
                stream = BytesIO()
                Image.fromarray(rgba).save(stream, format="PNG")
                png = stream.getvalue()
            info = self.proposal_summary()
            info["inset_px"] = self.inset_px
            info["recipe"] = self.composer.recipe.to_dict()
            info["fill_rgb"] = getattr(self.composer, "fill_rgb", None)
            info["stroke_info"] = getattr(self.composer, "stroke_info", None)
            return png, json.dumps(info, indent=2, default=str).encode("utf-8")

        write_image_diagnostic("rig", build_payloads)

    def proposal_summary(self) -> dict[str, Any]:
        """Registration facts shown for confirmation (step 3 of the flow)."""

        if self.data is None:
            return {"ok": False, "reasons": list(getattr(self.report, "reasons", []))}
        rig = self.data.tail_rig.rig_space()
        g = self.data.attachment_guide
        return {
            "ok": True,
            "perimeter_px": g.perimeter,
            "root_u": self.data.tail_rig.contact_u,
            "base_left": rig.base_left,
            "base_right": rig.base_right,
            "base_width_px": rig.base_width,
            "tip": rig.tip,
            "source_to_rig_scale": self.data.tail_rig.source_to_rig_scale,
            "deform_kind": rig.deform_kind,
            "compositing_mode": self.mode,
            "overlap_px": self.effective_overlap_px(),
            "composer_kind": self.composer_kind,
            "warnings": list(getattr(self.report, "warnings", [])),
        }

    # ── parameters ────────────────────────────────────────────────────
    def set_scale(self, value: float) -> None:
        value = float(value)
        if not (value > 0 and np.isfinite(value)) or self._source_data is None:
            return
        self.scale = value
        # ALWAYS derived from the source-space registration (no cumulative
        # degradation); the current pose (root_u + tip) is kept
        self.data = rescale_rig(self._source_data, value)
        self._rebuild_composer()

    def set_fidelity(self, value: float) -> None:
        from .raster_rig.contour_detail import normalized_fidelity  # noqa: PLC0415

        value = normalized_fidelity(value)
        if self.fidelity == value:
            return
        self.fidelity = value
        if self.mode == CONTOUR_MODE and self.composer is not None:
            self.composer.fidelity = value
            self.update_preview()

    def set_tip_fidelity(self, value: float) -> None:
        from .raster_rig.contour_detail import normalized_fidelity  # noqa: PLC0415

        value = normalized_fidelity(value)
        if self.tip_fidelity == value:
            return
        self.tip_fidelity = value
        if self.mode == CONTOUR_MODE and self.composer is not None:
            self.composer.tip_fidelity = value
            self.update_preview()

    def set_inset(self, value: float) -> None:
        value = float(value)
        if value < 0 or not np.isfinite(value):
            return
        self.inset_px = value
        self._rebuild_composer()

    def set_overlap(self, value: float | None) -> None:
        """Override how far the tail base sits INTO the body (px along the
        inward outline normal; negative = outside). None = registration."""

        if value is not None:
            value = float(value)
            if not np.isfinite(value):
                return
        self.overlap_px = value
        self._rebuild_composer()

    def effective_overlap_px(self) -> float | None:
        """The overlap the composer is using (override or registration)."""

        if self.data is None:
            return None
        if self.overlap_px is not None:
            return float(self.overlap_px)
        return float(self.data.compositing_recipe.overlap_px)

    def set_mode(self, mode: str) -> None:
        if mode not in COMPOSITING_MODES:
            return
        self.mode = mode
        self._rebuild_composer()

    def set_deform_kind(self, kind: str | None) -> None:
        self.deform_kind = kind
        if self._source_data is not None:
            self.register()

    def _rebuild_composer(self) -> None:
        if self.data is None:
            return
        if self.mode == CONTOUR_MODE:
            self.composer = rebuild_contour(self)
            self.composer_kind = CONTOUR_MODE
            self.update_preview()
            return
        ink_fill = _load_ink_fill_composer()
        recipe = self.data.compositing_recipe
        if self.mode in ("open_forced", "open_ink_fill") and not recipe.opening_enabled:
            recipe = replace(recipe, opening_enabled=True)
        if self.overlap_px is not None:
            recipe = replace(recipe, overlap_px=float(self.overlap_px))
        cls: Any = RigComposer
        self.composer_kind = "standard"
        if self.mode == "open_ink_fill":
            if ink_fill is not None:
                cls = partial(ink_fill, junction_fill_inset_px=self.inset_px)
                self.composer_kind = "open_ink_fill"
            else:
                # NOT silent: the dialog surfaces this state to the user
                self.composer_kind = "fallback_standard"
                logger.warning(
                    "open_ink_fill composer unavailable (harness module not "
                    "importable) — falling back to the standard composer; the "
                    "opening is transparent and cannot show in the overlay"
                )
        self.composer = cls(
            self.body_rgba,
            self.tail_rgba,
            self.data.attachment_guide,
            self.data.tail_rig,
            recipe,
            self.canvas_size,
        )
        if self.pose is not None:
            self.update_preview()
            self._debug_dump()

    def opening_visible_in_overlay(self) -> bool:
        """A fill-colour opening replaces pixels and shows directly; a
        TRANSPARENT opening is previewed by painting the backdrop
        approximation over the erased pixels (see ``preview_patch``)."""

        return True  # both paths are previewable since the backdrop pass

    # ── handles ───────────────────────────────────────────────────────
    def root_point(self) -> tuple[float, float] | None:
        if self.data is None or self.pose is None:
            return None
        g = self.data.attachment_guide
        p = g.point_at(self.pose.root_u)
        return (p[0] + g.origin[0], p[1] + g.origin[1])

    def tip_point(self) -> tuple[float, float] | None:
        return None if self.pose is None else self.pose.tip

    def middle_point(self) -> tuple[float, float] | None:
        if self.mode != CONTOUR_MODE or self.composer is None or self.pose is None:
            return None
        return middle_point(self.composer.geometry, self.pose)

    def hit_test(self, point: tuple[float, float], zoom: float) -> str | None:
        tol = HANDLE_SCREEN_PX / max(0.05, float(zoom))
        for kind, pt in (
            ("tip", self.tip_point()),
            ("root", self.root_point()),
            ("middle", self.middle_point()),
        ):
            if pt is None:
                continue
            if (point[0] - pt[0]) ** 2 + (point[1] - pt[1]) ** 2 <= tol * tol:
                return kind
        return None

    def begin_drag(self, kind: str) -> None:
        kinds = ("root", "tip", "middle") if self.mode == CONTOUR_MODE else ("root", "tip")
        if self.pose is None or kind not in kinds:
            return
        self._drag = {"kind": kind, "pose0": self.pose}
        if kind == "root" and self.data is not None:
            # remember where the tip sits in the outline's LOCAL frame at the
            # root (outward normal / tangent components) so the tail swings
            # around the body as the root slides along the perimeter
            g = self.data.attachment_guide
            p0, t0, n0 = g.frame_at(self.pose.root_u)
            dx = self.pose.tip[0] - g.origin[0] - p0[0]
            dy = self.pose.tip[1] - g.origin[1] - p0[1]
            self._drag["tip_frame"] = (dx * n0[0] + dy * n0[1], dx * t0[0] + dy * t0[1])
        self._set_quality("interactive")

    def drag_to(self, point: tuple[float, float]) -> None:
        if self._drag is None or self.data is None or self.pose is None:
            return
        if self.mode == CONTOUR_MODE:
            drag_contour(self, point)
            return
        g = self.data.attachment_guide
        if self._drag["kind"] == "root":
            # the root always lives ON the outline: the allowed interval is
            # the whole closed outline, and the projection IS the clamp.
            # The tip FOLLOWS the root AROUND the outline: its offset is kept
            # in the root's local (outward normal, tangent) frame, so the
            # tail swings with the perimeter instead of being stretched into
            # an extreme pose (user request, 2026-08-23)
            local = (point[0] - g.origin[0], point[1] - g.origin[1])
            new_u = g.nearest_u(local)
            tip = self.pose.tip
            frame = self._drag.get("tip_frame")
            if frame is not None:
                p1, t1, n1 = g.frame_at(new_u)
                dn, dt = frame
                tip = (
                    g.origin[0] + p1[0] + dn * n1[0] + dt * t1[0],
                    g.origin[1] + p1[1] + dn * n1[1] + dt * t1[1],
                )
            self.pose = TailPose(new_u, tip, self.pose.bend)
        else:
            self.pose = TailPose(
                self.pose.root_u, (float(point[0]), float(point[1])), self.pose.bend
            )
        self.update_preview()

    def end_drag(self) -> None:
        """Pointer release: SETTLE — recompute the final pose at full
        quality from the sources (never from the interactive frame)."""

        self._drag = None
        self._set_quality("settle")
        self.update_preview()

    def cancel_drag(self) -> None:
        """Pointer cancel: back to the pose at drag start (ephemeral)."""

        if self._drag is not None:
            self.pose = self._drag["pose0"]
            self._drag = None
            self._set_quality("settle")
            self.update_preview()

    def _set_quality(self, quality: str) -> None:
        self.preview_quality = quality
        if self.composer is not None:
            self.composer.preview_quality = quality

    @property
    def dragging(self) -> bool:
        return self._drag is not None

    def reset(self) -> None:
        """Pose Reset: back to the REGISTERED pose at the registered scale
        (returning to source size is `set_scale(1.0)`, a separate action)."""

        if self.data is not None and self.data.registered_pose is not None:
            self._drag = None
            self.pose = TailPose.from_data(self.data.registered_pose)
            self.update_preview()

    # ── preview ───────────────────────────────────────────────────────
    def update_preview(self) -> tuple[int, int, int, int] | None:
        if self.composer is None or self.pose is None:
            return None
        t0 = time.perf_counter()
        pose = self.pose
        self.revision += 1
        rev = self.revision
        t1 = time.perf_counter()
        self.composer.preview_quality = self.preview_quality
        dirty = self.composer.update(pose)
        self.frame_revision = rev
        t2 = time.perf_counter()
        rect = self._preview_rect()
        patch_w = rect[2] if rect else 0
        patch_h = rect[3] if rect else 0
        self.dirty = dirty
        self.perf.append(
            PerfSample(
                pose_ms=(t1 - t0) * 1000.0,
                compose_ms=(t2 - t1) * 1000.0,
                convert_ms=0.0,  # filled in by the overlay painter
                patch_w=patch_w,
                patch_h=patch_h,
                bytes_uploaded=patch_w * patch_h * 4,
            )
        )
        if len(self.perf) > 2000:
            del self.perf[:1000]
        return dirty

    def _preview_rect(self) -> tuple[int, int, int, int] | None:
        c = self.composer
        rects = [r for r in (c.opening_rect(), c.tail_rect()) if r is not None]
        if not rects:
            return None
        x0 = min(r[0] for r in rects)
        y0 = min(r[1] for r in rects)
        x1 = max(r[0] + r[2] for r in rects)
        y1 = max(r[1] + r[3] for r in rects)
        return (x0, y0, x1 - x0, y1 - y0)

    def preview_patch(self) -> tuple[tuple[int, int, int, int], np.ndarray] | None:
        """(rect, RGBA uint8) of the composed junction+tail region — the
        only pixels the overlay draws (the document itself is untouched)."""

        rect = self._preview_rect()
        if rect is None:
            return None
        x, y, w, h = rect
        patch = self.composer.output[y : y + h, x : x + w]
        opened = self._opened_strength(rect)
        if opened is None:
            return rect, patch
        # transparent opening: put the backdrop (or white paper) UNDER the
        # patch at exactly the erased pixels so the overlay can show the
        # opening. Rebuilt from the composer state every call — no caches.
        if self.backdrop_rgba is not None:
            back = self.backdrop_rgba[y : y + h, x : x + w].astype(np.float32)
            ba = back[:, :, 3:4] / 255.0
            back_rgb = back[:, :, :3] * ba + 255.0 * (1.0 - ba)
        else:
            back_rgb = np.full((h, w, 3), 255.0, np.float32)
        out = patch.astype(np.float32)
        pa = out[:, :, 3:4] / 255.0
        op = opened[:, :, None]
        rgb = out[:, :, :3] * pa + back_rgb * (1.0 - pa)
        a = np.maximum(out[:, :, 3:4], op * 255.0)
        blended = np.where(op > 0.003, np.concatenate([rgb, a], axis=2), out)
        return rect, np.clip(np.rint(blended), 0, 255).astype(np.uint8)

    def _opened_strength(self, rect: tuple[int, int, int, int]) -> np.ndarray | None:
        """(h, w) float 0..1: how much body alpha the opening REMOVED at
        each pixel of ``rect`` (0 everywhere for fill-colour openings)."""

        mask = getattr(self.composer, "_last_mask", None)
        if mask is None:
            return None
        x, y, w, h = rect
        body_a = self.composer.body_canvas[y : y + h, x : x + w, 3].astype(np.float32)
        region = getattr(self.composer, "body_region", None)
        body_out = (
            region(rect) if callable(region) else self.composer.body_output()[y : y + h, x : x + w]
        )
        out_a = body_out[:, :, 3].astype(np.float32)
        opened = (body_a - out_a) * (1.0 / 255.0)
        np.clip(opened, 0.0, 1.0, out=opened)
        return opened if float(opened.max()) > 0.003 else None

    def body_preview_region(self, rect: tuple[int, int, int, int]) -> np.ndarray:
        """Body-layer pixels (opening applied) for ``rect`` — what the real
        compositor should draw in place of the document body there."""

        region = getattr(self.composer, "body_region", None)
        if callable(region):
            return region(rect)
        x, y, w, h = rect
        return self.composer.body_output()[y : y + h, x : x + w]

    def diagnostics(self) -> dict[str, Any]:
        """Developer diagnostics (not shown in the normal UI)."""

        p = self.perf_summary()
        return {
            "root_u": None if self.pose is None else round(self.pose.root_u, 4),
            "scale": self.scale,
            "fill_inset_px": round(self.inset_px, 2),
            "overlap_px": (
                None
                if self.effective_overlap_px() is None
                else round(self.effective_overlap_px(), 2)
            ),
            "preview_quality": self.preview_quality,
            "last_frame_ms": (
                round(
                    self.perf[-1].pose_ms + self.perf[-1].compose_ms + self.perf[-1].convert_ms,
                    2,
                )
                if self.perf
                else None
            ),
            "p95_ms": round(p["p95_ms"], 2) if p.get("frames") else None,
            "tail_patch": ((self.perf[-1].patch_w, self.perf[-1].patch_h) if self.perf else None),
            "revision": self.revision,
            "frame_revision": self.frame_revision,
            "composer_kind": self.composer_kind,
        }

    def perf_summary(self) -> dict[str, Any]:
        if not self.perf:
            return {"frames": 0}
        totals = [p.pose_ms + p.compose_ms + p.convert_ms for p in self.perf]
        totals_sorted = sorted(totals)
        p95 = totals_sorted[min(len(totals_sorted) - 1, int(0.95 * len(totals_sorted)))]
        return {
            "frames": len(totals),
            "median_ms": statistics.median(totals),
            "p95_ms": p95,
            "max_ms": max(totals),
            "patch_px_median": statistics.median(p.patch_w * p.patch_h for p in self.perf),
            "upload_bytes_median": statistics.median(p.bytes_uploaded for p in self.perf),
            "body_texture_reuploads": 0,  # the document is never touched
        }
