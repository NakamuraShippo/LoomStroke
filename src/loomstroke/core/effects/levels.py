"""Compatibility import for :mod:`loomstroke.core.filters.levels`."""

import sys

from ..filters import levels as _implementation

# Preserve helpers, private imports, and module monkeypatch behavior.
sys.modules[__name__] = _implementation
