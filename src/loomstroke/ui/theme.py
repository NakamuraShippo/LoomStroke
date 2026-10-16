# ruff: noqa: E501
"""Application-wide visual themes."""

from __future__ import annotations

import sys
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Qt
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QAbstractSpinBox, QApplication

from loomstroke.ui.widgets.spinbox_sizing import apply_spinbox_entry_policy

_THEME_PROPERTY = "loomstroke_ai_effects_manager_theme"
_THEME_NAME_PROPERTY = "loomstroke_theme_name"
_BASE_STYLE_SHEET_PROPERTY = "loomstroke_theme_base_stylesheet"
_SPINBOX_POLICY_FILTER_PROPERTY = "loomstroke_spinbox_policy_filter"


@dataclass(frozen=True)
class _ThemeColors:
    name: str
    bg: str
    surface: str
    surface_2: str
    hover: str
    border: str
    text: str
    dim: str
    disabled: str
    accent: str
    accent_deep: str
    accent_soft: str
    input_disabled_bg: str
    button_pressed: str
    slider_groove: str
    slider_handle: str
    slider_handle_border: str


_DARK = _ThemeColors(
    name="dark",
    bg="#1e1e1e",
    surface="#252525",
    surface_2="#2a2a2a",
    hover="#333333",
    border="#3a3a3a",
    text="#e5e5e5",
    dim="#8a8a8a",
    disabled="#5a5a5a",
    accent="#a78bfa",
    accent_deep="#6d5ba3",
    accent_soft="rgba(167, 139, 250, 0.15)",
    input_disabled_bg="#202020",
    button_pressed="#202020",
    slider_groove="#2e2e2e",
    slider_handle="#ffffff",
    slider_handle_border="#c8c8c8",
)

_LIGHT = _ThemeColors(
    name="light",
    bg="#e7e9ee",
    surface="#f0f2f5",
    surface_2="#dde1e8",
    hover="#d5dae3",
    border="#b7bfcc",
    text="#242832",
    dim="#5f6877",
    disabled="#8e96a3",
    accent="#6256c7",
    accent_deep="#6d5ba3",
    accent_soft="rgba(98, 86, 199, 0.12)",
    input_disabled_bg="#e0e4eb",
    button_pressed="#cfd5df",
    slider_groove="#cbd1dc",
    slider_handle="#f7f8fa",
    slider_handle_border="#aeb7c4",
)


def apply_loomstroke_theme(app: QApplication, theme: str | None = None) -> None:
    """Apply a LoomStroke application theme to ``app``."""

    theme_name = resolve_theme_name(theme, app)
    current_sheet = app.styleSheet().strip()
    marker = f"LoomStroke {theme_name} global theme"
    if app.property(_THEME_NAME_PROPERTY) == theme_name and marker in current_sheet:
        app.setProperty(_THEME_PROPERTY, True)
        return

    colors = _LIGHT if theme_name == "light" else _DARK
    base_sheet = app.property(_BASE_STYLE_SHEET_PROPERTY)
    if not isinstance(base_sheet, str) or "LoomStroke " not in current_sheet:
        base_sheet = current_sheet
        app.setProperty(_BASE_STYLE_SHEET_PROPERTY, base_sheet)

    app.setProperty(_THEME_NAME_PROPERTY, theme_name)
    app.setPalette(_build_palette(colors))
    app.setFont(_compact_font(app.font()))
    sheet = _build_style_sheet(colors).strip()
    app.setStyleSheet(f"{base_sheet}\n\n{sheet}" if base_sheet else sheet)
    _install_spinbox_policy(app)
    app.setProperty(_THEME_PROPERTY, True)


def apply_ai_effects_manager_theme(app: QApplication) -> None:
    """Backward-compatible entry point for the original dark theme."""

    apply_loomstroke_theme(app, "dark")


class _SpinBoxPolicyFilter(QObject):
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() == QEvent.Type.ChildAdded:
            child_getter = getattr(event, "child", None)
            child = child_getter() if callable(child_getter) else None
            _apply_spinbox_policy_to_object(child)
        elif event.type() in {QEvent.Type.Polish, QEvent.Type.Show}:
            _apply_spinbox_policy_to_object(watched)
        return False


def _install_spinbox_policy(app: QApplication) -> None:
    policy_filter = app.property(_SPINBOX_POLICY_FILTER_PROPERTY)
    if not isinstance(policy_filter, _SpinBoxPolicyFilter):
        policy_filter = _SpinBoxPolicyFilter(app)
        app.installEventFilter(policy_filter)
        app.setProperty(_SPINBOX_POLICY_FILTER_PROPERTY, policy_filter)
    for widget in app.allWidgets():
        _apply_spinbox_policy_to_object(widget)


def _apply_spinbox_policy_to_object(obj: object) -> None:
    if isinstance(obj, QAbstractSpinBox):
        apply_spinbox_entry_policy(obj)
    find_children = getattr(obj, "findChildren", None)
    if callable(find_children):
        for spin in find_children(QAbstractSpinBox):
            apply_spinbox_entry_policy(spin)


def resolve_theme_name(theme: str | None, app: QApplication | None = None) -> str:
    """Return the concrete theme name for a stored user preference."""

    requested = theme.lower() if isinstance(theme, str) else "dark"
    if requested in {"dark", "light"}:
        return requested
    if requested != "system":
        return "dark"

    target_app = app or QApplication.instance()
    hints = target_app.styleHints() if isinstance(target_app, QApplication) else None
    color_scheme_getter = getattr(hints, "colorScheme", None)
    if callable(color_scheme_getter):
        with suppress(Exception):
            scheme = color_scheme_getter()
            if scheme == Qt.ColorScheme.Light:
                return "light"
            if scheme == Qt.ColorScheme.Dark:
                return "dark"
    return "dark"


def current_theme_name(app: QApplication | None = None) -> str:
    """Return the theme currently applied to the QApplication."""

    target_app = app or QApplication.instance()
    if isinstance(target_app, QApplication):
        value = target_app.property(_THEME_NAME_PROPERTY)
        if isinstance(value, str) and value in {"dark", "light"}:
            return value
    return resolve_theme_name(None, target_app if isinstance(target_app, QApplication) else None)


def svg_icon_tint_color(app: QApplication | None = None) -> QColor | None:
    """Return a theme tint for monochrome SVG icons, if one is needed."""

    if current_theme_name(app) == "light":
        return QColor("#000000")
    return None


def text_logo_filename_for_theme(theme: str | None, app: QApplication | None = None) -> str:
    """Return the text logo asset that matches the effective theme."""

    return "textlogo_br.png" if resolve_theme_name(theme, app) == "light" else "textlogo.png"


def image_asset_path(filename: str) -> Path:
    """Return the path to a root ``img`` asset in source and frozen builds."""

    for root in _image_asset_roots():
        candidate = root / "img" / filename
        if candidate.exists():
            return candidate
    return _image_asset_roots()[0] / "img" / filename


def _image_asset_roots() -> list[Path]:
    roots: list[Path] = []
    meipass = getattr(sys, "_MEIPASS", None)
    if isinstance(meipass, str) and meipass:
        roots.append(Path(meipass))
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)
    roots.append(Path(__file__).resolve().parents[3])

    unique_roots: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        resolved = root.resolve()
        if resolved not in seen:
            unique_roots.append(resolved)
            seen.add(resolved)
    return unique_roots


def _compact_font(font: QFont) -> QFont:
    themed_font = QFont(font)
    # Keep the application font point-sized. Pixel-sized fonts can make Qt
    # stylesheet hover repolish emit QFont::setPointSize(-1) warnings.
    themed_font.setPointSize(9)
    return themed_font


def _build_palette(colors: _ThemeColors) -> QPalette:
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(colors.bg))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(colors.text))
    palette.setColor(QPalette.ColorRole.Base, QColor(colors.bg))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors.surface))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors.surface))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(colors.text))
    palette.setColor(QPalette.ColorRole.Text, QColor(colors.text))
    palette.setColor(QPalette.ColorRole.Button, QColor(colors.surface_2))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors.text))
    palette.setColor(QPalette.ColorRole.BrightText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(colors.accent_deep))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#ffffff"))
    palette.setColor(QPalette.ColorRole.Link, QColor(colors.accent))

    with suppress(AttributeError):
        palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors.dim))

    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors.disabled))

    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Button,
        QColor(colors.input_disabled_bg),
    )
    palette.setColor(
        QPalette.ColorGroup.Disabled,
        QPalette.ColorRole.Base,
        QColor(colors.input_disabled_bg),
    )
    return palette


def _build_style_sheet(c: _ThemeColors) -> str:
    return f"""
/* LoomStroke {c.name} global theme */
QWidget {{ color: {c.text}; font-size: 9pt; selection-background-color: {c.accent_deep}; selection-color: #ffffff; }}
QWidget:disabled {{ color: {c.disabled}; }}
QMainWindow, QDialog, QScrollArea {{ background-color: {c.bg}; }}
QMenuBar, QMenu, QToolBar, QStatusBar, QDockWidget::title {{ background-color: {c.surface}; color: {c.text}; }}
QMenuBar {{ border-bottom: 1px solid {c.border}; padding: 0px; }}
QMenuBar::item {{ background: transparent; padding: 4px 8px; }}
QMenuBar::item:selected, QMenu::item:selected {{ background-color: {c.accent_soft}; color: {c.accent}; }}
QMenu {{ border: 1px solid {c.border}; padding: 4px; }}
QMenu::item {{ padding: 4px 24px 4px 10px; border-radius: 3px; }}
QMenu::separator {{ height: 1px; background-color: {c.border}; margin: 4px 6px; }}
QToolBar {{ border: 0px; border-bottom: 1px solid {c.border}; spacing: 3px; padding: 2px; }}
QToolBar::separator {{ background-color: {c.border}; width: 1px; height: 1px; margin: 3px; }}
QStatusBar {{ color: {c.dim}; border-top: 1px solid {c.border}; font-size: 8pt; }}
QDockWidget {{ background-color: {c.bg}; color: {c.text}; }}
QDockWidget::title {{ border: 1px solid {c.border}; border-bottom: 0px; padding: 4px 6px; font-size: 8pt; font-weight: 600; }}
QDockWidget::pane {{ border: 1px solid {c.border}; }}
QTabWidget::pane {{ background-color: {c.bg}; border: 1px solid {c.border}; }}
QTabBar::tab {{ background-color: {c.surface}; color: {c.dim}; border: 1px solid {c.border}; border-bottom: 0px; padding: 5px 10px; font-size: 8pt; }}
QTabBar::tab:selected {{ background-color: {c.bg}; color: {c.accent}; border-color: {c.accent}; }}
QTabBar::tab:hover:!selected {{ background-color: {c.hover}; color: {c.text}; }}
QGroupBox {{ border: 1px solid {c.border}; border-radius: 4px; margin-top: 14px; padding-top: 8px; color: {c.text}; }}
QGroupBox::title {{ subcontrol-origin: margin; subcontrol-position: top left; left: 8px; padding: 2px 6px; color: {c.accent}; background-color: {c.surface}; font-size: 8pt; font-weight: 600; }}
QLabel {{ color: {c.text}; }}
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ background-color: {c.surface}; color: {c.text}; border: 1px solid {c.border}; border-radius: 4px; padding: 1px 7px; min-height: 16px; }}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {{ border-color: {c.accent}; }}
QLineEdit:disabled, QTextEdit:disabled, QPlainTextEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{ background-color: {c.input_disabled_bg}; color: {c.disabled}; border-color: {c.border}; }}
QComboBox::drop-down {{ border: none; width: 18px; }}
QComboBox QAbstractItemView {{ background-color: {c.surface}; color: {c.text}; border: 1px solid {c.border}; selection-background-color: {c.accent_deep}; outline: 0; }}
QPushButton, QToolButton {{ background-color: {c.surface_2}; color: {c.text}; border: 1px solid {c.border}; border-radius: 4px; padding: 4px 8px; }}
QToolButton {{ padding: 3px; }}
QPushButton:hover, QToolButton:hover, QTreeView::item:hover, QListView::item:hover {{ background-color: {c.hover}; }}
QPushButton:pressed {{ background-color: {c.button_pressed}; }}
QPushButton:default, QToolButton:checked {{ background-color: {c.accent_soft}; color: {c.accent}; border-color: {c.accent}; }}
QPushButton:disabled, QToolButton:disabled {{ background-color: {c.input_disabled_bg}; color: {c.disabled}; border-color: {c.border}; }}
QAbstractItemView, QTreeWidget, QListWidget, QTableWidget, QTableView, QTreeView, QListView {{ background-color: {c.bg}; alternate-background-color: {c.surface}; color: {c.text}; border: 1px solid {c.border}; outline: 0; }}
QTreeView::item, QListView::item {{ padding: 3px; }}
QTreeView::item:selected, QListView::item:selected {{ background-color: {c.accent_soft}; color: {c.text}; }}
QHeaderView::section {{ background-color: {c.surface}; color: {c.dim}; border: none; border-right: 1px solid {c.border}; border-bottom: 1px solid {c.border}; padding: 4px 6px; font-size: 8pt; }}
QScrollBar:vertical {{ background-color: {c.bg}; width: 10px; margin: 0px; }}
QScrollBar:horizontal {{ background-color: {c.bg}; height: 10px; margin: 0px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{ background-color: {c.border}; border-radius: 3px; min-height: 24px; min-width: 24px; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background-color: {c.dim}; }}
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page {{ background: none; border: none; width: 0px; height: 0px; }}
QSlider::groove:horizontal, QSlider::add-page:horizontal {{ background-color: {c.slider_groove}; height: 3px; border-radius: 1px; }}
QSlider::sub-page:horizontal, QProgressBar::chunk {{ background-color: {c.accent}; border-radius: 1px; }}
QSlider::handle:horizontal {{ background-color: {c.slider_handle}; width: 8px; height: 8px; margin: -3px 0px; border-radius: 4px; border: 1px solid {c.slider_handle_border}; }}
QSlider::handle:horizontal:hover {{ border-color: {c.accent}; }}
QProgressBar {{ background-color: {c.surface}; color: {c.text}; border: 1px solid {c.border}; border-radius: 3px; text-align: center; }}
QSplitter::handle {{ background-color: {c.surface}; }}
QSplitter::handle:hover {{ background-color: {c.border}; }}
QToolTip {{ background-color: {c.surface}; color: {c.text}; border: 1px solid {c.border}; padding: 4px 6px; }}
"""


__all__ = [
    "apply_ai_effects_manager_theme",
    "apply_loomstroke_theme",
    "current_theme_name",
    "image_asset_path",
    "resolve_theme_name",
    "svg_icon_tint_color",
    "text_logo_filename_for_theme",
]
