"""Compatibility import for the filter panel's AI Effect section."""

from __future__ import annotations

import sys

from loomstroke.plugins.filter_panel import ai_effect_section as _implementation

sys.modules[__name__] = _implementation
