"""Compatibility module for :mod:`loomstroke.plugins.filter_pen.settings`."""

import sys

from loomstroke.plugins.filter_pen import settings as _implementation

sys.modules[__name__] = _implementation
