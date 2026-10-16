"""Compatibility module for :mod:`loomstroke.plugins.filter_pen.panel_position`."""

import sys

from loomstroke.plugins.filter_pen import panel_position as _implementation

sys.modules[__name__] = _implementation
