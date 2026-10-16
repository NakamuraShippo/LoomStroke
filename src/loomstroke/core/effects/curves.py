"""Compatibility import for :mod:`loomstroke.core.filters.curves`."""

import sys

from ..filters import curves as _implementation

# Preserve helpers, private imports, and module monkeypatch behavior.
sys.modules[__name__] = _implementation
