"""Compatibility import for image layer services and AI Effect helpers."""

import sys

from . import canvas_layer_image_service as _implementation

sys.modules[__name__] = _implementation
