"""PNG preview renderer for MCP ComicPagePlan payloads."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication, QImage, QPainter, QPen

from .comic_native_text import GLYPH_GUARD, build_comic_native_text, draw_comic_native_text
from .comic_text_layout import LINE_HEIGHT_FACTOR, wrap_lines
from .serialization import qimage_to_png_bytes
from .types import McpStructuredError, ReasonCode

AssetImageLoader = Callable[[str], QImage]


@dataclass(frozen=True, slots=True)
class _HeadlessTextCommand:
    rect: QRectF
    text: str
    color: QColor
    font_size: int
    word_wrap: bool = True


def render_comic_preview_image(
    plan: Mapping[str, Any],
    *,
    asset_loader: AssetImageLoader | None = None,
    base_asset_id: str | None = None,
) -> tuple[QImage, dict[str, Any]]:
    """Render a ComicPagePlan to a detached QImage preview."""

    page = _mapping(plan.get("page"))
    width = int(page.get("width") or 1)
    height = int(page.get("height") or 1)
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(_qcolor(page.get("background"), QColor(255, 255, 255, 255)))
    warnings: list[str] = []

    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    text_requested = False
    qt_text_available = isinstance(QGuiApplication.instance(), QGuiApplication)
    text_rendered = qt_text_available
    text_renderer = "qt" if qt_text_available else "none"
    headless_text_commands: list[_HeadlessTextCommand] = []
    fit_reports: list[dict[str, Any]] = []
    try:
        if base_asset_id:
            _draw_base_asset(
                painter,
                QRectF(0, 0, width, height),
                base_asset_id,
                asset_loader=asset_loader,
            )
        style = _mapping(plan.get("style"))
        for panel in _list(plan.get("panels")):
            panel_map = _mapping(panel)
            _draw_panel(painter, panel_map, style)
            panel_id = str(panel_map.get("id") or "")
            for art_slot in _list(panel_map.get("artSlots")):
                _draw_art_slot(
                    painter,
                    _mapping(art_slot),
                    asset_loader=asset_loader,
                    text_rendered=text_rendered,
                    headless_text_commands=headless_text_commands,
                    warnings=warnings,
                )
            for balloon in _list(panel_map.get("balloons")):
                text_requested = True
                fit_reports.append(
                    _draw_text_box(
                        painter,
                        _mapping(balloon),
                        panel_id=panel_id,
                        role="balloon",
                        default_fill=QColor(255, 255, 255, 255),
                        text_rendered=text_rendered,
                        headless_text_commands=headless_text_commands,
                        warnings=warnings,
                    )
                )
            for caption in _list(panel_map.get("captions")):
                text_requested = True
                fit_reports.append(
                    _draw_text_box(
                        painter,
                        _mapping(caption),
                        panel_id=panel_id,
                        role="caption",
                        default_fill=QColor(255, 244, 168, 255),
                        text_rendered=text_rendered,
                        headless_text_commands=headless_text_commands,
                        warnings=warnings,
                    )
                )
    finally:
        painter.end()

    if headless_text_commands and not qt_text_available:
        try:
            image = _draw_headless_text(image, headless_text_commands)
            text_rendered = True
            text_renderer = "pillow_fallback"
        except Exception:
            warnings.append("text_render_skipped_headless_fallback_failed")

    if "vertical_text_render_requires_qgui_application" in warnings:
        text_rendered = False
    if text_requested and not text_rendered:
        warnings.append("text_render_skipped_no_qgui_application")
    return image, {
        "textRendered": not text_requested or text_rendered,
        "textRenderer": text_renderer,
        "textFitReports": fit_reports,
        "warnings": list(dict.fromkeys(warnings)),
    }


def _draw_base_asset(
    painter: QPainter,
    rect: QRectF,
    asset_id: str,
    *,
    asset_loader: AssetImageLoader | None,
) -> None:
    if asset_loader is None:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE,
            "Comic base asset loading is not available.",
            details={"assetId": asset_id},
            code=-32603,
        )
    try:
        asset = asset_loader(asset_id)
    except McpStructuredError:
        raise
    except Exception as exc:
        raise McpStructuredError(
            ReasonCode.BACKEND_FAILED,
            "Comic base asset could not be loaded.",
            details={"assetId": asset_id, "error": exc.__class__.__name__},
            code=-32603,
        ) from exc
    painter.drawImage(rect, asset)


def _draw_panel(painter: QPainter, panel: Mapping[str, Any], style: Mapping[str, Any]) -> None:
    rect = _rect(panel.get("rect"))
    radius = _float(panel.get("borderRadius"), 4.0)
    fill = _qcolor(panel.get("background"), QColor(255, 253, 248, 255))
    stroke = _qcolor(style.get("panelStrokeColor"), QColor(17, 17, 17, 255))
    stroke_width = _float(style.get("panelStrokeWidth"), 5.0)
    painter.setBrush(QBrush(fill))
    painter.setPen(QPen(stroke, stroke_width))
    painter.drawRoundedRect(rect, radius, radius)


def _draw_art_slot(
    painter: QPainter,
    slot: Mapping[str, Any],
    *,
    asset_loader: AssetImageLoader | None,
    text_rendered: bool,
    headless_text_commands: list[_HeadlessTextCommand],
    warnings: list[str],
) -> None:
    rect = _rect(slot.get("rect"))
    asset_id = slot.get("assetId")
    if isinstance(asset_id, str) and asset_id.strip() and asset_loader is not None:
        try:
            asset = asset_loader(asset_id.strip())
        except McpStructuredError:
            raise
        except Exception as exc:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "Comic art asset could not be loaded.",
                details={"assetId": asset_id, "error": exc.__class__.__name__},
                code=-32603,
            ) from exc
        painter.drawImage(rect, asset)
        return

    placeholder = _mapping(slot.get("placeholder"))
    fill = _qcolor(placeholder.get("fillColor"), QColor(221, 235, 255, 255))
    painter.setBrush(QBrush(fill))
    painter.setPen(QPen(QColor(17, 17, 17, 255), 3))
    painter.drawRoundedRect(rect, 4, 4)
    label = str(placeholder.get("label") or slot.get("id") or "")
    if label and text_rendered:
        _draw_centered_text(painter, rect, label, QColor(17, 17, 17, 255), 18)
    elif label:
        headless_text_commands.append(
            _HeadlessTextCommand(rect, label, QColor(17, 17, 17, 255), 18)
        )


def _draw_text_box(
    painter: QPainter,
    box: Mapping[str, Any],
    *,
    panel_id: str,
    role: str,
    default_fill: QColor,
    text_rendered: bool,
    headless_text_commands: list[_HeadlessTextCommand],
    warnings: list[str],
) -> dict[str, Any]:
    rect = _rect(box.get("rect"))
    text_style = _mapping(box.get("textStyle"))
    text = str(box.get("text") or "")[:2000]
    native = build_comic_native_text(box, qt_available=text_rendered)
    fit = native.fit
    fill = _qcolor(box.get("fillColor"), default_fill)
    stroke = _qcolor(box.get("strokeColor"), QColor(17, 17, 17, 255))
    radius = _float(box.get("radius"), 10.0)
    painter.setBrush(QBrush(fill))
    painter.setPen(QPen(stroke, _float(box.get("strokeWidth"), 4.0)))
    painter.drawRoundedRect(rect, radius, radius)
    if text and text_rendered:
        draw_comic_native_text(painter, native)
    elif text and text_style.get("vertical") is True:
        warnings.append("vertical_text_render_requires_qgui_application")
    elif text:
        color = _qcolor(box.get("textColor"), QColor(17, 17, 17, 255))
        headless_text_commands.append(
            _HeadlessTextCommand(
                rect.adjusted(8 + GLYPH_GUARD, 8 + GLYPH_GUARD, -8 - GLYPH_GUARD, -8 - GLYPH_GUARD),
                native.object.meta["text_tool"]["content"],
                color,
                fit.font_size,
                str(text_style.get("fit")) != "none",
            )
        )
    return fit.to_fit_report(
        text_id=str(box.get("id") or ""),
        role=role,
        panel_id=panel_id,
    )


def comic_text_font(font_size: int) -> QFont:
    """Qt font for comic text, sized in pixels.

    The deterministic fit engine and the Pillow fallback both measure in pixels.
    Using `setPointSize` here rendered ~1.33x larger at 96 DPI, so text overflowed
    boxes the fit report had already called a fit.
    """

    font = QFont()
    font.setPixelSize(max(1, int(font_size)))
    return font


def _draw_centered_text(
    painter: QPainter,
    rect: QRectF,
    text: str,
    color: QColor,
    font_size: int,
) -> None:
    painter.setFont(comic_text_font(font_size))
    painter.setPen(QPen(color, 1))
    flags = Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap
    painter.drawText(rect, flags, text)


def _draw_headless_text(image: QImage, commands: list[_HeadlessTextCommand]) -> QImage:
    from PIL import Image, ImageDraw  # type: ignore[import-not-found]  # noqa: PLC0415

    with Image.open(BytesIO(qimage_to_png_bytes(image))) as opened:
        pil_image = opened.convert("RGBA")
    draw = ImageDraw.Draw(pil_image)
    for command in commands:
        font = _load_pillow_font(command.font_size)
        lines = (
            wrap_lines(
                command.text,
                max_width=max(1.0, command.rect.width()),
                font_size=command.font_size,
            )
            if command.word_wrap
            else command.text.splitlines()
        )
        if not lines:
            continue
        line_height = max(1, int(command.font_size * LINE_HEIGHT_FACTOR))
        total_height = line_height * len(lines)
        y = int(command.rect.y() + max(0.0, (command.rect.height() - total_height) / 2.0))
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            text_width = max(1, int(bbox[2] - bbox[0]))
            x = int(command.rect.x() + max(0.0, (command.rect.width() - text_width) / 2.0))
            draw.text((x, y), line, fill=_rgba(command.color), font=font)
            y += line_height
    out = BytesIO()
    pil_image.save(out, format="PNG")
    rendered = QImage.fromData(out.getvalue(), "PNG")
    if rendered.isNull():
        raise RuntimeError("Pillow text fallback produced an invalid image")
    return rendered


def _load_pillow_font(font_size: int) -> Any:
    from PIL import ImageFont  # type: ignore[import-not-found]  # noqa: PLC0415

    candidates = (
        Path("C:/Windows/Fonts/meiryo.ttc"),
        Path("C:/Windows/Fonts/YuGothM.ttc"),
        Path("C:/Windows/Fonts/msgothic.ttc"),
        Path("C:/Windows/Fonts/arial.ttf"),
    )
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            return ImageFont.truetype(str(candidate), font_size)
        except OSError:
            continue
    try:
        return ImageFont.truetype("DejaVuSans.ttf", font_size)
    except OSError:
        pass
    try:
        return ImageFont.load_default(size=font_size)
    except TypeError:
        return ImageFont.load_default()


def _rgba(color: QColor) -> tuple[int, int, int, int]:
    return (color.red(), color.green(), color.blue(), color.alpha())


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _rect(value: Any) -> QRectF:
    rect = _mapping(value)
    return QRectF(
        _float(rect.get("x"), 0.0),
        _float(rect.get("y"), 0.0),
        _float(rect.get("width"), 1.0),
        _float(rect.get("height"), 1.0),
    )


def _qcolor(value: Any, default: QColor) -> QColor:
    if isinstance(value, str) and value.strip():
        color = QColor(value.strip())
        if color.isValid():
            return color
    return QColor(default)


def _float(value: Any, default: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = default
    return parsed


def _int(value: Any, default: int) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


__all__ = [
    "comic_text_font",
    "render_comic_preview_image",
]
