"""Compatibility import for :mod:`loomstroke.core.filters.gradient_map`."""

import sys

from ..filters import gradient_map as _implementation

# Preserve helpers, private imports, and module monkeypatch behavior.
sys.modules[__name__] = _implementation
