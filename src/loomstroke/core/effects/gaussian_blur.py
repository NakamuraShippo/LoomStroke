"""Compatibility import for :mod:`loomstroke.core.filters.gaussian_blur`."""

import sys

from ..filters import gaussian_blur as _implementation

# Preserve helpers, private imports, and module monkeypatch behavior.
sys.modules[__name__] = _implementation
