"""Compatibility import for :mod:`loomstroke.core.filters.brightness_contrast`."""

import sys

from ..filters import brightness_contrast as _implementation

# Preserve helpers, private imports, and module monkeypatch behavior.
sys.modules[__name__] = _implementation
