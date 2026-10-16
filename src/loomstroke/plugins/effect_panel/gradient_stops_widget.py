"""Compatibility import for the filter gradient stops widget."""

from __future__ import annotations

import sys

from loomstroke.plugins.filter_panel import gradient_stops_widget as _implementation

sys.modules[__name__] = _implementation
