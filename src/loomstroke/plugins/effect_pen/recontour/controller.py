"""Compatibility module for :mod:`loomstroke.plugins.filter_pen.recontour.controller`."""

import sys

from loomstroke.plugins.filter_pen.recontour import controller as _implementation

sys.modules[__name__] = _implementation
