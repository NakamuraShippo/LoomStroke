"""Compatibility import for :mod:`loomstroke.core.filters.mosaic`."""

import sys

from ..filters import mosaic as _implementation

# Preserve helpers, private imports, and module monkeypatch behavior.
sys.modules[__name__] = _implementation
