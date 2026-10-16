"""Compatibility module for :mod:`loomstroke.plugins.filter_pen.translations`."""

import sys

from loomstroke.plugins.filter_pen import translations as _implementation

sys.modules[__name__] = _implementation
