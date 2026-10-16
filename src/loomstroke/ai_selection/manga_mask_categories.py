"""Manga and illustration mask category registry for SAM3 workflows.

This module is intentionally dependency-free. It provides stable internal values
that UI, package export, and assistant flows can share without importing Qt or a
worker runtime.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias

SAM3_MANGA_MASK_CATEGORY_SCHEMA_VERSION = "sam3.manga_mask_category.v1"

MangaMaskCategory: TypeAlias = Literal[
    "comic_panel",
    "speech_bubble",
    "caption_box",
    "character",
    "face",
    "eyes",
    "hands",
    "hair",
    "clothes",
    "weapon",
    "prop",
    "background",
    "sound_effect_text",
    "speed_lines",
    "screen_tone_region",
]
MangaMaskCategoryGroup: TypeAlias = Literal[
    "panel",
    "text",
    "subject",
    "object",
    "background",
    "effect",
]
MangaMaskPreset: TypeAlias = Literal[
    "all",
    "speech_bubble_protection",
    "character_extraction",
    "background_extraction",
    "inpaint_prep",
]


@dataclass(frozen=True, slots=True)
class Sam3MangaMaskCategoryDefinition:
    value: MangaMaskCategory
    group: MangaMaskCategoryGroup
    label_key: str
    default_label_en: str
    default_label_ja: str
    prompt_aliases: tuple[str, ...]
    is_text_protection_target: bool = False
    requires_user_confirmation: bool = True
    schema_version: str = SAM3_MANGA_MASK_CATEGORY_SCHEMA_VERSION

    def label(self, locale: str = "en") -> str:
        normalized_locale = str(locale or "en").lower().replace("_", "-")
        if normalized_locale.startswith("ja"):
            return self.default_label_ja
        return self.default_label_en

    def to_public_dict(self, *, locale: str = "en") -> dict[str, object]:
        return {
            "schemaVersion": self.schema_version,
            "value": self.value,
            "group": self.group,
            "labelKey": self.label_key,
            "label": self.label(locale),
            "promptAliases": list(self.prompt_aliases),
            "isTextProtectionTarget": self.is_text_protection_target,
            "requiresUserConfirmation": self.requires_user_confirmation,
        }


_DEFINITIONS: tuple[Sam3MangaMaskCategoryDefinition, ...] = (
    Sam3MangaMaskCategoryDefinition(
        value="comic_panel",
        group="panel",
        label_key="sam3.manga_mask_category.comic_panel",
        default_label_en="Comic panel",
        default_label_ja="コマ",
        prompt_aliases=("panel", "comic panel", "frame", "コマ枠", "漫画コマ"),
    ),
    Sam3MangaMaskCategoryDefinition(
        value="speech_bubble",
        group="text",
        label_key="sam3.manga_mask_category.speech_bubble",
        default_label_en="Speech bubble",
        default_label_ja="吹き出し",
        prompt_aliases=(
            "speech balloon",
            "bubble",
            "balloon",
            "ふきだし",
            "フキダシ",
            "セリフ枠",
        ),
        is_text_protection_target=True,
    ),
    Sam3MangaMaskCategoryDefinition(
        value="caption_box",
        group="text",
        label_key="sam3.manga_mask_category.caption_box",
        default_label_en="Caption box",
        default_label_ja="キャプション枠",
        prompt_aliases=(
            "caption",
            "narration box",
            "narration",
            "キャプション",
            "ナレーション枠",
        ),
        is_text_protection_target=True,
    ),
    Sam3MangaMaskCategoryDefinition(
        value="character",
        group="subject",
        label_key="sam3.manga_mask_category.character",
        default_label_en="Character",
        default_label_ja="人物",
        prompt_aliases=("person", "figure", "キャラクター", "人"),
    ),
    Sam3MangaMaskCategoryDefinition(
        value="face",
        group="subject",
        label_key="sam3.manga_mask_category.face",
        default_label_en="Face",
        default_label_ja="顔",
        prompt_aliases=("face area", "フェイス"),
    ),
    Sam3MangaMaskCategoryDefinition(
        value="eyes",
        group="subject",
        label_key="sam3.manga_mask_category.eyes",
        default_label_en="Eyes",
        default_label_ja="目",
        prompt_aliases=("eye", "眼", "瞳"),
    ),
    Sam3MangaMaskCategoryDefinition(
        value="hands",
        group="subject",
        label_key="sam3.manga_mask_category.hands",
        default_label_en="Hands",
        default_label_ja="手",
        prompt_aliases=("hand", "手元"),
    ),
    Sam3MangaMaskCategoryDefinition(
        value="hair",
        group="subject",
        label_key="sam3.manga_mask_category.hair",
        default_label_en="Hair",
        default_label_ja="髪",
        prompt_aliases=("hair area", "髪の毛"),
    ),
    Sam3MangaMaskCategoryDefinition(
        value="clothes",
        group="subject",
        label_key="sam3.manga_mask_category.clothes",
        default_label_en="Clothes",
        default_label_ja="服",
        prompt_aliases=("clothing", "costume", "衣装"),
    ),
    Sam3MangaMaskCategoryDefinition(
        value="weapon",
        group="object",
        label_key="sam3.manga_mask_category.weapon",
        default_label_en="Weapon",
        default_label_ja="武器",
        prompt_aliases=("weapons",),
    ),
    Sam3MangaMaskCategoryDefinition(
        value="prop",
        group="object",
        label_key="sam3.manga_mask_category.prop",
        default_label_en="Prop",
        default_label_ja="小物",
        prompt_aliases=("object", "item", "物体", "道具"),
    ),
    Sam3MangaMaskCategoryDefinition(
        value="background",
        group="background",
        label_key="sam3.manga_mask_category.background",
        default_label_en="Background",
        default_label_ja="背景",
        prompt_aliases=("bg",),
    ),
    Sam3MangaMaskCategoryDefinition(
        value="sound_effect_text",
        group="text",
        label_key="sam3.manga_mask_category.sound_effect_text",
        default_label_en="Sound effect text",
        default_label_ja="描き文字",
        prompt_aliases=(
            "sound effect",
            "sfx",
            "onomatopoeia",
            "効果音",
            "擬音",
        ),
        is_text_protection_target=True,
    ),
    Sam3MangaMaskCategoryDefinition(
        value="speed_lines",
        group="effect",
        label_key="sam3.manga_mask_category.speed_lines",
        default_label_en="Speed lines",
        default_label_ja="効果線",
        prompt_aliases=(
            "speed line",
            "motion lines",
            "effect lines",
            "集中線",
            "スピード線",
        ),
    ),
    Sam3MangaMaskCategoryDefinition(
        value="screen_tone_region",
        group="effect",
        label_key="sam3.manga_mask_category.screen_tone_region",
        default_label_en="Screen tone region",
        default_label_ja="トーン領域",
        prompt_aliases=("screen tone", "screentone", "tone", "スクリーントーン", "トーン"),
    ),
)

SAM3_MANGA_MASK_CATEGORY_VALUES: tuple[MangaMaskCategory, ...] = tuple(
    definition.value for definition in _DEFINITIONS
)

_PRESET_CATEGORIES: dict[MangaMaskPreset, tuple[MangaMaskCategory, ...]] = {
    "all": SAM3_MANGA_MASK_CATEGORY_VALUES,
    "speech_bubble_protection": (
        "speech_bubble",
        "caption_box",
        "sound_effect_text",
    ),
    "character_extraction": (
        "character",
        "face",
        "eyes",
        "hands",
        "hair",
        "clothes",
        "weapon",
        "prop",
    ),
    "background_extraction": (
        "background",
        "speed_lines",
        "screen_tone_region",
    ),
    "inpaint_prep": (
        "speech_bubble",
        "caption_box",
        "character",
        "background",
        "sound_effect_text",
    ),
}

SAM3_MANGA_MASK_PRESET_VALUES: tuple[MangaMaskPreset, ...] = tuple(_PRESET_CATEGORIES)

_DEFINITION_BY_VALUE: dict[MangaMaskCategory, Sam3MangaMaskCategoryDefinition] = {
    definition.value: definition for definition in _DEFINITIONS
}


def manga_mask_category_definitions() -> tuple[Sam3MangaMaskCategoryDefinition, ...]:
    return _DEFINITIONS


def manga_mask_category_values() -> tuple[MangaMaskCategory, ...]:
    return SAM3_MANGA_MASK_CATEGORY_VALUES


def manga_mask_category_definition(
    value: object,
) -> Sam3MangaMaskCategoryDefinition | None:
    category = normalize_manga_mask_category(value)
    if category is None:
        return None
    return _DEFINITION_BY_VALUE[category]


def normalize_manga_mask_category(value: object) -> MangaMaskCategory | None:
    if not isinstance(value, str):
        return None
    key = _normalize_lookup_key(value)
    if not key:
        return None
    return _CATEGORY_BY_LOOKUP_KEY.get(key)


def categories_for_manga_mask_preset(
    preset: object,
) -> tuple[MangaMaskCategory, ...]:
    if not isinstance(preset, str):
        raise ValueError("Manga mask preset must be a string.")
    key = _normalize_lookup_key(preset)
    if key not in _PRESET_CATEGORIES:
        raise ValueError(f"Unknown manga mask preset: {preset!r}")
    return _PRESET_CATEGORIES[key]


def manga_mask_category_public_payload(
    *,
    locale: str = "en",
) -> dict[str, object]:
    return {
        "schemaVersion": SAM3_MANGA_MASK_CATEGORY_SCHEMA_VERSION,
        "categories": [definition.to_public_dict(locale=locale) for definition in _DEFINITIONS],
        "presets": {preset: list(categories) for preset, categories in _PRESET_CATEGORIES.items()},
    }


def _normalize_lookup_key(value: str) -> str:
    normalized = str(value).strip().lower().replace("-", " ").replace("_", " ")
    return "_".join(normalized.split())


def _build_category_lookup() -> dict[str, MangaMaskCategory]:
    lookup: dict[str, MangaMaskCategory] = {}
    for definition in _DEFINITIONS:
        values = (
            definition.value,
            definition.default_label_en,
            definition.default_label_ja,
            *definition.prompt_aliases,
        )
        for alias in values:
            _add_alias(lookup, alias, definition.value)
    return lookup


def _add_alias(
    lookup: dict[str, MangaMaskCategory],
    alias: str,
    value: MangaMaskCategory,
) -> None:
    key = _normalize_lookup_key(alias)
    if not key:
        return
    existing = lookup.get(key)
    if existing is not None and existing != value:
        raise RuntimeError(f"Duplicate manga mask category alias {alias!r}: {existing} / {value}")
    lookup[key] = value


_CATEGORY_BY_LOOKUP_KEY = _build_category_lookup()

__all__ = [
    "SAM3_MANGA_MASK_CATEGORY_SCHEMA_VERSION",
    "SAM3_MANGA_MASK_CATEGORY_VALUES",
    "SAM3_MANGA_MASK_PRESET_VALUES",
    "MangaMaskCategory",
    "MangaMaskCategoryGroup",
    "MangaMaskPreset",
    "Sam3MangaMaskCategoryDefinition",
    "categories_for_manga_mask_preset",
    "manga_mask_category_definition",
    "manga_mask_category_definitions",
    "manga_mask_category_public_payload",
    "manga_mask_category_values",
    "normalize_manga_mask_category",
]
