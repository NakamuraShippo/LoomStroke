"""Compatibility module for :mod:`loomstroke.plugins.filter_pen.tip_library`."""

import sys

from loomstroke.plugins.filter_pen import tip_library as _implementation

sys.modules[__name__] = _implementation
