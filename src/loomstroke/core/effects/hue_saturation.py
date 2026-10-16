"""Compatibility import for :mod:`loomstroke.core.filters.hue_saturation`."""

import sys

from ..filters import hue_saturation as _implementation

# Preserve helpers, private imports, and module monkeypatch behavior.
sys.modules[__name__] = _implementation
