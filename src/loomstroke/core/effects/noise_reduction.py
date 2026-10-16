"""Compatibility import for :mod:`loomstroke.core.filters.noise_reduction`."""

import sys

from ..filters import noise_reduction as _implementation

# Preserve helpers, private imports, and module monkeypatch behavior.
sys.modules[__name__] = _implementation
