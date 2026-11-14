"""Prepare page-aligned raster pixels for nonuniform Image Size changes."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PIL import Image
from PySide6.QtGui import QImage

from loomstroke.core.command_image_utils import _affine_bounds
from loomstroke.core.geometry import Affine2D
from loomstroke.core.native_cmyk.geometry import NativeCmykGeometry
from loomstroke.core.native_cmyk.pixels import NativeCmykPixels

from .image_size_planner import ImageSizePlanner
from .interpolation import InterpolationMode

if TYPE_CHECKING:
    from loomstroke.core.native_cmyk.buffer import NativeCmykBuffer

    from .raster_resample_service import RasterResampleService


@dataclass(frozen=True, slots=True)
class ResampledRotatedRaster:
    image: QImage
    native_cmyk: NativeCmykBuffer | None
    x: int
    y: int


def prepare_rotated_rasters(
    layers: list[Any],
    sx: float,
    sy: float,
    mode: InterpolationMode,
    raster: RasterResampleService,
) -> dict[str, ResampledRotatedRaster]:
    """Build the replacement buffers before any layer or history is changed."""
    if math.isclose(sx, sy, rel_tol=1e-12, abs_tol=0):
        return {}
    prepared = {}
    for layer in layers:
        image = getattr(layer, "image", None)
        transform = getattr(layer, "transform", None)
        if (
            getattr(layer, "layer_type", "raster") != "raster"
            or image is None
            or image.isNull()
            or transform is None
            or abs(math.sin(transform.rotation)) <= 1e-8
        ):
            continue
        if getattr(layer, "native_cmyk", None) is None and image.hasAlphaChannel():
            alpha = image.convertToFormat(QImage.Format.Format_Alpha8)
            if alpha.isNull():
                raise MemoryError("cannot inspect alpha on the rotated raster")
            if not raster._packed_array(alpha, 1).any():
                # A blank layer still has an editing basis worth keeping.
                continue
        prepared[layer.id] = _resample_rotated_raster(layer, image, sx, sy, mode, raster)
    return prepared


def _resample_rotated_raster(
    layer: Any,
    image: QImage,
    sx: float,
    sy: float,
    mode: InterpolationMode,
    raster: RasterResampleService,
) -> ResampledRotatedRaster:
    original = layer.transform.to_affine()
    placement = Affine2D(
        sx * original.a,
        sx * original.b,
        sy * original.c,
        sy * original.d,
        sx * original.tx,
        sy * original.ty,
    )
    # Keep the full footprint, including any pixels outside the canvas.
    bounds = _affine_bounds(placement, image.width(), image.height())
    inverse = placement.inverse()
    if bounds is None or inverse is None:
        raise ValueError("rotated layer needs a finite, invertible placement")
    x, y, width, height = bounds
    _validate_buffer_size(width, height)
    native = getattr(layer, "native_cmyk", None)
    if native is not None:
        if native.alpha.shape != (image.height(), image.width()):
            raise ValueError("native ink and RGB preview have different dimensions")
        if width * height * 5 * native.inks.dtype.itemsize > NativeCmykPixels.MAX_DECODED_BYTES:
            raise ValueError("rotated CMYK layer would exceed the decoded layer budget")
    input_x, input_y = inverse.apply(x, y)

    filter_mode = raster._filter(mode)
    prefilter_size = None
    warp_filter = filter_mode
    fx = fy = 1.0
    if mode is not InterpolationMode.NEAREST:
        scale_along_x = math.hypot(placement.a, placement.c) / math.hypot(original.a, original.c)
        scale_along_y = math.hypot(placement.b, placement.d) / math.hypot(original.b, original.d)
        resize_only = mode in (
            InterpolationMode.LANCZOS,
            InterpolationMode.HAMMING,
            InterpolationMode.BOX,
        )
        if resize_only:
            warp_filter = Image.Resampling.BICUBIC
        else:
            # Affine sampling alone does not low-pass filter shrinking axes.
            scale_along_x = min(1.0, scale_along_x)
            scale_along_y = min(1.0, scale_along_y)
        local_size = (
            max(1, ImageSizePlanner.round_half_up(image.width() * scale_along_x)),
            max(1, ImageSizePlanner.round_half_up(image.height() * scale_along_y)),
        )
        if local_size != (image.width(), image.height()):
            prefilter_size = local_size
            _validate_buffer_size(*local_size)
            fx = local_size[0] / image.width()
            fy = local_size[1] / image.height()
    coefficients = (
        fx * inverse.a,
        fx * inverse.b,
        fx * input_x,
        fy * inverse.c,
        fy * inverse.d,
        fy * input_y,
    )

    def sample(source: Image.Image) -> Image.Image:
        if prefilter_size is not None:
            source = source.resize(prefilter_size, filter_mode)
        return source.transform((width, height), Image.Transform.AFFINE, coefficients, warp_filter)

    output = raster.sample_premultiplied(image, sample)
    if output.isNull():
        raise MemoryError("cannot allocate the rotated raster")
    if native is not None:
        native = NativeCmykGeometry.sample_buffer(
            native, sample, nearest=mode is InterpolationMode.NEAREST
        )
    return ResampledRotatedRaster(output, native, x, y)


def _validate_buffer_size(width: int, height: int) -> None:
    # The archive reader's raster limit is 8192 squared. Rotation can
    # increase the bounding box even for a much smaller output canvas.
    if max(width, height) > ImageSizePlanner.MAX_DIMENSION or width * height > 8192**2:
        raise ValueError("rotated layer would exceed the supported raster size")
