"""Compatibility module for :mod:`loomstroke.core.filter_pen.coverage`."""

import sys

from loomstroke.core.filter_pen import coverage as _implementation

sys.modules[__name__] = _implementation
