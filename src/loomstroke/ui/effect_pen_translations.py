"""Compatibility module for :mod:`loomstroke.ui.filter_pen_translations`."""

import sys

from loomstroke.ui import filter_pen_translations as _implementation

sys.modules[__name__] = _implementation
