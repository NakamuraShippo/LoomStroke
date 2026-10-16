"""Compatibility import for the canonical filter panel widget."""

from __future__ import annotations

import sys

from loomstroke.plugins.filter_panel import panel as _implementation

# Historical monkeypatches and private helpers share the canonical globals.
sys.modules[__name__] = _implementation
