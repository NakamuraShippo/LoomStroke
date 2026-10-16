"""Deterministic text fitting helpers for MCP comic plans."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any

DEFAULT_FONT_SIZE = 20
MIN_FONT_SIZE = 6
MAX_FONT_SIZE = 256
LINE_HEIGHT_FACTOR = 1.38
BASELINE_GUARD_FACTOR = 0.22
# Sub-pixel: absorbs float rounding in threshold comparisons without ever
# masking a real (>= 0.01px) shortfall.
_SLACK_TOLERANCE = 1e-3

_LATIN_WORD = re.compile(r"[A-Za-z0-9]+(?:['-][A-Za-z0-9]+)*")


@dataclass(frozen=True, slots=True)
class ComicTextLayoutResult:
    fits: bool
    font_size: int
    line_count: int
    overflow_horizontal: bool
    overflow_vertical: bool
    warnings: tuple[str, ...] = ()
    visual_risk_warnings: tuple[str, ...] = ()
    recommended_padding: int = 0

    def to_fit_report(
        self,
        *,
        text_id: str,
        role: str,
        panel_id: str,
        include_visual_risks: bool = False,
    ) -> dict[str, Any]:
        report = {
            "textId": text_id,
            "role": role,
            "panelId": panel_id,
            "fits": self.fits,
            "fontSize": self.font_size,
            "lineCount": self.line_count,
            "overflow": {
                "horizontal": self.overflow_horizontal,
                "vertical": self.overflow_vertical,
            },
            "warnings": list(self.warnings),
        }
        if include_visual_risks and self.visual_risk_warnings:
            report["visualRiskWarnings"] = list(self.visual_risk_warnings)
            report["recommendedPadding"] = self.recommended_padding
        return report


def fit_text_box(
    text: str,
    *,
    width: float,
    height: float,
    font_size: int | None = None,
    fit_mode: str = "none",
    min_font_size: int = MIN_FONT_SIZE,
) -> ComicTextLayoutResult:
    """Return a conservative fit report without exposing text in the response."""

    initial_size = _clamp_int(font_size, DEFAULT_FONT_SIZE, MIN_FONT_SIZE, MAX_FONT_SIZE)
    minimum = _clamp_int(min_font_size, MIN_FONT_SIZE, MIN_FONT_SIZE, initial_size)
    mode = fit_mode if fit_mode in {"none", "wrap", "shrink_to_fit", "truncate"} else "none"

    if mode == "shrink_to_fit":
        for size in range(initial_size, minimum - 1, -1):
            result = _layout_at_size(text, width=width, height=height, font_size=size)
            if result.fits:
                return result
        result = _layout_at_size(text, width=width, height=height, font_size=minimum)
        warnings = tuple(dict.fromkeys((*result.warnings, "text_overflow_after_shrink")))
        return ComicTextLayoutResult(
            fits=False,
            font_size=result.font_size,
            line_count=result.line_count,
            overflow_horizontal=result.overflow_horizontal,
            overflow_vertical=result.overflow_vertical,
            warnings=warnings,
            visual_risk_warnings=result.visual_risk_warnings,
            recommended_padding=result.recommended_padding,
        )

    result = _layout_at_size(text, width=width, height=height, font_size=initial_size)
    if mode == "none":
        horizontal = _line_width(text.replace("\n", ""), initial_size) > max(0.0, width)
        vertical = _estimated_text_height(1 if text else 0, initial_size) > max(0.0, height)
        fits = not horizontal and not vertical
        visual_risks = _visual_risks(
            text,
            line_count=1 if text else 0,
            font_size=initial_size,
            max_height=max(0.0, height),
        )
        return ComicTextLayoutResult(
            fits=fits,
            font_size=initial_size,
            line_count=1 if text else 0,
            overflow_horizontal=horizontal,
            overflow_vertical=vertical,
            warnings=() if fits else ("text_overflow",),
            visual_risk_warnings=visual_risks,
            recommended_padding=_recommended_padding(initial_size, text),
        )
    if mode == "truncate" and not result.fits:
        warnings = tuple(dict.fromkeys((*result.warnings, "text_would_truncate")))
        return ComicTextLayoutResult(
            fits=True,
            font_size=result.font_size,
            line_count=max(1, min(result.line_count, _max_lines(height, result.font_size))),
            overflow_horizontal=False,
            overflow_vertical=False,
            warnings=warnings,
            visual_risk_warnings=result.visual_risk_warnings,
            recommended_padding=result.recommended_padding,
        )
    return result


def _layout_at_size(
    text: str,
    *,
    width: float,
    height: float,
    font_size: int,
) -> ComicTextLayoutResult:
    max_width = max(0.0, width)
    max_height = max(0.0, height)
    lines = _wrap_lines(text, max_width=max_width, font_size=font_size)
    line_count = len(lines)
    overflow_horizontal = any(_line_width(line, font_size) > max_width for line in lines)
    estimated_height = _estimated_text_height(line_count, font_size)
    overflow_vertical = estimated_height > max_height
    fits = not overflow_horizontal and not overflow_vertical
    warnings = () if fits else ("text_overflow",)
    visual_risks = _visual_risks(
        text,
        line_count=line_count,
        font_size=font_size,
        max_height=max_height,
        estimated_height=estimated_height,
    )
    return ComicTextLayoutResult(
        fits=fits,
        font_size=font_size,
        line_count=line_count,
        overflow_horizontal=overflow_horizontal,
        overflow_vertical=overflow_vertical,
        warnings=warnings,
        visual_risk_warnings=visual_risks,
        recommended_padding=_recommended_padding(font_size, text),
    )


def _wrap_lines(text: str, *, max_width: float, font_size: int) -> tuple[str, ...]:
    if not text:
        return ()
    wrapped: list[str] = []
    for paragraph in text.splitlines() or [text]:
        current = ""
        for unit in _break_units(paragraph):
            # A Latin word wider than the box has to be broken anyway.
            pieces = (
                list(unit) if len(unit) > 1 and _line_width(unit, font_size) > max_width else [unit]
            )
            for piece in pieces:
                candidate = f"{current}{piece}"
                if current and _line_width(candidate, font_size) > max_width:
                    wrapped.append(current)
                    current = "" if piece.isspace() else piece
                else:
                    current = candidate
        if current:
            wrapped.append(current)
        elif not paragraph:
            wrapped.append("")
    return tuple(wrapped)


def wrap_lines(text: str, *, max_width: float, font_size: int) -> tuple[str, ...]:
    """Break `text` exactly as the fit reports assume it will break.

    Renderer fallbacks (e.g. the Pillow path) must use this instead of their own
    wrapping so the drawn line breaks match `lineCount` in the fit report.
    """

    return _wrap_lines(text, max_width=max_width, font_size=font_size)


def _break_units(paragraph: str) -> list[str]:
    """Split into break opportunities: CJK breaks anywhere, Latin words stay whole."""

    units: list[str] = []
    index = 0
    while index < len(paragraph):
        match = _LATIN_WORD.match(paragraph, index)
        if match is not None:
            units.append(match.group())
            index = match.end()
            continue
        units.append(paragraph[index])
        index += 1
    return units


def _line_width(text: str, font_size: int) -> float:
    return sum(_char_width(char, font_size) for char in text)


def _char_width(char: str, font_size: int) -> float:
    if char.isspace():
        return font_size * 0.35
    codepoint = ord(char)
    if (
        0x3040 <= codepoint <= 0x30FF
        or 0x3400 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0xFF01 <= codepoint <= 0xFF60
    ):
        return font_size
    if char in ".,:;!?-_()[]{}'\"":
        return font_size * 0.45
    return font_size * 0.58


def _max_lines(height: float, font_size: int) -> int:
    if font_size <= 0:
        return 0
    return max(1, math.floor(max(0.0, height) / (font_size * LINE_HEIGHT_FACTOR)))


def estimated_text_height(line_count: int, font_size: int) -> float:
    """Vertical space the fit reports assume for `line_count` lines, in pixels.

    Callers that size a box themselves must use this, otherwise the box and the
    fit report disagree and `visualRiskWarnings` fires on boxes that are fine.
    """

    return _estimated_text_height(line_count, font_size)


def _estimated_text_height(line_count: int, font_size: int) -> float:
    if line_count <= 0:
        return 0.0
    return line_count * font_size * LINE_HEIGHT_FACTOR + font_size * BASELINE_GUARD_FACTOR


def _visual_risks(
    text: str,
    *,
    line_count: int,
    font_size: int,
    max_height: float,
    estimated_height: float | None = None,
) -> tuple[str, ...]:
    risks: list[str] = []
    height = (
        estimated_height
        if estimated_height is not None
        else _estimated_text_height(
            line_count,
            font_size,
        )
    )
    # Boxes sized to land exactly on a threshold (e.g. the speech bubble body
    # adds exactly half a line of slack) must not flip to "at risk" over float
    # rounding, so comparisons get a tolerance well above arithmetic noise.
    slack = max_height - height + _SLACK_TOLERANCE
    # Multi-line text is only at risk when the box leaves less than half a line
    # of slack; reporting it for every wrapped box made the signal unusable.
    if line_count >= 2 and slack < font_size * LINE_HEIGHT_FACTOR * 0.5:
        risks.append("lineHeightRisk")
    if _has_mixed_cjk_latin(text):
        risks.append("mixedScriptRisk")
    if line_count > 0 and slack < max(2.0, font_size * 0.25):
        risks.append("verticalClipRisk")
    return tuple(dict.fromkeys(risks))


def _recommended_padding(font_size: int, text: str) -> int:
    multiplier = 0.55 if _has_mixed_cjk_latin(text) else 0.45
    return max(4, math.ceil(font_size * multiplier))


def _has_mixed_cjk_latin(text: str) -> bool:
    has_cjk = False
    has_latin = False
    for char in text:
        codepoint = ord(char)
        if (
            0x3040 <= codepoint <= 0x30FF
            or 0x3400 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
            or 0xFF01 <= codepoint <= 0xFF60
        ):
            has_cjk = True
        elif "A" <= char <= "Z" or "a" <= char <= "z" or "0" <= char <= "9":
            has_latin = True
        if has_cjk and has_latin:
            return True
    return False


def _clamp_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    parsed = default if not isinstance(value, int) or isinstance(value, bool) else value
    return max(minimum, min(maximum, parsed))


__all__ = [
    "ComicTextLayoutResult",
    "estimated_text_height",
    "fit_text_box",
    "wrap_lines",
]
