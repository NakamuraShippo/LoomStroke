"""Compatibility module for :mod:`loomstroke.core.filter_pen.snapshot`."""

import sys

from loomstroke.core.filter_pen import snapshot as _implementation

sys.modules[__name__] = _implementation
