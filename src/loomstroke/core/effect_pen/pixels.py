"""Compatibility module for :mod:`loomstroke.core.filter_pen.pixels`."""

import sys

from loomstroke.core.filter_pen import pixels as _implementation

sys.modules[__name__] = _implementation
