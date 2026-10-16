"""Compatibility module for :mod:`loomstroke.plugins.filter_pen.tip_import`."""

import sys

from loomstroke.plugins.filter_pen import tip_import as _implementation

sys.modules[__name__] = _implementation
