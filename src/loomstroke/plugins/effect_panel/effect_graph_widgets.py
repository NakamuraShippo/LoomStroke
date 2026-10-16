"""Compatibility import for filter parameter graph widgets."""

from __future__ import annotations

import sys

from loomstroke.plugins.filter_panel import filter_graph_widgets as _implementation

sys.modules[__name__] = _implementation
