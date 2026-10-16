"""Compatibility import for :mod:`loomstroke.core.filters.screentone`."""

import sys

from ..filters import screentone as _implementation

# Preserve helpers, private imports, and module monkeypatch behavior.
sys.modules[__name__] = _implementation
