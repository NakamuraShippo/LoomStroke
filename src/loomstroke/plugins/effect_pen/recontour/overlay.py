"""Compatibility module for :mod:`loomstroke.plugins.filter_pen.recontour.overlay`."""

import sys

from loomstroke.plugins.filter_pen.recontour import overlay as _implementation

sys.modules[__name__] = _implementation
