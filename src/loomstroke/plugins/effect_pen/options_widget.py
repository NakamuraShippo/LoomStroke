"""Compatibility module for :mod:`loomstroke.plugins.filter_pen.options_widget`."""

import sys

from loomstroke.plugins.filter_pen import options_widget as _implementation

sys.modules[__name__] = _implementation
