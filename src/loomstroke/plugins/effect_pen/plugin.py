"""Compatibility module for :mod:`loomstroke.plugins.filter_pen.plugin`."""

import sys

from loomstroke.plugins.filter_pen import plugin as _implementation

sys.modules[__name__] = _implementation
