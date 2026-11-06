"""Fixed AI image canvas sizes, checked against vendor tables on 2026-11-03."""

from __future__ import annotations

from math import gcd

from .startpage_models import CanvasPreset

# Keep these independent of optional gateway imports during New Document startup.
_GPT_SIZES = (
    (1024, 1024),
    (1024, 1536),
    (1536, 1024),
    (1536, 864),
    (864, 1536),
    (2048, 2048),
    (2048, 1152),
    (1152, 2048),
    (3840, 2160),
    (2160, 3840),
)
_NANO_LEGACY = (
    ("1:1", 1024, 1024),
    ("2:3", 832, 1248),
    ("3:2", 1248, 832),
    ("3:4", 864, 1184),
    ("4:3", 1184, 864),
    ("4:5", 896, 1152),
    ("5:4", 1152, 896),
    ("9:16", 768, 1344),
    ("16:9", 1344, 768),
    ("21:9", 1536, 672),
)
_NANO_MODERN_1K = (
    ("1:1", 1024, 1024),
    ("1:4", 512, 2048),
    ("1:8", 384, 3072),
    ("2:3", 848, 1264),
    ("3:2", 1264, 848),
    ("3:4", 896, 1200),
    ("4:1", 2048, 512),
    ("4:3", 1200, 896),
    ("4:5", 928, 1152),
    ("5:4", 1152, 928),
    ("8:1", 3072, 384),
    ("9:16", 768, 1376),
    ("16:9", 1376, 768),
    ("21:9", 1584, 672),
)
_EXTREME_ASPECTS = {"1:4", "1:8", "4:1", "8:1"}


def ai_image_presets() -> tuple[CanvasPreset, ...]:
    """Return stable IDs and model-labelled pixel sizes, without implicit rounding."""
    entries: list[CanvasPreset] = []

    def add(key: str, family: str, tier: str, ratio: str, width: int, height: int) -> None:
        entries.append(
            CanvasPreset(
                id=f"ai_image_{key}_{width}x{height}",
                label=f"[{family}] {tier}{ratio} {width} x {height}",
                width_px=width,
                height_px=height,
            )
        )

    for width, height in _GPT_SIZES:
        divisor = gcd(width, height)
        add(
            "gpt",
            "ChatGPT / GPT Image",
            "",
            f"{width // divisor}:{height // divisor}",
            width,
            height,
        )
    for ratio, width, height in _NANO_LEGACY:
        add("nano_legacy", "NanoBanana", "1K ", ratio, width, height)
    # Google's 1K/2K/4K tables have these exact 1x/2x/4x dimensions.
    # Pro shares the ten common ratios; only 2 / 2.1 support the four extremes.
    for tier, scale in (("1K", 1), ("2K", 2), ("4K", 4)):
        for ratio, width, height in _NANO_MODERN_1K:
            family = "NanoBanana 2/2.1" if ratio in _EXTREME_ASPECTS else "NanoBanana 2/2.1/Pro"
            add(
                f"nano_modern_{tier.lower()}",
                family,
                f"{tier} ",
                ratio,
                width * scale,
                height * scale,
            )
    for ratio, width, height in _NANO_MODERN_1K:
        # 2.1 and Pro do not offer 512. Google's 512 21:9 row says 792x168,
        # inconsistent with its stated ratio; omit that unverified size.
        if ratio != "21:9":
            add("nano2_512", "NanoBanana 2", "512 ", ratio, width // 2, height // 2)
    return tuple(entries)
