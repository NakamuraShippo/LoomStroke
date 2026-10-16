"""Compatibility module for :mod:`loomstroke.core.filter_pen.worker`."""

import sys

from loomstroke.core.filter_pen import worker as _implementation

sys.modules[__name__] = _implementation
