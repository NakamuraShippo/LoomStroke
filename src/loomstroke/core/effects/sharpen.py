"""Compatibility import for :mod:`loomstroke.core.filters.sharpen`."""

import sys

from ..filters import sharpen as _implementation

# Preserve helpers, private imports, and module monkeypatch behavior.
sys.modules[__name__] = _implementation
