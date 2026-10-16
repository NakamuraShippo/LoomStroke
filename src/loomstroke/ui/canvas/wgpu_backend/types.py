"""Types and error classes for the optional wgpu offscreen backend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


class WgpuBackendError(RuntimeError):
    """Base class for typed wgpu backend failures."""


class WgpuUnavailableError(WgpuBackendError):
    """Raised when the optional wgpu runtime or adapter is unavailable."""


class WgpuDeviceLostError(WgpuBackendError):
    """Raised when a previously created wgpu device can no longer be used."""


class WgpuShaderError(WgpuBackendError):
    """Raised when WGSL compilation or pipeline creation fails."""


class WgpuTextureError(WgpuBackendError):
    """Raised when texture upload/cache operations fail."""


class WgpuRenderError(WgpuBackendError):
    """Raised when a render pass cannot complete."""


@dataclass(slots=True)
class WgpuDeviceBundle:
    """Runtime handles owned by :class:`WgpuDeviceManager`."""

    wgpu: Any
    adapter: Any
    device: Any
    queue: Any
    adapter_info: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class WgpuTextureHandle:
    """Backend texture cache entry keyed by layer id and texture version."""

    texture: Any
    view: Any
    width: int
    height: int
    layer_id: str | None = None
    texture_version: int = -1
    offset_x: int = 0
    offset_y: int = 0


@dataclass(frozen=True, slots=True)
class WgpuRenderTargetHandle:
    """Offscreen render target texture."""

    texture: Any
    view: Any
    width: int
    height: int
    format: str = "rgba8unorm"


@dataclass(frozen=True, slots=True)
class WgpuViewParams:
    """View-resolution compositing parameters (display path only).

    ``affine`` maps canvas pixels to target (surface physical) pixels as
    ``(a, b, c, d, tx, ty)`` with ``x' = a*x + b*y + tx``.  Export, offscreen
    readback, and bitmap fallbacks never set this; they keep compositing at
    canvas resolution.
    """

    affine: tuple[float, float, float, float, float, float]
    width: int
    height: int

    def translated_source(self, dx: float, dy: float) -> WgpuViewParams:
        """Same view, with the source shifted ``(dx, dy)`` canvas pixels.

        The spread viewport draws a second page beside the active one
        (CT-06 §6.3.1).  Both share the viewport's zoom and rotation, so the
        facing page differs only by where its canvas origin sits -- the
        offset runs through the linear part so it rotates and scales with
        the view instead of sliding in screen pixels.
        """
        a, b, c, d, tx, ty = self.affine
        return WgpuViewParams(
            affine=(a, b, c, d, tx + a * dx + b * dy, ty + c * dx + d * dy),
            width=self.width,
            height=self.height,
        )

    def scaled_target(self, factor: float) -> WgpuViewParams:
        """Same view, composited into a smaller target (CT-06 B4-c5).

        Both the affine and the target size shrink by ``factor``, so the
        image occupies the same *normalized* region of a lower-resolution
        target.  That is what lets the blit keep sampling by on-screen
        position (``identity_uv``) while the facing page of a spread is
        composited at reduced quality.

        Values at or above 1.0, and degenerate ones, return the view
        unchanged: quality settings may only cost less, never more.
        """
        if not (0.0 < factor < 1.0):
            return self
        a, b, c, d, tx, ty = self.affine
        return WgpuViewParams(
            affine=(a * factor, b * factor, c * factor, d * factor, tx * factor, ty * factor),
            width=max(1, int(self.width * factor)),
            height=max(1, int(self.height * factor)),
        )

    def inverse(self) -> tuple[float, float, float, float, float, float] | None:
        a, b, c, d, tx, ty = self.affine
        det = a * d - b * c
        if abs(det) <= 1e-12:
            return None
        inv_a = d / det
        inv_b = -b / det
        inv_c = -c / det
        inv_d = a / det
        return (
            inv_a,
            inv_b,
            inv_c,
            inv_d,
            -(inv_a * tx + inv_b * ty),
            -(inv_c * tx + inv_d * ty),
        )

    def scale(self) -> float:
        a, b, c, d, _tx, _ty = self.affine
        return float(max((a * a + c * c) ** 0.5, (b * b + d * d) ** 0.5))


@dataclass(frozen=True, slots=True)
class WgpuDrawRegion:
    """Clipped destination and source UV region for one layer draw."""

    dest_origin: tuple[float, float]
    dest_size: tuple[float, float]
    uv_origin: tuple[float, float]
    uv_size: tuple[float, float]
    src_size: tuple[float, float] = (1.0, 1.0)
    inverse_affine: tuple[float, float, float, float, float, float] | None = None


@dataclass(frozen=True, slots=True)
class WgpuTextureUploadReport:
    """Observability for full/partial uploads."""

    layer_id: str
    texture_version: int
    uploaded: bool
    partial: bool = False
    fallback_full_upload: bool = False
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class WgpuRenderMetrics:
    """Small render metrics surfaced in tests and diagnostics."""

    layers_seen: int = 0
    layers_drawn: int = 0
    full_uploads: int = 0
    partial_uploads: int = 0
    partial_upload_fallbacks: int = 0
    texture_uploads: tuple[WgpuTextureUploadReport, ...] = ()
    full_frame_fallbacks: int = 0
    render_frame_ms: float = 0.0
    readback_ms: float = 0.0

    @property
    def uploaded_layer_ids(self) -> tuple[str, ...]:
        return tuple(report.layer_id for report in self.texture_uploads if report.uploaded)

    @property
    def full_upload_layer_ids(self) -> tuple[str, ...]:
        return tuple(
            report.layer_id
            for report in self.texture_uploads
            if report.uploaded and not report.partial
        )

    @property
    def partial_upload_layer_ids(self) -> tuple[str, ...]:
        return tuple(report.layer_id for report in self.texture_uploads if report.partial)

    @property
    def partial_upload_fallback_layer_ids(self) -> tuple[str, ...]:
        return tuple(
            report.layer_id for report in self.texture_uploads if report.fallback_full_upload
        )


@dataclass(frozen=True, slots=True)
class WgpuRenderResult:
    """CPU readback result from the wgpu offscreen renderer.

    ``rgba`` is straight-alpha RGBA, ``uint8``. When ``readback_rect`` is
    ``None`` it is shaped ``(height, width, 4)``. When ``readback_rect`` is set,
    it is shaped ``(readback_rect.height, readback_rect.width, 4)`` and should
    be patched into a previous full frame at that rectangle.
    """

    width: int
    height: int
    rgba: np.ndarray
    backend_name: str
    warnings: tuple[str, ...] = ()
    metrics: WgpuRenderMetrics = field(default_factory=WgpuRenderMetrics)
    readback_rect: tuple[int, int, int, int] | None = None
