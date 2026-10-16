"""Cache path utilities for LoomStroke.

Provides helpers to resolve cache directories and filenames for
layer snapshot exports while complying with Master.md constraints.
"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtGui import QImageWriter

from .user_dirs import default_user_root

LAYER_CACHE_SUBDIR = "layers"
APP_CACHE_SUBDIR = "LoomStroke"

_INVALID_CHARS = re.compile(r"[^A-Za-z0-9_-]+")


def get_cache_root() -> Path:
    """Resolve the writable cache root for LoomStroke.

    Returns:
        Path to the cache directory (guaranteed to exist).
    """
    user_root = default_user_root(create=True)
    cache_root = user_root / "cache" / APP_CACHE_SUBDIR
    cache_root.mkdir(parents=True, exist_ok=True)
    return cache_root


def get_layer_cache_dir() -> Path:
    """Get the cache directory dedicated to cached layer exports."""
    cache_dir = get_cache_root() / LAYER_CACHE_SUBDIR
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def sanitize_stem(name: str | None) -> str:
    """Sanitize a filename stem to ASCII-safe characters.

    Args:
        name: Desired stem value.

    Returns:
        Sanitized stem (never empty).
    """
    if not name:
        return "layer"

    ascii_name = name.encode("ascii", "ignore").decode("ascii")
    ascii_name = ascii_name.replace(os.sep, "_").replace("/", "_")
    sanitized = _INVALID_CHARS.sub("_", ascii_name)
    sanitized = sanitized.strip("._")
    return sanitized or "layer"


def normalize_image_format(fmt: str | None, fallback: str = "png") -> str:
    """Normalize image format to one supported by Qt.

    Args:
        fmt: Requested image format.
        fallback: Fallback format when unsupported.

    Returns:
        Lowercase supported format string.
    """
    requested = (fmt or fallback).lower()
    if requested == "jpeg":
        requested = "jpg"

    supported = {
        bytes(fmt.data()).decode("ascii", "ignore").lower()
        for fmt in QImageWriter.supportedImageFormats()
    }
    fallback_lower = fallback.lower()
    if requested not in supported:
        if fallback_lower in supported:
            return fallback_lower
        return next(iter(supported), fallback_lower)
    return requested


def build_snapshot_path(stem: str | None, fmt: str) -> Path:
    """Build the final snapshot path for a layer export.

    Args:
        stem: Base filename stem.
        fmt: Normalized image format.

    Returns:
        Absolute path for the snapshot file.
    """
    cache_dir = get_layer_cache_dir()
    sanitized = sanitize_stem(stem)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    short_id = uuid4().hex[:6]
    filename = f"{sanitized}_{timestamp}-{short_id}.{fmt.lower()}"
    return cache_dir / filename
