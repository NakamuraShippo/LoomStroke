"""Backward-compatible module alias for the canonical filter implementation."""

import sys

from . import filter_prefix_renderer as _canonical

sys.modules[__name__] = _canonical
