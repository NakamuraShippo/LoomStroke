"""Manual vertical forms for fonts without usable OpenType vertical shaping."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QFontMetricsF

_PUNCTUATION_FORMS = {
    "、": "︑",
    "。": "︒",
    "，": "︐",  # noqa: RUF001
    ",": "︐",
    "．": "︒",  # noqa: RUF001
    ".": "︒",
    "（": "︵",  # noqa: RUF001
    "）": "︶",  # noqa: RUF001
    "(": "︵",
    ")": "︶",
    "「": "﹁",
    "」": "﹂",
    "『": "﹃",
    "』": "﹄",
    "【": "︻",
    "】": "︼",
    "〔": "︹",  # noqa: RUF001
    "〕": "︺",  # noqa: RUF001
    "〈": "︿",
    "〉": "﹀",
    "《": "︽",
    "》": "︾",
    "{": "︷",
    "}": "︸",
    "｛": "︷",  # noqa: RUF001
    "｝": "︸",  # noqa: RUF001
    "[": "﹇",
    "]": "﹈",
    "［": "﹇",  # noqa: RUF001
    "］": "﹈",  # noqa: RUF001
    "：": "︓",  # noqa: RUF001
    "；": "︔",  # noqa: RUF001
    ":": "︓",
    ";": "︔",
}
_ROTATE_FALLBACK = frozenset("（）()「」『』【】〔〕〈〉《》{}｛｝[]［］：；:;")  # noqa: RUF001
_CENTER_ROTATE = frozenset("ーｰ―—…⋯‥〜～")  # noqa: RUF001
_SMALL_KANA = frozenset(
    "ぁぃぅぇぉっゃゅょゎゕゖァィゥェォッャュョヮヵヶ" "ㇰㇱㇲㇳㇴㇵㇶㇷㇸㇹㇺㇻㇼㇽㇾㇿ"
)


def is_center_rotated_char(text: str) -> bool:
    return bool(text and text[0] in _CENTER_ROTATE)


def is_sideways_char(text: str) -> bool:
    if not text:
        return False
    cp = ord(text[0])
    if 0x0021 <= cp <= 0x007E:
        return True
    if 0x00C0 <= cp <= 0x00FF and cp not in (0x00D7, 0x00F7):
        return True
    return 0xFF61 <= cp <= 0xFF9F


def glyph_draw_spec(  # noqa: PLR0911
    text: str,
    *,
    metrics: QFontMetricsF,
    column_step: float,
    char_step: float,
    vertical_flow: str,
    rotate_half_width: bool,
    supports_char: Callable[[str], bool],
) -> tuple[str, float, float, float]:
    if not text:
        return (" ", 0.0, 0.0, 0.0)
    base = text[0]
    form = _PUNCTUATION_FORMS.get(base)
    if form:
        if supports_char(form):
            return (form + text[1:], 0.0, 0.0, 0.0)
        if base in _ROTATE_FALLBACK:
            return (text, 90.0, 0.0, 0.0)
        return (
            text,
            0.0,
            column_step * (0.18 if vertical_flow == "left" else 0.14),
            -char_step * 0.24,
        )
    if is_center_rotated_char(text) or (rotate_half_width and is_sideways_char(text)):
        return (text, 90.0, 0.0, 0.0)
    if base in _SMALL_KANA:
        # Align ink toward the full-size reference glyph's upper-right corner.
        # Ink bounds do not contain tracking or inter-column line-height spacing.
        reference = metrics.tightBoundingRect("日")
        ink = metrics.tightBoundingRect(text)
        dx = max(reference.width() * 0.06, reference.right() - ink.right())
        dy = min(-reference.height() * 0.06, reference.top() - ink.top())
        return (text, 0.0, dx, dy)
    return (text, 0.0, 0.0, 0.0)
