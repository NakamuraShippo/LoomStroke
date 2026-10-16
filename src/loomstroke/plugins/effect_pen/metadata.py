"""Compatibility module for :mod:`loomstroke.plugins.filter_pen.metadata`."""

import sys

from loomstroke.plugins.filter_pen import metadata as _implementation

sys.modules[__name__] = _implementation
