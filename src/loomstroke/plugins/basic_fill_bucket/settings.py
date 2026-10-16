"""
Settings helpers for the basic fill bucket tool.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from ...plugins.sdk.tool import ToolSettings

logger = logging.getLogger(__name__)

SETTINGS_SECTION = "tools/basic_fill_bucket"
LEGACY_SECTION = "tools.basic_fill_bucket"

FILL_TARGET_ACTIVE = "active"
FILL_TARGET_NEW_LAYER = "new_layer"

FILL_TARGET_LABELS = {
    FILL_TARGET_ACTIVE: "Active Layer",
    FILL_TARGET_NEW_LAYER: "New Layer",
}

FILL_TARGET_VALUES = {value: key for key, value in FILL_TARGET_LABELS.items()}


@dataclass
class FillBucketSettings(ToolSettings):
    color_tolerance: int = 8
    gap_close_px: int = 0
    fill_target: str = FILL_TARGET_ACTIVE
    reference_mode: str = "Active Layer"
    area_expansion: int = 0
    fill_mode: str = "Bucket"
    gap_fill_size: int = 32


def load_settings(settings_service: Any) -> FillBucketSettings:
    data: dict[str, Any] = {}
    section_getter = getattr(settings_service, "get_section", None)
    if callable(section_getter):
        try:
            section = section_getter(SETTINGS_SECTION)
            if isinstance(section, dict):
                data.update(section)
        except Exception:
            logger.debug("settings section read failed", exc_info=True)

    legacy_getter = getattr(settings_service, "get", None)
    if callable(legacy_getter):
        try:
            legacy = legacy_getter(LEGACY_SECTION, None)
            if isinstance(legacy, dict):
                for key in ("color_tolerance", "gap_close_px", "fill_target"):
                    if key not in data and key in legacy:
                        data[key] = legacy[key]
        except Exception:
            logger.debug("legacy settings read failed", exc_info=True)

    return FillBucketSettings(
        color_tolerance=_clamp_int(data.get("color_tolerance", 8), 0, 255),
        gap_close_px=_clamp_int(data.get("gap_close_px", 0), 0, 10),
        fill_target=_normalize_fill_target(data.get("fill_target", FILL_TARGET_ACTIVE)),
        reference_mode=(
            data.get("reference_mode", "Active Layer")
            if data.get("reference_mode") in {"Active Layer", "Visible Layers"}
            else "Active Layer"
        ),
        area_expansion=_clamp_int(data.get("area_expansion", 0), -16, 16),
        fill_mode=(
            data.get("fill_mode", "Bucket")
            if data.get("fill_mode") in {"Bucket", "Enclose and Fill", "Gap Fill"}
            else "Bucket"
        ),
        gap_fill_size=_clamp_int(data.get("gap_fill_size", 32), 1, 256),
    )


def save_settings(settings_service: Any, settings: FillBucketSettings) -> None:
    setter = getattr(settings_service, "set", None)
    if not callable(setter):
        return
    setter(f"{SETTINGS_SECTION}/color_tolerance", int(settings.color_tolerance))
    setter(f"{SETTINGS_SECTION}/gap_close_px", int(settings.gap_close_px))
    setter(f"{SETTINGS_SECTION}/fill_target", settings.fill_target)
    for key in ("reference_mode", "area_expansion", "fill_mode", "gap_fill_size"):
        setter(f"{SETTINGS_SECTION}/{key}", getattr(settings, key))


def to_display_fill_target(value: str) -> str:
    normalized = _normalize_fill_target(value)
    return FILL_TARGET_LABELS.get(normalized, FILL_TARGET_LABELS[FILL_TARGET_ACTIVE])


def normalize_fill_target(value: Any) -> str:
    return _normalize_fill_target(value)


def _normalize_fill_target(value: Any) -> str:
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"active", "active_layer"}:
            return FILL_TARGET_ACTIVE
        if lowered in {"new", "new_layer", "new layer"}:
            return FILL_TARGET_NEW_LAYER
        mapped = FILL_TARGET_VALUES.get(value)
        if mapped:
            return mapped
    return FILL_TARGET_ACTIVE


def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        return minimum
    return max(minimum, min(maximum, numeric))


__all__ = [
    "FILL_TARGET_ACTIVE",
    "FILL_TARGET_LABELS",
    "FILL_TARGET_NEW_LAYER",
    "FillBucketSettings",
    "load_settings",
    "normalize_fill_target",
    "save_settings",
    "to_display_fill_target",
]
