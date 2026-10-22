"""Compact text-tool context toolbar content.

Mirrors the FontPanelWidget layout and behaviour (minus custom font folders)
in a two-row compact form suitable for the floating ContextToolbar.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any, cast

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from loomstroke.plugins.text_tool.common import (
    VERTICAL_FLOW_LEFT,
    VERTICAL_FLOW_RIGHT,
    normalize_vertical_flow,
)

logger = logging.getLogger(__name__)

_LABEL_STYLE = "font-size: 11px;"


def _ts(text: str) -> str:
    """Translate via TextFontPanel context (same as the font panel)."""
    try:
        from loomstroke.ui.translations import (  # noqa: PLC0415
            translate_with_fallback,
            translated_literal,
        )

        return translated_literal(
            lambda source: translate_with_fallback("TextFontPanel", source),
            text,
        )
    except Exception:
        return text


class TextContextContent(QWidget):
    """Two-row compact widget that replicates the font panel controls.

    Row 1: Font Family | Font Style | Font Size | Color + Opacity
    Row 2: Text Mode | Alignment | Line Height | Tracking
    """

    _vertical_flow_combo: QComboBox | None

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "_path_button"):
            from loomstroke.plugins.text_tool.path_text_controls import (  # noqa: PLC0415
                ts as path_ts,
            )

            self._path_button.setText(path_ts("Text Along Path"))

    def __init__(
        self,
        parent: QWidget | None,
        on_changed: Callable[[str, Any], None],
        *,
        is_vertical: bool = False,
    ) -> None:
        super().__init__(parent)
        self._on_changed = on_changed
        self._is_vertical = is_vertical
        self._block = False
        self._color = QColor(0, 0, 0, 255)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(2)

        # ── Row 1 ──
        row1 = QWidget(self)
        r1 = QHBoxLayout(row1)
        r1.setContentsMargins(0, 0, 0, 0)
        r1.setSpacing(4)

        r1.addWidget(self._make_label(_ts("Font Family")))
        self._family_combo = QComboBox(self)
        self._family_combo.setMinimumWidth(120)
        self._family_combo.setEditable(True)
        self._family_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        r1.addWidget(self._family_combo, 2)

        r1.addWidget(self._make_label(_ts("Font Style")))
        self._style_combo = QComboBox(self)
        self._style_combo.setMinimumWidth(80)
        r1.addWidget(self._style_combo, 1)

        r1.addWidget(self._make_label(_ts("Font Size (pt)")))
        _SIZE_PRESETS = [
            "6",
            "7",
            "8",
            "9",
            "10",
            "11",
            "12",
            "14",
            "16",
            "18",
            "20",
            "24",
            "28",
            "32",
            "36",
            "42",
            "48",
            "56",
            "64",
            "72",
            "96",
            "128",
        ]
        self._size_combo = self._make_numeric_combo(
            _SIZE_PRESETS,
            width=70,
            min_val=1.0,
            max_val=512.0,
            decimals=1,
        )
        r1.addWidget(self._size_combo)

        r1.addWidget(self._make_label(_ts("Color")))
        self._color_button = QPushButton(self)
        self._color_button.setFixedWidth(60)
        self._color_button.setFixedHeight(22)
        r1.addWidget(self._color_button)

        r1.addWidget(self._make_label(_ts("Opacity")))
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setValue(100)
        self._opacity_slider.setFixedWidth(60)
        r1.addWidget(self._opacity_slider)
        self._opacity_label = QLabel("100%", self)
        self._opacity_label.setStyleSheet(_LABEL_STYLE)
        self._opacity_label.setFixedWidth(32)
        r1.addWidget(self._opacity_label)
        r1.addStretch(1)

        root.addWidget(row1)

        from loomstroke.plugins.text_tool.path_text_controls import ts as path_ts  # noqa: PLC0415

        self._path_button = QPushButton(path_ts("Text Along Path"), self)
        self._path_button.clicked.connect(lambda: self._on_changed("path_text_action", True))
        r1.addWidget(self._path_button)

        # ── Row 2 ──
        row2 = QWidget(self)
        r2 = QHBoxLayout(row2)
        r2.setContentsMargins(0, 0, 0, 0)
        r2.setSpacing(4)

        self._wrap_check = QCheckBox(_ts("Word Wrap"), self)
        self._wrap_check.setChecked(True)
        r2.addWidget(self._wrap_check)

        r2.addWidget(self._make_label(_ts("Alignment")))
        self._align_combo = QComboBox(self)
        self._align_combo.addItem(_ts("Left"), "left")
        self._align_combo.addItem(_ts("Center"), "center")
        self._align_combo.addItem(_ts("Right"), "right")
        self._align_combo.addItem(_ts("Justify"), "justify")
        r2.addWidget(self._align_combo)

        r2.addWidget(self._make_label(_ts("Line Height")))
        _LH_PRESETS = ["0.8", "1.0", "1.2", "1.4", "1.5", "1.6", "1.8", "2.0", "2.5", "3.0"]
        self._line_height_combo = self._make_numeric_combo(
            _LH_PRESETS,
            width=65,
            min_val=0.5,
            max_val=4.0,
            decimals=2,
        )
        r2.addWidget(self._line_height_combo)

        r2.addWidget(self._make_label(_ts("Tracking")))
        _TRK_PRESETS = ["-2", "-1", "-0.5", "0", "0.5", "1", "2", "4", "8", "16"]
        self._tracking_combo = self._make_numeric_combo(
            _TRK_PRESETS,
            width=65,
            min_val=-64.0,
            max_val=128.0,
            decimals=1,
        )
        r2.addWidget(self._tracking_combo)

        if is_vertical:
            r2.addWidget(self._make_label(_ts("Paragraph Flow")))
            self._vertical_flow_combo = QComboBox(self)
            self._vertical_flow_combo.addItem(_ts("Leftward"), VERTICAL_FLOW_LEFT)
            self._vertical_flow_combo.addItem(_ts("Rightward"), VERTICAL_FLOW_RIGHT)
            r2.addWidget(self._vertical_flow_combo)
        else:
            self._vertical_flow_combo = None
        r2.addStretch(1)

        root.addWidget(row2)

        # ── Row 3 — Padding ──
        row3 = QWidget(self)
        r3 = QHBoxLayout(row3)
        r3.setContentsMargins(0, 0, 0, 0)
        r3.setSpacing(4)

        _PAD_PRESETS = ["0", "2", "4", "8", "12", "16", "24", "32", "48", "64"]
        r3.addWidget(self._make_label(_ts("Padding")))

        def _mk_pad_combo() -> QComboBox:
            return self._make_numeric_combo(
                _PAD_PRESETS, width=55, min_val=0.0, max_val=256.0, decimals=1
            )

        r3.addWidget(self._make_label("T"))
        self._padding_top_combo = _mk_pad_combo()
        r3.addWidget(self._padding_top_combo)
        r3.addWidget(self._make_label("B"))
        self._padding_bottom_combo = _mk_pad_combo()
        r3.addWidget(self._padding_bottom_combo)
        r3.addWidget(self._make_label("R"))
        self._padding_right_combo = _mk_pad_combo()
        r3.addWidget(self._padding_right_combo)
        r3.addWidget(self._make_label("L"))
        self._padding_left_combo = _mk_pad_combo()
        r3.addWidget(self._padding_left_combo)
        r3.addStretch(1)

        root.addWidget(row3)

        # ── Populate fonts & connect signals ──
        self._reload_font_families()
        self._bind_signals()
        self._update_color_button()
        self._update_vertical_labels()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setStyleSheet(_LABEL_STYLE)
        return label

    @staticmethod
    def _fmt_num(val: float, decimals: int) -> str:
        """Format a number with appropriate decimal places, stripping trailing zeros."""
        if decimals <= 0 or val == int(val):
            return str(int(val))
        return f"{val:.{decimals}f}".rstrip("0").rstrip(".")

    @staticmethod
    def _make_numeric_combo(
        presets: list[str],
        width: int = 70,
        *,
        min_val: float = 0.0,
        max_val: float = 9999.0,
        decimals: int = 1,
    ) -> QComboBox:
        """Create an editable QComboBox with preset values and numeric validation."""
        from PySide6.QtGui import QDoubleValidator  # noqa: PLC0415

        combo = QComboBox()
        combo.setEditable(True)
        combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        combo.addItems(presets)
        combo.setFixedWidth(width)
        validator = QDoubleValidator(min_val, max_val, decimals)
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        line_edit = combo.lineEdit()
        if line_edit is not None:
            line_edit.setValidator(validator)
        return combo

    # ------------------------------------------------------------------
    # Font population (mirrors FontPanelWidget)
    # ------------------------------------------------------------------

    def _reload_font_families(self) -> None:
        previous = self._family_combo.currentText()
        self._family_combo.blockSignals(True)
        self._family_combo.clear()
        families = sorted(QFontDatabase.families())
        self._family_combo.addItems(families)
        if previous:
            idx = self._family_combo.findText(previous)
            if idx >= 0:
                self._family_combo.setCurrentIndex(idx)
        self._family_combo.blockSignals(False)

    def _reload_styles(self, family: str, preferred: str = "") -> None:
        self._style_combo.blockSignals(True)
        self._style_combo.clear()
        styles = QFontDatabase.styles(family) if family else []
        if not styles:
            styles = ["Regular"]
        for s in styles:
            self._style_combo.addItem(s)
        if preferred:
            idx = self._style_combo.findText(preferred)
            if idx >= 0:
                self._style_combo.setCurrentIndex(idx)
        self._style_combo.blockSignals(False)

    # ------------------------------------------------------------------
    # Signal wiring
    # ------------------------------------------------------------------

    def _bind_signals(self) -> None:
        self._wrap_check.toggled.connect(self._on_wrap_toggled)
        self._family_combo.currentTextChanged.connect(self._on_family_text)
        self._style_combo.currentIndexChanged.connect(self._on_style)
        self._size_combo.currentTextChanged.connect(self._on_size_text)
        self._align_combo.currentIndexChanged.connect(self._on_align)
        self._line_height_combo.currentTextChanged.connect(self._on_line_height_text)
        self._tracking_combo.currentTextChanged.connect(self._on_tracking_text)
        self._padding_top_combo.currentTextChanged.connect(
            lambda t: self._on_pad_text("padding_top", t)
        )
        self._padding_right_combo.currentTextChanged.connect(
            lambda t: self._on_pad_text("padding_right", t)
        )
        self._padding_bottom_combo.currentTextChanged.connect(
            lambda t: self._on_pad_text("padding_bottom", t)
        )
        self._padding_left_combo.currentTextChanged.connect(
            lambda t: self._on_pad_text("padding_left", t)
        )
        self._color_button.clicked.connect(self._pick_color)
        self._opacity_slider.valueChanged.connect(self._on_opacity)
        if self._vertical_flow_combo is not None:
            self._vertical_flow_combo.currentIndexChanged.connect(self._on_vflow)
        highlighted = getattr(self._family_combo, "textHighlighted", None)
        if highlighted is not None and hasattr(highlighted, "connect"):
            with suppress(Exception):
                cast(Any, highlighted).connect(self._on_family_text)

    # ------------------------------------------------------------------
    # Change handlers
    # ------------------------------------------------------------------

    def _emit(self, key: str, value: Any) -> None:
        if not self._block:
            self._on_changed(key, value)

    def _on_wrap_toggled(self, checked: bool) -> None:
        self._emit("word_wrap", bool(checked))

    def _on_family_text(self, family: str) -> None:
        if self._block:
            return
        family = str(family or "").strip()
        if not family:
            return
        self._reload_styles(family, self._style_combo.currentText())
        self._emit("font_family", family)
        self._emit("font_style", self._style_combo.currentText())

    def _on_style(self, _idx: int) -> None:
        self._emit("font_style", self._style_combo.currentText())

    def _on_size_text(self, text: str) -> None:
        try:
            val = float(text)
        except (ValueError, TypeError):
            return
        self._emit("font_size_pt", val)

    def _on_line_height_text(self, text: str) -> None:
        try:
            val = float(text)
        except (ValueError, TypeError):
            return
        self._emit("line_height", val)

    def _on_tracking_text(self, text: str) -> None:
        try:
            val = float(text)
        except (ValueError, TypeError):
            return
        self._emit("tracking", val)

    def _on_pad_text(self, key: str, text: str) -> None:
        try:
            val = float(text)
        except (ValueError, TypeError):
            return
        self._emit(key, val)

    def _on_align(self, _idx: int) -> None:
        data = self._align_combo.currentData()
        if isinstance(data, str):
            self._emit("align", data)

    def _on_vflow(self, _idx: int) -> None:
        if self._vertical_flow_combo is None:
            return
        data = self._vertical_flow_combo.currentData()
        if isinstance(data, str):
            self._emit(
                "vertical_flow",
                normalize_vertical_flow(data, default=VERTICAL_FLOW_LEFT),
            )

    def _on_opacity(self, value: int) -> None:
        clamped = max(0, min(100, int(value)))
        self._opacity_label.setText(f"{clamped}%")
        self._color.setAlpha(round((clamped / 100.0) * 255.0))
        self._save_color()
        self._update_color_button()

    def _pick_color(self) -> None:
        from loomstroke.ui.widgets.color_picker_popup import ColorPickerPopup

        chosen = ColorPickerPopup.pick_color(self._color, self.window())
        if chosen is None or not chosen.isValid():
            return
        chosen.setAlpha(self._color.alpha())
        self._color = chosen
        self._save_color()
        self._update_color_button()

    def _save_color(self) -> None:
        rgba = [
            int(self._color.red()),
            int(self._color.green()),
            int(self._color.blue()),
            int(self._color.alpha()),
        ]
        self._emit("color_rgba", rgba)

    def _update_color_button(self) -> None:
        rgb_hex = self._color.name(QColor.NameFormat.HexRgb).upper()
        self._color_button.setText(rgb_hex)
        swatch = QColor(self._color)
        swatch.setAlpha(255)
        fg = "#000" if swatch.lightness() > 128 else "#fff"
        self._color_button.setStyleSheet(
            f"QPushButton{{background:{swatch.name()};color:{fg};padding:2px;}}"
        )

    def _update_vertical_labels(self) -> None:
        if self._align_combo.count() < 4:
            return
        if self._is_vertical:
            self._align_combo.setItemText(0, _ts("Top"))
            self._align_combo.setItemText(1, _ts("Center"))
            self._align_combo.setItemText(2, _ts("Bottom"))
            self._align_combo.setItemText(3, _ts("Justify"))
        else:
            self._align_combo.setItemText(0, _ts("Left"))
            self._align_combo.setItemText(1, _ts("Center"))
            self._align_combo.setItemText(2, _ts("Right"))
            self._align_combo.setItemText(3, _ts("Justify"))

    # ------------------------------------------------------------------
    # External sync — called by ContextToolbar.set_value()
    # ------------------------------------------------------------------

    def set_value(self, key: str, value: Any) -> None:  # noqa: PLR0912
        """Update a widget to match an externally-changed setting."""
        self._block = True
        try:
            if key == "word_wrap":
                self._wrap_check.setChecked(bool(value))
            elif key == "font_family":
                idx = self._family_combo.findText(str(value))
                if idx >= 0:
                    self._family_combo.setCurrentIndex(idx)
                self._reload_styles(str(value), self._style_combo.currentText())
            elif key == "font_style":
                idx = self._style_combo.findText(str(value))
                if idx >= 0:
                    self._style_combo.setCurrentIndex(idx)
            elif key == "font_size_pt":
                self._size_combo.setCurrentText(self._fmt_num(float(value), 1))
            elif key == "align":
                idx = self._align_combo.findData(str(value))
                if idx >= 0:
                    self._align_combo.setCurrentIndex(idx)
            elif key == "line_height":
                self._line_height_combo.setCurrentText(self._fmt_num(float(value), 2))
            elif key == "tracking":
                self._tracking_combo.setCurrentText(self._fmt_num(float(value), 1))
            elif key == "padding_top":
                self._padding_top_combo.setCurrentText(self._fmt_num(float(value), 1))
            elif key == "padding_right":
                self._padding_right_combo.setCurrentText(self._fmt_num(float(value), 1))
            elif key == "padding_bottom":
                self._padding_bottom_combo.setCurrentText(self._fmt_num(float(value), 1))
            elif key == "padding_left":
                self._padding_left_combo.setCurrentText(self._fmt_num(float(value), 1))
            elif key == "color_rgba":
                if isinstance(value, (list, tuple)) and len(value) >= 4:
                    r, g, b, a = (int(v) for v in value[:4])
                    self._color = QColor(r, g, b, a)
                    self._opacity_slider.setValue(round((a / 255.0) * 100.0))
                    self._opacity_label.setText(f"{self._opacity_slider.value()}%")
                    self._update_color_button()
            elif key == "vertical_flow" and self._vertical_flow_combo is not None:
                idx = self._vertical_flow_combo.findData(
                    normalize_vertical_flow(value, default=VERTICAL_FLOW_LEFT)
                )
                if idx >= 0:
                    self._vertical_flow_combo.setCurrentIndex(idx)
        except Exception:
            logger.debug("TextContextContent.set_value(%s) failed", key, exc_info=True)
        finally:
            self._block = False
