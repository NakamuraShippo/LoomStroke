"""Compatibility import for the canvas filter and AI Effect API mixin."""

import sys

from . import canvas_service_simple_filter_api as _implementation

sys.modules[__name__] = _implementation
