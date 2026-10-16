"""Compatibility import for :mod:`loomstroke.core.filters.invert`."""

import sys

from ..filters import invert as _implementation

# Preserve helpers, private imports, and module monkeypatch behavior.
sys.modules[__name__] = _implementation
