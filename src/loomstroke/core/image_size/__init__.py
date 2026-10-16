"""Image Size / resample (CT-13): dimension math, kernels and canvas apply.

One class per module:

- :mod:`interpolation` — stable kernel ids
- :mod:`image_size_target` / :mod:`resample_plan` — request and frozen plan
- :mod:`image_size_planner` — pure dimension / unit / validation math
- :mod:`raster_resample_service` — Pillow kernels for rasters and masks
- :mod:`text_meta_scaler`, :mod:`parametric_meta_scaler`,
  :mod:`vector_meta_scaler_registry`, :mod:`vector_geometry_scale_service`
  — vector / text geometry scaling
- :mod:`resample_snapshot` — undo payload
- :mod:`canvas_resample_service` — the transaction on a canvas service
"""

from .canvas_resample_service import CanvasResampleService
from .image_size_planner import ImageSizePlanner
from .image_size_target import ImageSizeTarget
from .interpolation import InterpolationMode
from .parametric_meta_scaler import ParametricMetaScaler
from .raster_resample_service import RasterResampleService
from .resample_plan import PageResamplePlan
from .resample_snapshot import ResampleSnapshot
from .text_meta_scaler import TextMetaScaler
from .vector_geometry_scale_service import VectorGeometryScaleService
from .vector_meta_scaler_registry import VectorMetaScalerRegistry

__all__ = [
    "CanvasResampleService",
    "ImageSizePlanner",
    "ImageSizeTarget",
    "InterpolationMode",
    "PageResamplePlan",
    "ParametricMetaScaler",
    "RasterResampleService",
    "ResampleSnapshot",
    "TextMetaScaler",
    "VectorGeometryScaleService",
    "VectorMetaScalerRegistry",
]
