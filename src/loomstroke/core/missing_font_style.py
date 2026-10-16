"""Retain conventional style intent when the requested font family is absent."""

from PySide6.QtGui import QFont, QFontDatabase


def preserve_missing_font_style(font: QFont, family: str, style: str) -> None:
    if not family or QFontDatabase.hasFamily(family):
        return
    name = style.casefold().replace(" ", "").replace("-", "")
    weights = (
        ("extrablack", QFont.Weight.Black),
        ("black", QFont.Weight.Black),
        ("heavy", QFont.Weight.Black),
        ("extrabold", QFont.Weight.ExtraBold),
        ("ultrabold", QFont.Weight.ExtraBold),
        ("semibold", QFont.Weight.DemiBold),
        ("demibold", QFont.Weight.DemiBold),
        ("bold", QFont.Weight.Bold),
        ("medium", QFont.Weight.Medium),
        ("extralight", QFont.Weight.ExtraLight),
        ("ultralight", QFont.Weight.ExtraLight),
        ("light", QFont.Weight.Light),
        ("thin", QFont.Weight.Thin),
    )
    for token, weight in weights:
        if token in name:
            font.setWeight(weight)
            break
    if "italic" in name or "oblique" in name:
        font.setItalic(True)
