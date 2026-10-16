"""Compatibility module for :mod:`loomstroke.core.filter_pen.command`."""

import sys

from loomstroke.core.filter_pen import command as _implementation

sys.modules[__name__] = _implementation
