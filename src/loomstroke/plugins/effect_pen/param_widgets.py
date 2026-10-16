"""Compatibility module for :mod:`loomstroke.plugins.filter_pen.param_widgets`."""

import sys

from loomstroke.plugins.filter_pen import param_widgets as _implementation

sys.modules[__name__] = _implementation
