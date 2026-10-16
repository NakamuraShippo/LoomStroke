"""Preset storage for StartPage."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from uuid import uuid4

from ..platform.user_dirs import default_user_root
from .startpage_models import CanvasPreset, PresetCategory

logger = logging.getLogger(__name__)

_PRESET_FILE_NAME = "canvas_presets.json"
_PRESET_VERSION = 1

_REQUIRED_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("ai", "AI"),
    ("a", "A判"),
    ("b", "B判"),
    ("desktop", "Desktop Screen"),
    ("mobile", "Mobile Screen"),
    ("custom", "Custom"),
)


def preset_file_path() -> Path:
    root = default_user_root(create=True)
    settings_dir = root / "settings"
    settings_dir.mkdir(parents=True, exist_ok=True)
    return settings_dir / _PRESET_FILE_NAME


def default_categories() -> list[PresetCategory]:
    def _preset(label: str, width: int, height: int, *, notes: str | None = None) -> CanvasPreset:
        return CanvasPreset(
            id=f"preset_{uuid4().hex[:8]}",
            label=label,
            width_px=width,
            height_px=height,
            notes=notes,
        )

    ai = PresetCategory(
        id="ai",
        label="AI",
        presets=(
            _preset("[SD1.5] 1:1 512 x 512", 512, 512),
            _preset("[SD1.5] 1:1 768 x 768", 768, 768),
            _preset("[SD1.5] 4:3 512 x 384", 512, 384),
            _preset("[SD1.5] 4:3 640 x 480", 640, 480),
            _preset("[SD1.5] 3:2 608 x 416", 608, 416),
            _preset("[SD1.5] 16:9 768 x 448", 768, 448),
            _preset("[SD1.5] 9:16 448 x 768", 448, 768),
            _preset("[SDXL/etc] 1:1 1024 x 1024", 1024, 1024),
            _preset("[SDXL/etc] 4:3 1152 x 864", 1152, 864),
            _preset("[SDXL/etc] 3:4 864 x 1152", 864, 1152),
            _preset("[SDXL/etc] 3:2 1216 x 832", 1216, 832),
            _preset("[SDXL/etc] 2:3 832 x 1216", 832, 1216),
            _preset("[SDXL/etc] 16:9 1344 x 768", 1344, 768),
            _preset("[SDXL/etc] 9:16 768 x 1344", 768, 1344),
            _preset("[SDXL/etc] 21:9 1536 x 640", 1536, 640),
            _preset("[SDXL/etc] 9:21 640 x 1536", 640, 1536),
        ),
    )

    a = PresetCategory(
        id="a",
        label="A判",
        presets=(
            _preset("A0 2384 x 3370 px", 2384, 3370),
            _preset("A1 1684 x 2384 px", 1684, 2384),
            _preset("A2 1191 x 1684 px", 1191, 1684),
            _preset("A3 842 x 1191 px", 842, 1191),
            _preset("A4 595 x 842 px", 595, 842),
            _preset("A5 420 x 595 px", 420, 595),
            _preset("A6 298 x 420 px", 298, 420),
            _preset("A7 210 x 298 px", 210, 298),
            _preset("A8 147 x 210 px", 147, 210),
        ),
    )

    b = PresetCategory(
        id="b",
        label="B判",
        presets=(
            _preset("B0 2920 x 4127 px", 2920, 4127),
            _preset("B1 2064 x 2920 px", 2064, 2920),
            _preset("B2 1460 x 2064 px", 1460, 2064),
            _preset("B3 1032 x 1460 px", 1032, 1460),
            _preset("B4 729 x 1032 px", 729, 1032),
            _preset("B5 516 x 729 px", 516, 729),
            _preset("B6 363 x 516 px", 363, 516),
            _preset("B7 258 x 363 px", 258, 363),
            _preset("B8 181 x 258 px", 181, 258),
        ),
    )

    desktop = PresetCategory(
        id="desktop",
        label="Desktop Screen",
        presets=(
            _preset("8K 7680 x 4320", 7680, 4320),
            _preset("4K (UHD) 3840 x 2160", 3840, 2160),
            _preset("WQHD 2560 x 1440", 2560, 1440),
            _preset("Full HD 1920 x 1080", 1920, 1080),
            _preset("HD 1280 x 720", 1280, 720),
            _preset("XGA 1024 x 768", 1024, 768),
        ),
    )

    mobile = PresetCategory(
        id="mobile",
        label="Mobile Screen",
        presets=(
            _preset("iPhone 17 Pro Max (2025) 2868 x 1320", 2868, 1320),
            _preset("iPhone 17 / 16 / 15 2556 x 1179", 2556, 1179),
            _preset("Galaxy S26 Ultra (2026) 3120 x 1440", 3120, 1440),
            _preset("Galaxy S26 / S25 2340 x 1080", 2340, 1080),
            _preset("Google Pixel 10 Pro XL 2992 x 1344", 2992, 1344),
            _preset("Google Pixel 10 Pro 2856 x 1280", 2856, 1280),
            _preset("Google Pixel 9a / 8a 2424 x 1080", 2424, 1080),
            _preset("Xperia 1 VI (2024) 2340 x 1080", 2340, 1080),
            _preset("iPad Pro 13インチ (M4) 2752 x 2064", 2752, 2064),
            _preset("iPad Pro 11インチ (M4) 2420 x 1668", 2420, 1668),
            _preset("iPad Air 13インチ (M2) 2732 x 2048", 2732, 2048),
            _preset("iPad mini (A17 Pro) 2266 x 1488", 2266, 1488),
            _preset("iPad (第10世代) 2360 x 1640", 2360, 1640),
            _preset("Galaxy Tab S10 Ultra 2960 x 1848", 2960, 1848),
            _preset("OnePlus Pad 3 3000 x 2120", 3000, 2120),
            _preset("Google Pixel Tablet 2560 x 1600", 2560, 1600),
        ),
    )

    custom = PresetCategory(id="custom", label="Custom", presets=())

    return [ai, a, b, desktop, mobile, custom]


def _category_id_from_label(label: str) -> str | None:
    for category_id, category_label in _REQUIRED_CATEGORIES:
        if label == category_label:
            return category_id
    return None


def _normalize_categories(categories: Iterable[PresetCategory]) -> list[PresetCategory]:
    by_id = {category.id: category for category in categories}
    normalized: list[PresetCategory] = []
    for category_id, category_label in _REQUIRED_CATEGORIES:
        category = by_id.get(category_id)
        if category is None:
            normalized.append(PresetCategory(id=category_id, label=category_label, presets=()))
        else:
            normalized.append(
                PresetCategory(
                    id=category_id,
                    label=category_label,
                    presets=tuple(category.presets),
                )
            )
    return normalized


def load_presets() -> list[PresetCategory]:
    path = preset_file_path()
    if not path.exists():
        defaults = default_categories()
        save_presets(defaults)
        return defaults

    try:
        raw = path.read_text(encoding="utf-8")
        payload = json.loads(raw) if raw.strip() else {}
    except Exception as exc:
        logger.warning("Failed to read presets file %s: %s", path, exc)
        defaults = default_categories()
        save_presets(defaults)
        return defaults

    categories_payload = payload.get("categories") if isinstance(payload, Mapping) else None
    categories: list[PresetCategory] = []
    if isinstance(categories_payload, list):
        for entry in categories_payload:
            if not isinstance(entry, Mapping):
                continue
            category_id = str(entry.get("id", "")).strip()
            category_label = str(entry.get("label", "")).strip()
            if not category_id and category_label:
                category_id = _category_id_from_label(category_label) or ""
            if not category_id:
                continue
            category = PresetCategory.from_payload(entry)
            categories.append(category)

    if not categories:
        categories = default_categories()
        save_presets(categories)
        return categories

    return _normalize_categories(categories)


def save_presets(categories: Iterable[PresetCategory]) -> None:
    path = preset_file_path()
    normalized = _normalize_categories(categories)
    payload = {
        "version": _PRESET_VERSION,
        "categories": [category.to_payload() for category in normalized],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


__all__ = [
    "default_categories",
    "load_presets",
    "preset_file_path",
    "save_presets",
]
