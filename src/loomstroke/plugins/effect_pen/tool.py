"""Compatibility module for :mod:`loomstroke.plugins.filter_pen.tool`."""

import sys

from loomstroke.plugins.filter_pen import tool as _implementation

sys.modules[__name__] = _implementation
