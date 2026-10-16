"""Backward-compatible module alias for the canonical filter processor."""

import sys

from . import filter_processor as _canonical

# Preserve private imports and module-global monkeypatches used by plugins.
sys.modules[__name__] = _canonical
