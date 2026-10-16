"""Compatibility module for :mod:`loomstroke.mcp.filter_mask_command`."""

import sys

from loomstroke.mcp import filter_mask_command as _implementation

sys.modules[__name__] = _implementation
