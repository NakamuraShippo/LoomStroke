"""Layer rectangle and UV helpers for the wgpu compositor."""

from __future__ import annotations

import math
from typing import Any

from .types import WgpuDrawRegion


class WgpuTransformProcessor:
    """Compute simple 2D draw regions.

    Initial support is intentionally narrow: layer offset plus clipping to the
    canvas bounds. Non-identity transforms are reported as unsupported by the
    compositor before this helper is called.
    """

    def compute_draw_region(
        self,
        layer: Any,
        *,
        canvas_width: int,
        canvas_height: int,
        texture_width: int,
        texture_height: int,
        extra_offset: tuple[int, int] = (0, 0),
    ) -> WgpuDrawRegion | None:
        """Compute the blend-pass draw region for a layer texture.

        ``extra_offset`` shifts the texture in canvas space on top of the
        layer offset — used by prescaled tight-region vector rasters whose
        texture origin is the content-bounds corner (view compositing C2).
        """

        if canvas_width <= 0 or canvas_height <= 0:
            return None
        if texture_width <= 0 or texture_height <= 0:
            return None

        explicit = self._explicit_affine(layer)
        if explicit is not None:
            if extra_offset != (0, 0):
                # Fold the texture-origin shift into the affine translation.
                a, b, c, d, tx, ty = explicit
                ox, oy = float(extra_offset[0]), float(extra_offset[1])
                explicit = (a, b, c, d, tx + a * ox + b * oy, ty + c * ox + d * oy)
            return self._compute_affine_region(
                explicit,
                canvas_width=canvas_width,
                canvas_height=canvas_height,
                texture_width=texture_width,
                texture_height=texture_height,
            )

        offset_x = int(getattr(layer, "offset_x", 0)) + int(extra_offset[0])
        offset_y = int(getattr(layer, "offset_y", 0)) + int(extra_offset[1])
        src_x0 = max(0, -offset_x)
        src_y0 = max(0, -offset_y)
        dst_x0 = max(0, offset_x)
        dst_y0 = max(0, offset_y)
        dst_x1 = min(canvas_width, offset_x + texture_width)
        dst_y1 = min(canvas_height, offset_y + texture_height)
        width = dst_x1 - dst_x0
        height = dst_y1 - dst_y0
        if width <= 0 or height <= 0:
            return None

        return WgpuDrawRegion(
            dest_origin=(float(dst_x0), float(dst_y0)),
            dest_size=(float(width), float(height)),
            uv_origin=(float(src_x0) / texture_width, float(src_y0) / texture_height),
            uv_size=(float(width) / texture_width, float(height) / texture_height),
            src_size=(float(texture_width), float(texture_height)),
        )

    def _compute_affine_region(
        self,
        affine: tuple[float, float, float, float, float, float],
        *,
        canvas_width: int,
        canvas_height: int,
        texture_width: int,
        texture_height: int,
    ) -> WgpuDrawRegion | None:
        inverse = self._inverse_affine(affine)
        if inverse is None:
            return None
        a, b, c, d, tx, ty = affine
        corners = (
            (tx, ty),
            (a * texture_width + tx, c * texture_width + ty),
            (b * texture_height + tx, d * texture_height + ty),
            (
                a * texture_width + b * texture_height + tx,
                c * texture_width + d * texture_height + ty,
            ),
        )
        min_x = math.floor(min(x for x, _y in corners))
        max_x = math.ceil(max(x for x, _y in corners))
        min_y = math.floor(min(y for _x, y in corners))
        max_y = math.ceil(max(y for _x, y in corners))
        dst_x0 = max(0, min_x)
        dst_y0 = max(0, min_y)
        dst_x1 = min(canvas_width, max_x)
        dst_y1 = min(canvas_height, max_y)
        width = dst_x1 - dst_x0
        height = dst_y1 - dst_y0
        if width <= 0 or height <= 0:
            return None
        return WgpuDrawRegion(
            dest_origin=(float(dst_x0), float(dst_y0)),
            dest_size=(float(width), float(height)),
            uv_origin=(0.0, 0.0),
            uv_size=(1.0, 1.0),
            src_size=(float(texture_width), float(texture_height)),
            inverse_affine=inverse,
        )

    @staticmethod
    def _explicit_affine(layer: Any) -> tuple[float, float, float, float, float, float] | None:
        transform = getattr(layer, "_preview_transform", None)
        if transform is None:
            transform = getattr(layer, "transform", None)
        if transform is None:
            return None
        to_affine = getattr(transform, "to_affine", None)
        if callable(to_affine):
            affine = to_affine()
            values = (
                float(getattr(affine, "a", 1.0)),
                float(getattr(affine, "b", 0.0)),
                float(getattr(affine, "c", 0.0)),
                float(getattr(affine, "d", 1.0)),
                float(getattr(affine, "tx", 0.0)),
                float(getattr(affine, "ty", 0.0)),
            )
        else:
            values = (
                float(getattr(transform, "scale_x", 1.0)),
                0.0,
                0.0,
                float(getattr(transform, "scale_y", 1.0)),
                float(getattr(transform, "tx", 0.0)),
                float(getattr(transform, "ty", 0.0)),
            )
        if not all(math.isfinite(value) for value in values):
            return None
        if WgpuTransformProcessor._is_identity_affine(values):
            return None
        return values

    @staticmethod
    def _inverse_affine(
        values: tuple[float, float, float, float, float, float],
    ) -> tuple[float, float, float, float, float, float] | None:
        a, b, c, d, tx, ty = values
        det = a * d - b * c
        if abs(det) <= 1e-10 or not math.isfinite(det):
            return None
        inv_a = d / det
        inv_b = -b / det
        inv_c = -c / det
        inv_d = a / det
        inv_tx = -(inv_a * tx + inv_b * ty)
        inv_ty = -(inv_c * tx + inv_d * ty)
        return (inv_a, inv_b, inv_c, inv_d, inv_tx, inv_ty)

    @staticmethod
    def _is_identity_affine(values: tuple[float, float, float, float, float, float]) -> bool:
        a, b, c, d, tx, ty = values
        return (
            abs(a - 1.0) <= 1e-8
            and abs(b) <= 1e-8
            and abs(c) <= 1e-8
            and abs(d - 1.0) <= 1e-8
            and abs(tx) <= 1e-8
            and abs(ty) <= 1e-8
        )
