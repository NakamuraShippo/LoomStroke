"""Compatibility module for :mod:`loomstroke.core.filters.vector_raster_bounds`."""

import sys

from loomstroke.core.filters import vector_raster_bounds as _implementation

sys.modules[__name__] = _implementation
