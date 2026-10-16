"""Compatibility module for :mod:`loomstroke.mcp.filter_tools`."""

import sys

from loomstroke.mcp import filter_tools as _implementation

sys.modules[__name__] = _implementation
