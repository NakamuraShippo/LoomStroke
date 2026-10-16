"""Compatibility module for :mod:`loomstroke.plugins.filter_pen.stroke_input`."""

import sys

from loomstroke.plugins.filter_pen import stroke_input as _implementation

sys.modules[__name__] = _implementation
