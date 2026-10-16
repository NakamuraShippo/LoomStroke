"""Shared lazy loaders for heavy optional runtimes (numpy / Pillow / cv2).

Importing this module must stay free of heavy imports (Tier 0 contract).
Each loader imports its target only when called and lets ImportError
propagate; callers convert it to their domain-specific error types.
"""

from __future__ import annotations

from typing import Any


def load_numpy() -> Any:
    import numpy as np  # noqa: PLC0415

    return np


def load_pillow_image() -> Any:
    from PIL import Image  # noqa: PLC0415

    return Image


def load_cv2() -> Any:
    import cv2  # noqa: PLC0415

    return cv2
