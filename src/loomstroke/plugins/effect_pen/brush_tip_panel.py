"""Compatibility module for :mod:`loomstroke.plugins.filter_pen.brush_tip_panel`."""

import sys

from loomstroke.plugins.filter_pen import brush_tip_panel as _implementation

sys.modules[__name__] = _implementation
