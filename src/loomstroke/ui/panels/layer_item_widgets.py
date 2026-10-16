"""Sub-widgets used by :mod:`loomstroke.ui.panels.layer_panel` rows."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QToolButton, QWidget

_LAYER_ROW_ICON_DIR = Path(__file__).resolve().parent.parent / "resources" / "layer_icons"
_LAYER_ROW_ICON_MAP = {
    "visibility": "layer_visibility.svg",
    "alpha_lock": "layer_alpha_lock.svg",
    "lock": "layer_lock.svg",
}
_LAYER_ROW_ICON_CACHE: dict[str, QIcon | None] = {}


def clear_layer_row_icon_cache() -> None:
    _LAYER_ROW_ICON_CACHE.clear()


class LayerRowControlGroup(QWidget):
    """Owns the visibility, alpha-lock, and lock row buttons."""

    def __init__(
        self,
        *,
        visible: bool,
        alpha_locked: bool,
        locked: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._hovered = False
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.alpha_lock_box = self._make_button(alpha_locked, self.tr("Toggle alpha lock"))
        self.lock_box = self._make_button(locked, self.tr("Toggle layer lock"))
        self.visibility_box = self._make_button(visible, self.tr("Toggle layer visibility"))

        layout.addWidget(self.alpha_lock_box)
        layout.addWidget(self.lock_box)
        layout.addWidget(self.visibility_box)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        self.update_styles()

    def _make_button(self, checked: bool, tooltip: str) -> QToolButton:
        button = QToolButton(self)
        button.setCheckable(True)
        button.setChecked(checked)
        button.setToolTip(tooltip)
        button.setFixedSize(QSize(24, 24))
        button.setAutoRaise(True)
        return button

    def set_hovered(self, hovered: bool) -> None:
        self._hovered = hovered
        self.update_styles()

    def set_controls_visible(self, visible: bool) -> None:
        self.visibility_box.setVisible(visible)
        self.lock_box.setVisible(visible)
        self.alpha_lock_box.setVisible(visible)

    def update_styles(self) -> None:
        self._update_icons()
        self.visibility_box.setStyleSheet(
            self._visibility_button_style(self.visibility_box.isChecked())
        )
        self.alpha_lock_box.setStyleSheet(
            self._state_button_style(
                active=self.alpha_lock_box.isChecked(),
                active_color="#66bbff",
                inactive_color="#666666",
                bold=True,
            )
        )
        self.lock_box.setStyleSheet(
            self._state_button_style(
                active=self.lock_box.isChecked(),
                active_color="#ff9900",
                inactive_color="#666666",
                bold=False,
            )
        )

    def refresh_theme_icons(self) -> None:
        self._update_icons()

    def _update_icons(self) -> None:
        visible = self.visibility_box.isChecked()
        alpha_locked = self.alpha_lock_box.isChecked()
        locked = self.lock_box.isChecked()
        self._set_icon(
            self.visibility_box,
            icon_key="visibility",
            fallback_text="\u25c9" if visible else "\u25ce",
            show=(not visible) or self._hovered,
        )
        self._set_icon(
            self.alpha_lock_box,
            icon_key="alpha_lock",
            fallback_text="\u03b1",
            show=alpha_locked or self._hovered,
        )
        self._set_icon(
            self.lock_box,
            icon_key="lock",
            fallback_text="\u2588" if locked else "\u2591",
            show=locked or self._hovered,
        )

    @staticmethod
    def _row_control_icon(icon_key: str) -> QIcon | None:
        from loomstroke.ui.icon_loader import load_svg_icon  # noqa: PLC0415

        if icon_key not in _LAYER_ROW_ICON_CACHE:
            svg_name = _LAYER_ROW_ICON_MAP[icon_key]
            _LAYER_ROW_ICON_CACHE[icon_key] = load_svg_icon(
                _LAYER_ROW_ICON_DIR / svg_name,
                QSize(14, 14),
            )
        return _LAYER_ROW_ICON_CACHE[icon_key]

    def _set_icon(
        self,
        button: QToolButton,
        *,
        icon_key: str,
        fallback_text: str,
        show: bool,
    ) -> None:
        if not show:
            button.setIcon(QIcon())
            button.setText("")
            return
        icon = self._row_control_icon(icon_key)
        if icon is None:
            button.setIcon(QIcon())
            button.setText(fallback_text)
            return
        button.setText("")
        button.setIcon(icon)
        button.setIconSize(QSize(14, 14))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)

    def _visibility_button_style(self, visible: bool) -> str:
        if not visible:
            return (
                "QToolButton { color: #555555; background: transparent; border: none; }"
                " QToolButton:hover { color: #888888; background: transparent; border: none; }"
                " QToolButton:pressed { background: transparent; border: none; }"
            )
        color = "#ffffff" if self._hovered else "transparent"
        return (
            f"QToolButton {{ color: {color}; background: transparent; border: none; }}"
            " QToolButton:hover { color: #ffffff; background: transparent; border: none; }"
            " QToolButton:pressed { background: transparent; border: none; }"
        )

    def _state_button_style(
        self,
        *,
        active: bool,
        active_color: str,
        inactive_color: str,
        bold: bool,
    ) -> str:
        if active:
            font_weight = " font-weight: bold;" if bold else ""
            return (
                f"QToolButton {{ color: {active_color}; background: transparent;"
                f" border: none;{font_weight} }}"
                f" QToolButton:hover {{ color: {active_color};"
                " background: transparent; border: none; }"
                " QToolButton:pressed { background: transparent; border: none; }"
            )
        color = inactive_color if self._hovered else "transparent"
        return (
            f"QToolButton {{ color: {color}; background: transparent; border: none; }}"
            f" QToolButton:hover {{ color: {inactive_color};"
            " background: transparent; border: none; }"
            " QToolButton:pressed { background: transparent; border: none; }"
        )


class LayerGenerateButton(QToolButton):
    """Generate button shown on AI Effect layer rows."""

    def __init__(
        self,
        *,
        icon_path: Path,
        accent_hex: str,
        accent_dim: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._icon_path = icon_path
        self._accent_hex = accent_hex
        self._accent_dim = accent_dim
        self.setObjectName("aiEffectGenerateButton")
        self.refresh_icon()
        self.setFixedSize(QSize(20, 20))
        self.setAutoRaise(True)
        self.setToolTip(self.tr("Generate (AI Effect)"))
        self.setVisible(False)
        self.setStyleSheet(self._stylesheet())

    def refresh_icon(self) -> None:
        from loomstroke.ui.icon_loader import load_svg_icon  # noqa: PLC0415

        ai_generate_icon = load_svg_icon(self._icon_path)
        if ai_generate_icon is not None:
            self.setText("")
            self.setIcon(ai_generate_icon)
            self.setIconSize(QSize(14, 14))
            self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        else:
            self.setIcon(QIcon())
            self.setText("\u25b6")

    def configure(self, *, layer_id: str, is_ai_effect: bool, state: str | None) -> None:
        del layer_id
        self.setVisible(is_ai_effect)
        if not is_ai_effect:
            return
        self.setEnabled(state not in {"queued", "generating"})

    def _stylesheet(self) -> str:
        return (
            "QToolButton#aiEffectGenerateButton {"
            f" color: #ffffff; background-color: {self._accent_hex};"
            f" border: 1px solid {self._accent_hex};"
            " border-radius: 2px; font-weight: bold;"
            " padding: 0px;"
            " }"
            " QToolButton#aiEffectGenerateButton:hover {"
            " background-color: #b59cff;"
            " border-color: #b59cff;"
            " }"
            " QToolButton#aiEffectGenerateButton:pressed {"
            f" background-color: {self._accent_dim};"
            " }"
            " QToolButton#aiEffectGenerateButton:disabled {"
            " color: #5a5a5a; background-color: #2e2e2e;"
            " border-color: #3a3a3a;"
            " }"
        )


class LayerProgressOverlay(QFrame):
    """Three-pixel AI Effect progress overlay pinned to a row bottom edge."""

    def __init__(self, *, accent_hex: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("aiEffectProgressOverlay")
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setStyleSheet(
            "QFrame#aiEffectProgressOverlay { background-color: #0a0a0a; border: none; }"
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setVisible(False)
        self.fill = QFrame(self)
        self.fill.setObjectName("aiEffectProgressFill")
        self.fill.setStyleSheet(
            "QFrame#aiEffectProgressFill {" f" background-color: {accent_hex}; border: none;" " }"
        )

    def reposition_to_host(self, host: QWidget) -> None:
        h = host.height()
        w = host.width()
        if h <= 3 or w <= 0:
            return
        self.setGeometry(0, h - 3, w, 3)

    def apply_progress_value(self, progress: float | None, host: QWidget) -> None:
        self.reposition_to_host(host)
        width = self.width()
        if width <= 0:
            return
        if progress is not None:
            clamped = max(0.0, min(1.0, float(progress)))
            fill_width = max(1, int(width * clamped)) if clamped > 0 else 0
            self.fill.setGeometry(0, 0, fill_width, 3)
            self.fill.setVisible(fill_width > 0)
            return
        fill_width = max(1, int(width * 0.3))
        offset = int(width * 0.1)
        self.fill.setGeometry(offset, 0, fill_width, 3)
        self.fill.setVisible(True)
