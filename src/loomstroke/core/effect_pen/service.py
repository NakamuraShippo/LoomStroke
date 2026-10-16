"""Compatibility module for :mod:`loomstroke.core.filter_pen.service`."""

import sys

from loomstroke.core.filter_pen import service as _implementation

sys.modules[__name__] = _implementation
