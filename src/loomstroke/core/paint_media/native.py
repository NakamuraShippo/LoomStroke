"""Select the optional PyO3 kernels or the pixel-equivalent Python implementation."""

from __future__ import annotations

import logging
from functools import cache
from importlib import import_module
from types import ModuleType

from .native_oil import RustOilBrush
from .native_watercolor import RustWatercolorBrush
from .stroke import MediaStroke

_EXTENSION = "_loomstroke_paint"
# Raise this together with KERNEL_VERSION in native/paint_media/src/lib.rs whenever a kernel's
# signature or arithmetic changes, so updated sources never drive an older binary.
KERNEL_VERSION = 2
_LOGGER = logging.getLogger(__name__)


@cache
def _available_module() -> ModuleType | None:
    """Return the packaged kernels, or None so that strokes use the Python implementation."""
    qualified_name = f"{__package__}.{_EXTENSION}"
    try:
        module = import_module(qualified_name)
    except (ImportError, OSError) as exc:
        if isinstance(exc, ModuleNotFoundError) and exc.name == qualified_name:
            _LOGGER.info("No paint extension for this Python; using the Python implementation")
        else:
            _LOGGER.warning(
                "Paint extension failed to load; using the Python implementation: %s", exc
            )
        return None
    found = getattr(module, "KERNEL_VERSION", None)
    if found != KERNEL_VERSION:
        _LOGGER.warning(
            "Paint extension has kernel version %s but %s is required; "
            "using the Python implementation",
            found,
            KERNEL_VERSION,
        )
        return None
    return module


def native_module() -> ModuleType:
    """Require native kernels when running explicit native tests or comparisons."""
    module = _available_module()
    if module is None:
        raise ImportError(
            "Build the paint extension with the project Python: scripts/build_paint_extension.py"
        )
    return module


class RustMediaStroke(MediaStroke):
    """Use native pixel kernels with the same stroke geometry, tiles and pigment state."""

    def _create_brush(self, medium):
        if medium == "knead":
            return super()._create_brush(medium)  # Plain array maths; no kernel.
        brush_type = RustWatercolorBrush if medium == "water" else RustOilBrush
        return brush_type(self, native_module())


def create_media_stroke(
    layer,
    medium,
    diameter,
    *,
    color_bgr=None,
    opacity=1.0,
    paper="Standard",
    water_amount=0.5,
    paint_load=0.5,
    tip=None,
    wear=None,
) -> MediaStroke:
    stroke_type = MediaStroke if _available_module() is None else RustMediaStroke
    return stroke_type(
        layer,
        medium,
        diameter,
        color_bgr=color_bgr,
        opacity=opacity,
        paper=paper,
        water_amount=water_amount,
        paint_load=paint_load,
        tip=tip,
        wear=wear,
    )
