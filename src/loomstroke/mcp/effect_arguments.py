"""Compatibility module for :mod:`loomstroke.mcp.filter_arguments`."""

import sys

from loomstroke.mcp import filter_arguments as _implementation

sys.modules[__name__] = _implementation
