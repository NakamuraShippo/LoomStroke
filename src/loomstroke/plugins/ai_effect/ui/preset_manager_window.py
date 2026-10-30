"""Preset Manager window — AI Effects Manager (Phase 6-e).

A PySide6 reproduction of the ``preset_manager_final.html`` mockup for
browsing installed ``.lsfx`` presets and applying them to the current
canvas. Scope of this initial phase:

Working (interactive):
- Open / close / raise idempotency
- Import: QFileDialog -> library.import_lsfx -> refresh
- + New: opens the Effect Editor through the supplied opener
- Double-click a preset card: routes through the applier to add an
  AI Effect Layer to the canvas
- Preset display: card grid, detail list, or compact no-thumbnail cards
- Search input
- Sidebar filters (category / Gateway / source)
- Sort dropdown
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QDrag, QDragEnterEvent, QDropEvent, QPainter, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStatusBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.plugins.ai_effect.settings import AIEffectPreferences, default_preferences_path
from loomstroke.plugins.ai_effect.ui.preset_manager_cards import PresetCardFactory
from loomstroke.plugins.ai_effect.ui.preset_manager_filter import (
    PresetFilterEngine,
    PresetFilterState,
)
from loomstroke.ui._dock_safe import is_valid
from loomstroke.ui.ai_effect_preset_drag import make_ai_effect_preset_mime_data
from loomstroke.ui.theme import current_theme_name
from loomstroke.ui.translations import translate_with_fallback

logger = logging.getLogger(__name__)


def _tr(text: str) -> str:
    return translate_with_fallback("PresetManagerWindow", text)


# ---------------------------------------------------------------------------
# Palette & stylesheet (translated from preset_manager_final.html CSS)
# ---------------------------------------------------------------------------

_BG = "#1e1e1e"
_SURFACE = "#252525"
_SURFACE_2 = "#2a2a2a"
_SURFACE_HOVER = "#333333"
_BORDER = "#3a3a3a"
_TEXT = "#e5e5e5"
_TEXT_DIM = "#8a8a8a"
_ACCENT = "#a78bfa"
_ACCENT_SOFT = "rgba(167, 139, 250, 0.15)"
_BUILTIN = "#ffd700"
_GATEWAY_CHIP = "rgba(0, 0, 0, 0.55)"

_STYLE_SHEET = f"""
QWidget#PresetManagerWindow, QWidget#PresetManagerCentral {{
    background-color: {_BG};
    color: {_TEXT};
}}
QScrollArea#PresetManagerSidebarScroll {{
    border: none;
    background-color: {_SURFACE};
}}

/* Toolbar */
QFrame#PresetManagerToolbar {{
    background-color: {_SURFACE};
    border-bottom: 1px solid {_BORDER};
}}
QFrame#PresetManagerSearchBox {{
    background-color: {_SURFACE_2};
    border: 1px solid {_BORDER};
    border-radius: 6px;
}}
QLineEdit#PresetManagerSearchInput {{
    background: transparent;
    border: none;
    color: {_TEXT};
    padding: 4px 6px;
}}
QLineEdit#PresetManagerSearchInput:focus {{
    color: {_TEXT};
}}
QLabel#PresetManagerSearchIcon {{
    color: {_TEXT_DIM};
    padding-left: 8px;
}}
QToolButton#PresetManagerViewToggleBtn {{
    background-color: {_SURFACE_2};
    border: 1px solid {_BORDER};
    color: {_TEXT};
    padding: 4px 10px;
}}
QToolButton#PresetManagerViewToggleBtn:checked {{
    background-color: {_ACCENT_SOFT};
    color: {_ACCENT};
    border-color: {_ACCENT};
}}
QPushButton#PresetManagerToolbarBtn {{
    background-color: {_SURFACE_2};
    border: 1px solid {_BORDER};
    color: {_TEXT};
    padding: 5px 12px;
    border-radius: 4px;
}}
QPushButton#PresetManagerToolbarBtn:hover {{
    background-color: {_SURFACE_HOVER};
}}
QPushButton#PresetManagerToolbarBtnPrimary {{
    background-color: {_ACCENT};
    border: none;
    color: #1a1a1a;
    padding: 5px 12px;
    border-radius: 4px;
    font-weight: bold;
}}
QPushButton#PresetManagerToolbarBtnPrimary:hover {{
    background-color: #b69cff;
}}

/* Sidebar */
QFrame#PresetManagerSidebar {{
    background-color: {_SURFACE};
    border-right: 1px solid {_BORDER};
}}
QLabel#PresetManagerSidebarSectionTitle {{
    color: {_TEXT_DIM};
    font-size: 10px;
    font-weight: bold;
    padding: 6px 12px 3px 12px;
    text-transform: uppercase;
    letter-spacing: 1px;
}}
QFrame#PresetManagerSidebarSectionHeader {{
    background: transparent;
}}
QToolButton#PresetManagerSidebarIconButton {{
    background: transparent;
    border: none;
    color: {_TEXT_DIM};
    padding: 0px;
    min-width: 18px;
    min-height: 18px;
}}
QToolButton#PresetManagerSidebarIconButton:hover {{
    color: {_TEXT};
}}
QFrame#PresetManagerSidebarItem {{
    background: transparent;
    border-radius: 4px;
    margin: 0 6px;
}}
QFrame#PresetManagerSidebarItem:hover {{
    background-color: {_SURFACE_HOVER};
}}
QFrame#PresetManagerSidebarItem[active="true"] {{
    background-color: {_ACCENT_SOFT};
}}
QLabel#PresetManagerSidebarItemLabel {{
    color: {_TEXT};
}}
QFrame#PresetManagerSidebarItem[active="true"] QLabel#PresetManagerSidebarItemLabel {{
    color: {_ACCENT};
}}
QLabel#PresetManagerSidebarItemCount {{
    color: {_TEXT_DIM};
    font-size: 11px;
}}
QFrame#PresetManagerSidebarDivider {{
    background-color: {_BORDER};
    max-height: 1px;
    min-height: 1px;
    margin: 6px 12px;
}}

/* Content area */
QFrame#PresetManagerContentHeader {{
    background-color: {_SURFACE};
    border-bottom: 1px solid {_BORDER};
}}
QLabel#PresetManagerContentTitle {{
    color: {_TEXT};
    font-size: 14px;
    font-weight: bold;
}}
QLabel#PresetManagerContentCount {{
    color: {_TEXT_DIM};
    font-size: 11px;
}}
QLabel#PresetManagerSortLabel {{
    color: {_TEXT_DIM};
    font-size: 11px;
}}
QComboBox#PresetManagerSortSelect {{
    background-color: {_SURFACE_2};
    color: {_TEXT};
    border: 1px solid {_BORDER};
    border-radius: 4px;
    padding: 3px 8px;
}}
QScrollArea#PresetManagerGridScroll {{
    border: none;
    background-color: {_BG};
}}

/* Preset card */
QFrame#PresetManagerCard {{
    background-color: {_SURFACE_2};
    border: 1px solid {_BORDER};
    border-radius: 6px;
}}
QFrame#PresetManagerCard:hover {{
    background-color: {_SURFACE_HOVER};
    border-color: {_ACCENT};
}}
QLabel#PresetManagerCardName {{
    color: {_TEXT};
    font-weight: bold;
    font-size: 12px;
}}
QLabel#PresetManagerCardMeta {{
    color: {_TEXT_DIM};
    font-size: 10px;
}}
QFrame#PresetManagerListRow, QFrame#PresetManagerCompactCard {{
    background-color: {_SURFACE_2};
    border: 1px solid {_BORDER};
    border-radius: 6px;
}}
QFrame#PresetManagerListRow:hover, QFrame#PresetManagerCompactCard:hover {{
    background-color: {_SURFACE_HOVER};
    border-color: {_ACCENT};
}}
QLabel#PresetManagerListName, QLabel#PresetManagerCompactName {{
    color: {_TEXT};
    font-weight: bold;
    font-size: 12px;
}}
QLabel#PresetManagerListMeta, QLabel#PresetManagerCompactMeta {{
    color: {_TEXT_DIM};
    font-size: 10px;
}}
QLabel#PresetManagerListDescription {{
    color: {_TEXT_DIM};
    font-size: 11px;
}}
QLabel#PresetManagerCardBuiltinChip {{
    background-color: rgba(255, 215, 0, 0.85);
    color: #1a1a1a;
    font-size: 9px;
    font-weight: bold;
    padding: 2px 5px;
    border-radius: 3px;
}}
QLabel#PresetManagerCardGatewayChip {{
    background-color: {_GATEWAY_CHIP};
    color: {_TEXT};
    font-size: 9px;
    padding: 2px 5px;
    border-radius: 3px;
}}
QLabel#PresetManagerCardPlaceholder {{
    color: rgba(255, 255, 255, 0.2);
    font-size: 32px;
    font-weight: bold;
}}
"""


_LIGHT_STYLE_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (_BG, "#e7e9ee"),
    (_SURFACE, "#f0f2f5"),
    (_SURFACE_2, "#ffffff"),
    (_SURFACE_HOVER, "#e3e7ee"),
    (_BORDER, "#b7bfcc"),
    (_TEXT, "#242832"),
    (_TEXT_DIM, "#5f6877"),
    (_ACCENT, "#6256c7"),
    (_ACCENT_SOFT, "rgba(98, 86, 199, 0.12)"),
    (_GATEWAY_CHIP, "rgba(255, 255, 255, 0.78)"),
    ("#b69cff", "#5146b8"),
    ("rgba(255, 255, 255, 0.2)", "rgba(36, 40, 50, 0.22)"),
)


def _style_sheet_for_theme(theme_name: str) -> str:
    if theme_name != "light":
        return _STYLE_SHEET
    sheet = _STYLE_SHEET
    for dark_value, light_value in _LIGHT_STYLE_REPLACEMENTS:
        sheet = sheet.replace(dark_value, light_value)
    sheet = sheet.replace(
        "QPushButton#PresetManagerToolbarBtnPrimary {\n"
        "    background-color: #6256c7;\n"
        "    border: none;\n"
        "    color: #1a1a1a;",
        "QPushButton#PresetManagerToolbarBtnPrimary {\n"
        "    background-color: #6256c7;\n"
        "    border: none;\n"
        "    color: #ffffff;",
    )
    return sheet


def _style_sheet_for_current_theme() -> str:
    return _style_sheet_for_theme(current_theme_name())


# ---------------------------------------------------------------------------
# Card widget
# ---------------------------------------------------------------------------


_CARD_THUMB_W = 200
_CARD_THUMB_H = 140
_CARD_W = _CARD_THUMB_W + 2
_COMPACT_CARD_W = 176
_COMPACT_CARD_H = 68
_GRID_MARGIN = 16
_GRID_SPACING = 16
_VIEW_GRID = "grid"
_VIEW_LIST = "list"
_VIEW_COMPACT = "compact"


# Category -> gradient color pair (approximation of mockup's per-category thumbs).
_CATEGORY_COLORS: dict[str, tuple[str, str]] = {
    "analysis": ("#3a6073", "#16222a"),
    "generate": ("#654ea3", "#eaafc8"),
    "convert": ("#0f9b0f", "#000428"),
    "other": ("#555555", "#222222"),
}

_BUILTIN_CATEGORY_ENTRIES: tuple[tuple[str, str, str], ...] = (
    ("analysis", "🔬", "Analysis"),
    ("generate", "✧", "Generate"),
    ("convert", "⇄", "Convert"),
)
_BUILTIN_SOURCE_ENTRIES: tuple[tuple[str, str, str], ...] = (
    ("builtin", "★", "Built-in"),
    ("user", "✎", "User"),
)
_BUILTIN_GATEWAY_ENTRIES: tuple[tuple[str, str, str], ...] = (
    ("comfyui", "🖥", "ComfyUI"),
    ("nanobanana", "🍌", "NanoBanana"),
    ("gptimage2", "G2", "GPT Image 2"),
)
_RESERVED_SOURCE_VALUES = {"builtin", "user", "imported"}
_MAX_GATEWAY_ICON_CHARS = 4


def _clean_sidebar_label(label: str) -> str:
    """Collapse whitespace without changing the user's casing."""
    return " ".join(str(label or "").strip().split())


def _normalise_sidebar_value(label: str) -> str:
    """Canonical value used in item ids and filter comparisons."""
    return _clean_sidebar_label(label).lower()


def _dedupe_sidebar_labels(labels: Any) -> list[str]:
    """Return non-empty labels, deduplicated by normalized value."""
    if not isinstance(labels, list):
        return []
    seen: set[str] = set()
    cleaned: list[str] = []
    for raw in labels:
        label = _clean_sidebar_label(str(raw))
        value = _normalise_sidebar_value(label)
        if not value or value in seen:
            continue
        seen.add(value)
        cleaned.append(label)
    return cleaned


def _clean_gateway_icon(icon: str) -> str:
    """Keep sidebar icons compact; empty means "use the built-in default"."""
    clean = _clean_sidebar_label(icon)
    if len(clean) > _MAX_GATEWAY_ICON_CHARS:
        return clean[:_MAX_GATEWAY_ICON_CHARS]
    return clean


def _normalise_gateway_icons(raw: Any) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    icons: dict[str, str] = {}
    for key, value in raw.items():
        gateway_id = _normalise_sidebar_value(str(key))
        icon = _clean_gateway_icon(str(value))
        if gateway_id and icon:
            icons[gateway_id] = icon
    return icons


def _preset_display_name(preset: Any) -> str:
    return str(getattr(preset, "name", "") or getattr(preset, "preset_id", ""))


def _preset_meta_parts(preset: Any) -> list[str]:
    parts: list[str] = []
    category = str(getattr(preset, "category", "") or "").strip()
    gateway_id = str(getattr(preset, "gateway_id", "") or "").strip()
    source = str(getattr(preset, "source", "") or "").strip()
    if category:
        parts.append(category)
    if gateway_id:
        parts.append(gateway_id)
    if source == "builtin":
        parts.append("Built-in")
    elif source:
        parts.append(source)
    return parts


def _category_gradient(category: str) -> tuple[QColor, QColor]:
    pair = _CATEGORY_COLORS.get(category, _CATEGORY_COLORS["other"])
    return QColor(pair[0]), QColor(pair[1])


class _PresetDraggableFrame(QFrame):
    """QFrame base that can start an AI Effect preset drag."""

    def _init_preset_drag(self) -> None:
        self._drag_start_pos: Any = None

    def _preset_drag_mime_data(self) -> Any:
        return make_ai_effect_preset_mime_data(str(getattr(self, "_preset_id", "")))

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        self._drag_start_pos = None
        button = getattr(event, "button", None)
        if callable(button) and button() == Qt.MouseButton.LeftButton:
            self._drag_start_pos = self._event_pos(event)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        preset_id = str(getattr(self, "_preset_id", "") or "")
        if not preset_id or self._drag_start_pos is None:
            super().mouseMoveEvent(event)
            return
        buttons = getattr(event, "buttons", None)
        if callable(buttons) and not (buttons() & Qt.MouseButton.LeftButton):
            self._drag_start_pos = None
            super().mouseMoveEvent(event)
            return
        current_pos = self._event_pos(event)
        if current_pos is None:
            super().mouseMoveEvent(event)
            return
        if (
            current_pos - self._drag_start_pos
        ).manhattanLength() < QApplication.startDragDistance():
            super().mouseMoveEvent(event)
            return
        self._drag_start_pos = None
        drag = QDrag(self)
        drag.setMimeData(self._preset_drag_mime_data())
        drag.exec(Qt.DropAction.CopyAction)
        event.accept()

    @staticmethod
    def _event_pos(event: Any) -> Any:
        position = getattr(event, "position", None)
        if callable(position):
            return position().toPoint()
        pos = getattr(event, "pos", None)
        if callable(pos):
            return pos()
        return None


class _PresetItemFrame(_PresetDraggableFrame):
    """Base interaction contract for non-grid preset presentations."""

    apply_requested = Signal(str)
    edit_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, preset: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._preset_id = str(getattr(preset, "preset_id", ""))
        self._is_builtin = str(getattr(preset, "source", "user")) == "builtin"
        self._preset_name = _preset_display_name(preset) or self._preset_id
        self._init_preset_drag()
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseDoubleClickEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        if self._preset_id:
            self.edit_requested.emit(self._preset_id)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        if not self._preset_id:
            return
        menu = QMenu(self)
        apply_action = menu.addAction(_tr("Add AI Effect Layer"))
        edit_action = menu.addAction(_tr("Edit..."))
        delete_action: Any = None
        if not self._is_builtin:
            menu.addSeparator()
            delete_action = menu.addAction(_tr("Delete..."))
        chosen = menu.exec(event.globalPos())
        if chosen is apply_action:
            self.apply_requested.emit(self._preset_id)
        elif chosen is edit_action:
            self.edit_requested.emit(self._preset_id)
        elif delete_action is not None and chosen is delete_action:
            self.delete_requested.emit(self._preset_id)


class _PresetCard(_PresetDraggableFrame):
    """One preset tile in the Manager grid.

    Three user actions, all keyed by ``preset_id``:

    - **Double-click** → :attr:`edit_requested` — opens the Effect
      Editor pre-loaded with this preset.
    - **Right-click "Add AI Effect Layer"** → :attr:`apply_requested`
      — instantiates the preset as an AI Effect Layer on the canvas.
    - **Right-click "Delete..."** → :attr:`delete_requested` — removes
      the preset from the user library (skipped for builtins).

    The card itself never knows what each action does — it just emits.
    The parent (:class:`PresetManagerWindow`) wires each signal to
    the appropriate host callback.
    """

    apply_requested = Signal(str)
    edit_requested = Signal(str)
    delete_requested = Signal(str)

    def __init__(self, preset: Any, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._preset_id = str(getattr(preset, "preset_id", ""))
        # Builtin source presets are read-only on disk — Delete must be
        # hidden / no-op for them. Captured at card-build time so the
        # context menu doesn't have to re-look up the preset object.
        self._is_builtin = str(getattr(preset, "source", "user")) == "builtin"
        self._preset_name = str(getattr(preset, "name", "") or self._preset_id)
        self._init_preset_drag()
        self.setObjectName("PresetManagerCard")
        self.setFixedSize(_CARD_W, _CARD_THUMB_H + 64)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        # Thumb area — built as a QFrame so corner chips can be absolute.
        thumb_container = QFrame(self)
        thumb_container.setFixedSize(_CARD_THUMB_W, _CARD_THUMB_H)
        thumb_container.setFrameShape(QFrame.Shape.NoFrame)
        layout.addWidget(thumb_container)

        self._thumb_label = QLabel(thumb_container)
        self._thumb_label.setGeometry(0, 0, _CARD_THUMB_W, _CARD_THUMB_H)
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._install_thumbnail(preset)

        # Built-in chip (top-left)
        source = str(getattr(preset, "source", "user"))
        if source == "builtin":
            chip = QLabel(f"★ {_tr('Built-in')}", thumb_container)
            chip.setObjectName("PresetManagerCardBuiltinChip")
            chip.adjustSize()
            chip.move(8, 8)
            chip.raise_()

        # Gateway chip (top-right)
        gateway_id = str(getattr(preset, "gateway_id", "") or "")
        if gateway_id:
            gchip = QLabel(gateway_id, thumb_container)
            gchip.setObjectName("PresetManagerCardGatewayChip")
            gchip.adjustSize()
            gchip.move(
                _CARD_THUMB_W - gchip.width() - 8,
                8,
            )
            gchip.raise_()

        # Info area below the thumb
        info = QWidget(self)
        info_layout = QVBoxLayout(info)
        info_layout.setContentsMargins(10, 8, 10, 10)
        info_layout.setSpacing(2)

        name = str(getattr(preset, "name", "") or getattr(preset, "preset_id", ""))
        name_label = QLabel(name, info)
        name_label.setObjectName("PresetManagerCardName")
        name_label.setWordWrap(True)
        info_layout.addWidget(name_label)

        category = str(getattr(preset, "category", "") or "")
        meta_label = QLabel(category, info)
        meta_label.setObjectName("PresetManagerCardMeta")
        info_layout.addWidget(meta_label)

        layout.addWidget(info)

    def _install_thumbnail(self, preset: Any) -> None:
        preview = getattr(preset, "preview_image", None)
        icon = getattr(preset, "icon_image", None)
        category = str(getattr(preset, "category", "") or "other")

        if isinstance(preview, (bytes, bytearray)) and preview:
            pm = QPixmap()
            if pm.loadFromData(bytes(preview)) and not pm.isNull():
                self._thumb_label.setPixmap(
                    pm.scaled(
                        _CARD_THUMB_W,
                        _CARD_THUMB_H,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                return
        if isinstance(icon, (bytes, bytearray)) and icon:
            pm = QPixmap()
            if pm.loadFromData(bytes(icon)) and not pm.isNull():
                self._thumb_label.setPixmap(
                    pm.scaled(
                        _CARD_THUMB_W,
                        _CARD_THUMB_H,
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                return
        # Category placeholder: paint a gradient with a big "AI" glyph
        pm = QPixmap(_CARD_THUMB_W, _CARD_THUMB_H)
        pm.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pm)
        from PySide6.QtGui import QLinearGradient  # noqa: PLC0415

        c1, c2 = _category_gradient(category)
        grad = QLinearGradient(0, 0, _CARD_THUMB_W, _CARD_THUMB_H)
        grad.setColorAt(0.0, c1)
        grad.setColorAt(1.0, c2)
        painter.fillRect(pm.rect(), grad)
        painter.setPen(QColor(255, 255, 255, 60))
        font = painter.font()
        font.setPointSize(32)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(pm.rect(), int(Qt.AlignmentFlag.AlignCenter), "AI")
        painter.end()
        self._thumb_label.setPixmap(pm)

    def mouseDoubleClickEvent(self, event: Any) -> None:  # noqa: N802 — Qt naming
        """Double-click → open the Effect Editor pre-loaded with this
        preset (was "Apply" pre-merge; now matches the user's
        request to make double-click the most-discoverable Edit
        affordance and right-click the way to add a layer)."""
        if self._preset_id:
            self.edit_requested.emit(self._preset_id)
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event: Any) -> None:  # noqa: N802 — Qt naming
        """Right-click → Add AI Effect Layer / Edit / Delete menu.

        - **Add AI Effect Layer**: instantiates the preset as a layer
          (the previous double-click behaviour). Always available.
        - **Edit...**: same as double-click, kept here so authors can
          discover the keyboard-free path.
        - **Delete...**: hidden for builtin presets (the library
          refuses to delete those anyway); confirmed via dialog
          before firing the signal.
        """
        if not self._preset_id:
            return
        menu = QMenu(self)
        apply_action = menu.addAction(_tr("Add AI Effect Layer"))
        edit_action = menu.addAction(_tr("Edit..."))
        delete_action: Any = None
        if not self._is_builtin:
            menu.addSeparator()
            delete_action = menu.addAction(_tr("Delete..."))
        chosen = menu.exec(event.globalPos())
        if chosen is apply_action:
            self.apply_requested.emit(self._preset_id)
        elif chosen is edit_action:
            self.edit_requested.emit(self._preset_id)
        elif delete_action is not None and chosen is delete_action:
            self.delete_requested.emit(self._preset_id)


class _PresetListRow(_PresetItemFrame):
    """Detail-list presentation for one preset."""

    def __init__(self, preset: Any, parent: QWidget | None = None) -> None:
        super().__init__(preset, parent)
        self.setObjectName("PresetManagerListRow")
        self.setMinimumHeight(68)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(14)

        text_box = QWidget(self)
        text_layout = QVBoxLayout(text_box)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)

        name_label = QLabel(self._preset_name, text_box)
        name_label.setObjectName("PresetManagerListName")
        name_label.setToolTip(self._preset_name)
        text_layout.addWidget(name_label)

        description = self._preset_description(preset)
        description_label = QLabel(description, text_box)
        description_label.setObjectName("PresetManagerListDescription")
        description_label.setToolTip(description)
        text_layout.addWidget(description_label)

        layout.addWidget(text_box, stretch=1)

        meta_label = QLabel("  •  ".join(_preset_meta_parts(preset)), self)
        meta_label.setObjectName("PresetManagerListMeta")
        meta_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(meta_label)

    @staticmethod
    def _preset_description(preset: Any) -> str:
        manifest = getattr(preset, "manifest", None)
        if isinstance(manifest, dict):
            metadata = manifest.get("metadata")
            if isinstance(metadata, dict):
                description = str(metadata.get("description", "") or "").strip()
                if description:
                    return description
        return "  •  ".join(_preset_meta_parts(preset))


class _PresetCompactCard(_PresetItemFrame):
    """Small no-thumbnail card presentation for one preset."""

    def __init__(self, preset: Any, parent: QWidget | None = None) -> None:
        super().__init__(preset, parent)
        self.setObjectName("PresetManagerCompactCard")
        self.setFixedSize(_COMPACT_CARD_W, _COMPACT_CARD_H)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(4)

        name_label = QLabel(self._preset_name, self)
        name_label.setObjectName("PresetManagerCompactName")
        name_label.setToolTip(self._preset_name)
        layout.addWidget(name_label)

        meta_label = QLabel("  •  ".join(_preset_meta_parts(preset)), self)
        meta_label.setObjectName("PresetManagerCompactMeta")
        meta_label.setToolTip(meta_label.text())
        layout.addWidget(meta_label)

        layout.addStretch(1)


# ---------------------------------------------------------------------------
# Sidebar item
# ---------------------------------------------------------------------------


class _SidebarItem(QFrame):
    """Icon + label + count row used by every sidebar category / Gateway /
    source entry. Emits either a filter click or, for user-added rows,
    a delete request keyed by the same item id."""

    clicked = Signal(str)
    delete_requested = Signal(str)
    edit_requested = Signal(str)

    def __init__(
        self,
        icon_text: str,
        label: str,
        count: int | None = None,
        *,
        item_id: str = "",
        active: bool = False,
        deletable: bool = False,
        editable: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._item_id = item_id
        self.setObjectName("PresetManagerSidebarItem")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("active", active)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 5, 10, 5)
        layout.setSpacing(8)

        icon_label = QLabel(icon_text, self)
        icon_label.setFixedWidth(16)
        self._icon_label = icon_label
        layout.addWidget(icon_label)

        text_label = QLabel(label, self)
        text_label.setObjectName("PresetManagerSidebarItemLabel")
        layout.addWidget(text_label)
        layout.addStretch(1)

        # Always create the count label so ``set_count`` can update it
        # later — the badge auto-hides when the count is 0 / None to
        # match the original "no badge for empty bucket" UX.
        self._count_label = QLabel("", self)
        self._count_label.setObjectName("PresetManagerSidebarItemCount")
        layout.addWidget(self._count_label)
        self.set_count(count)

        if editable:
            edit_button = QToolButton(self)
            edit_button.setObjectName("PresetManagerSidebarIconButton")
            edit_button.setText("✎")
            edit_button.setFixedSize(18, 18)
            edit_button.setToolTip(_tr("Change icon"))
            edit_button.clicked.connect(lambda: self.edit_requested.emit(self._item_id))
            layout.addWidget(edit_button)

        if deletable:
            delete_button = QToolButton(self)
            delete_button.setObjectName("PresetManagerSidebarIconButton")
            delete_button.setText("x")
            delete_button.setFixedSize(18, 18)
            delete_button.setToolTip(_tr("Remove from sidebar"))
            delete_button.clicked.connect(lambda: self.delete_requested.emit(self._item_id))
            layout.addWidget(delete_button)

    def set_count(self, count: int | None) -> None:
        """Update the trailing count badge. ``None`` / ``0`` hides it."""
        if count is None or count <= 0:
            self._count_label.setText("")
            self._count_label.setVisible(False)
        else:
            self._count_label.setText(str(count))
            self._count_label.setVisible(True)

    def set_active(self, active: bool) -> None:
        self.setProperty("active", active)
        # Force stylesheet reapplication for the property-based selector.
        self.style().unpolish(self)
        self.style().polish(self)

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802 — Qt naming
        if event.button() == Qt.MouseButton.LeftButton and self._item_id:
            self.clicked.emit(self._item_id)
        super().mousePressEvent(event)


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------


class PresetManagerWindow(QWidget):
    """AI Effects Manager — preset browser window."""

    def __init__(
        self,
        library: Any,
        applier: Callable[[str], str | None] | None,
        editor_opener: Callable[[], Any] | None = None,
        editor_loader: Callable[[str], None] | None = None,
        parent: QWidget | None = None,
        *,
        sidebar_preferences_path: Path | None = None,
    ) -> None:
        """``editor_loader``: optional callback invoked from the card's
        right-click "Edit..." action. Receives the preset id; the host
        is expected to open the Effect Editor pre-populated with that
        preset's workflow + choices. When left ``None`` the context
        menu is still shown (so the shortcut is discoverable) but the
        action is a no-op — callers can hide the menu by not returning
        one from the card's ``contextMenuEvent`` if they want to
        suppress it entirely.
        """
        super().__init__(parent)
        self.setObjectName("PresetManagerWindow")
        self.setAcceptDrops(True)
        self._library = library
        self._applier = applier
        self._editor_opener = editor_opener
        self._editor_loader = editor_loader
        self._init_runtime_state(sidebar_preferences_path)
        self._init_theme_stylesheet_state()
        self._load_sidebar_preferences()

        self.setWindowTitle(_tr("AI Effects Manager"))
        self.resize(1000, 560)
        self._apply_theme_stylesheet()

        window_root = QVBoxLayout(self)
        window_root.setContentsMargins(0, 0, 0, 0)
        window_root.setSpacing(0)

        central = QWidget(self)
        central.setObjectName("PresetManagerCentral")
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_toolbar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self._build_sidebar(), stretch=0)
        body.addWidget(self._build_content_area(), stretch=1)
        body_wrapper = QWidget(central)
        body_wrapper.setLayout(body)
        root.addWidget(body_wrapper, stretch=1)

        window_root.addWidget(central, stretch=1)
        self._status_bar = QStatusBar(self)
        self._status_bar.setSizeGripEnabled(False)
        window_root.addWidget(self._status_bar)

    # ------------------------------------------------------------------
    # Construction helpers
    # ------------------------------------------------------------------

    def _init_runtime_state(self, sidebar_preferences_path: Path | None) -> None:
        self._card_factory: PresetCardFactory | None = None
        self._cards: list[_PresetCard | _PresetListRow | _PresetCompactCard] = []
        self._filter_engine = PresetFilterEngine()
        self._filter_category: str | None = None
        self._filter_gateway: str | None = None
        self._filter_source: str | None = None
        self._search_query: str = ""
        self._sort_mode: str = "recent"
        self._view_mode = _VIEW_GRID
        self._grid_columns = 0
        self._grid_viewport: QWidget | None = None
        self._sidebar_items: dict[str, _SidebarItem] = {}
        self._sidebar_labels: dict[str, str] = {}
        self._sidebar_frame: QFrame | None = None
        self._sidebar_layout: QVBoxLayout | None = None
        self._sidebar_preferences_path = sidebar_preferences_path or default_preferences_path()
        self._custom_categories: list[str] = []
        self._custom_sources: list[str] = []
        self._custom_gateway_icons: dict[str, str] = {}

    def statusBar(self) -> QStatusBar:  # noqa: N802 - QMainWindow compatibility
        return self._status_bar

    def _build_toolbar(self) -> QFrame:
        bar = QFrame(self)
        bar.setObjectName("PresetManagerToolbar")
        bar.setFixedHeight(42)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(10, 6, 10, 6)
        layout.setSpacing(8)

        # Search box
        search_box = QFrame(bar)
        search_box.setObjectName("PresetManagerSearchBox")
        search_box.setFixedHeight(26)
        sb_layout = QHBoxLayout(search_box)
        sb_layout.setContentsMargins(0, 0, 8, 0)
        sb_layout.setSpacing(0)
        icon = QLabel("⌕", search_box)
        icon.setObjectName("PresetManagerSearchIcon")
        sb_layout.addWidget(icon)
        self._search_input = QLineEdit(search_box)
        self._search_input.setObjectName("PresetManagerSearchInput")
        self._search_input.setPlaceholderText(_tr("Search presets by name / tag / author..."))
        self._search_input.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._search_input.textChanged.connect(self._on_search_text_changed)
        sb_layout.addWidget(self._search_input, stretch=1)
        layout.addWidget(search_box, stretch=1)

        layout.addWidget(self._build_view_toggle(bar))

        # Import button
        self._import_button = QPushButton(f"📂 {_tr('Import')}", bar)
        self._import_button.setObjectName("PresetManagerToolbarBtn")
        self._import_button.clicked.connect(self._on_import_clicked)
        layout.addWidget(self._import_button)

        # + New button
        self._new_button = QPushButton(f"+ {_tr('New')}", bar)
        self._new_button.setObjectName("PresetManagerToolbarBtnPrimary")
        self._new_button.clicked.connect(self._on_new_clicked)
        if self._editor_opener is None:
            self._new_button.setEnabled(False)
        layout.addWidget(self._new_button)

        return bar

    def _build_view_toggle(self, parent: QWidget) -> QFrame:
        toggle = QFrame(parent)
        toggle_layout = QHBoxLayout(toggle)
        toggle_layout.setContentsMargins(0, 0, 0, 0)
        toggle_layout.setSpacing(0)

        self._grid_toggle = self._make_view_toggle_button(
            toggle,
            "▦",
            _tr("Grid view"),
            _VIEW_GRID,
        )
        self._list_toggle = self._make_view_toggle_button(
            toggle,
            "☰",
            _tr("List view"),
            _VIEW_LIST,
        )
        self._compact_toggle = self._make_view_toggle_button(
            toggle,
            "▤",
            _tr("Small cards"),
            _VIEW_COMPACT,
        )
        toggle_layout.addWidget(self._grid_toggle)
        toggle_layout.addWidget(self._list_toggle)
        toggle_layout.addWidget(self._compact_toggle)
        self._sync_view_toggles()
        return toggle

    def _make_view_toggle_button(
        self,
        parent: QWidget,
        text: str,
        tooltip: str,
        mode: str,
    ) -> QToolButton:
        button = QToolButton(parent)
        button.setObjectName("PresetManagerViewToggleBtn")
        button.setText(text)
        button.setCheckable(True)
        button.setToolTip(tooltip)
        button.clicked.connect(lambda _checked=False: self._set_view_mode(mode))
        return button

    def _build_sidebar(self) -> QScrollArea:
        scroll = QScrollArea(self)
        scroll.setObjectName("PresetManagerSidebarScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(210)
        scroll.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Ignored)

        sidebar = QFrame(scroll)
        sidebar.setObjectName("PresetManagerSidebar")
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 6, 0, 6)
        layout.setSpacing(1)
        self._sidebar_frame = sidebar
        self._sidebar_layout = layout
        self._rebuild_sidebar([])
        scroll.setWidget(sidebar)
        return scroll

    def _rebuild_sidebar(self, presets: list[Any]) -> None:
        """Recreate sidebar rows so custom/discovered buckets appear."""
        if self._sidebar_layout is None or self._sidebar_frame is None:
            return
        self._clear_layout(self._sidebar_layout)
        self._sidebar_items.clear()
        self._sidebar_labels.clear()

        self._sidebar_layout.addWidget(
            self._section_header(
                _tr("Category"),
                on_add=self._on_add_category_clicked,
                tooltip=_tr("Add category"),
            )
        )
        self._add_sidebar_item(
            "✦",
            _tr("All"),
            item_id="cat:all",
            active=self._filter_category is None,
        )
        for value, icon, label, deletable in self._category_sidebar_entries(presets):
            self._add_sidebar_item(
                icon,
                label,
                item_id=f"cat:{value}",
                active=self._filter_category == value,
                deletable=deletable,
            )
        self._sidebar_layout.addWidget(self._divider())

        self._sidebar_layout.addWidget(self._section_header(_tr("Gateway")))
        for value, icon, label in self._gateway_sidebar_entries(presets):
            self._add_sidebar_item(
                icon,
                label,
                item_id=f"gw:{value}",
                active=self._filter_gateway == value,
                editable=True,
            )
        self._sidebar_layout.addWidget(self._divider())

        self._sidebar_layout.addWidget(
            self._section_header(
                _tr("Source"),
                on_add=self._on_add_source_clicked,
                tooltip=_tr("Add source"),
            )
        )
        for value, icon, label, deletable in self._source_sidebar_entries(presets):
            self._add_sidebar_item(
                icon,
                label,
                item_id=f"src:{value}",
                active=self._filter_source == value,
                deletable=deletable,
            )

        self._sidebar_layout.addStretch(1)

    @staticmethod
    def _clear_layout(layout: QVBoxLayout) -> None:
        while layout.count():
            item = layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                PresetManagerWindow._dispose_embedded_widget(widget)

    @staticmethod
    def _dispose_embedded_widget(widget: QWidget) -> None:
        widget.hide()
        widget.deleteLater()

    def _section_header(
        self,
        text: str,
        *,
        on_add: Callable[[], None] | None = None,
        tooltip: str = "",
    ) -> QFrame:
        header = QFrame(self._sidebar_frame)
        header.setObjectName("PresetManagerSidebarSectionHeader")
        layout = QHBoxLayout(header)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(4)

        lbl = QLabel(text, header)
        lbl.setObjectName("PresetManagerSidebarSectionTitle")
        layout.addWidget(lbl, stretch=1)
        if on_add is not None:
            add_button = QToolButton(header)
            add_button.setObjectName("PresetManagerSidebarIconButton")
            add_button.setText("+")
            add_button.setFixedSize(18, 18)
            add_button.setToolTip(tooltip)
            add_button.clicked.connect(on_add)
            layout.addWidget(add_button)
        return header

    def _divider(self) -> QFrame:
        div = QFrame(self._sidebar_frame)
        div.setObjectName("PresetManagerSidebarDivider")
        return div

    def _add_sidebar_item(
        self,
        icon: str,
        label: str,
        *,
        item_id: str,
        active: bool = False,
        deletable: bool = False,
        editable: bool = False,
    ) -> _SidebarItem:
        item = _SidebarItem(
            icon,
            label,
            0,
            item_id=item_id,
            active=active,
            deletable=deletable,
            editable=editable,
            parent=self._sidebar_frame,
        )
        item.clicked.connect(self._on_sidebar_clicked)
        item.delete_requested.connect(self._on_delete_sidebar_item)
        item.edit_requested.connect(self._on_edit_gateway_icon)
        self._sidebar_items[item_id] = item
        self._sidebar_labels[item_id] = label
        if self._sidebar_layout is not None:
            self._sidebar_layout.addWidget(item)
        return item

    def _category_sidebar_entries(self, presets: list[Any]) -> list[tuple[str, str, str, bool]]:
        custom_values = {_normalise_sidebar_value(label) for label in self._custom_categories}
        entries: list[tuple[str, str, str, bool]] = []
        seen: set[str] = {"all"}
        for value, icon, label in _BUILTIN_CATEGORY_ENTRIES:
            entries.append((value, icon, _tr(label), value in custom_values))
            seen.add(value)

        for label in self._custom_categories:
            value = _normalise_sidebar_value(label)
            if not value or value in seen:
                continue
            entries.append((value, "•", label, True))
            seen.add(value)

        for preset in presets:
            raw = _clean_sidebar_label(str(getattr(preset, "category", "") or ""))
            value = _normalise_sidebar_value(raw)
            if not value or value in seen:
                continue
            entries.append((value, "•", raw, value in custom_values))
            seen.add(value)
        return entries

    def _source_sidebar_entries(self, presets: list[Any]) -> list[tuple[str, str, str, bool]]:
        custom_values = {_normalise_sidebar_value(label) for label in self._custom_sources}
        entries: list[tuple[str, str, str, bool]] = []
        seen: set[str] = set()
        for value, icon, label in _BUILTIN_SOURCE_ENTRIES:
            entries.append((value, icon, _tr(label), value in custom_values))
            seen.add(value)

        for label in self._custom_sources:
            value = _normalise_sidebar_value(label)
            if not value or value in seen or value == "imported":
                continue
            entries.append((value, "•", label, True))
            seen.add(value)

        for preset in presets:
            raw = _clean_sidebar_label(str(getattr(preset, "source", "") or ""))
            value = _normalise_sidebar_value(raw)
            if not value or value in seen or value in _RESERVED_SOURCE_VALUES:
                continue
            entries.append((value, "•", raw, value in custom_values))
            seen.add(value)
        return entries

    def _gateway_sidebar_entries(self, presets: list[Any]) -> list[tuple[str, str, str]]:
        entries: list[tuple[str, str, str]] = []
        defaults = {value: icon for value, icon, _label in _BUILTIN_GATEWAY_ENTRIES}
        seen: set[str] = set()
        for value, icon, label in _BUILTIN_GATEWAY_ENTRIES:
            entries.append((value, self._custom_gateway_icons.get(value, icon), label))
            seen.add(value)

        for preset in presets:
            raw = _clean_sidebar_label(str(getattr(preset, "gateway_id", "") or ""))
            value = _normalise_sidebar_value(raw)
            if not value or value in seen:
                continue
            entries.append(
                (
                    value,
                    self._custom_gateway_icons.get(value, defaults.get(value, "•")),
                    raw,
                )
            )
            seen.add(value)
        return entries

    def _load_sidebar_preferences(self) -> None:
        try:
            prefs = AIEffectPreferences.load(self._sidebar_preferences_path)
        except Exception:
            logger.exception("PresetManagerWindow: failed to load sidebar preferences")
            return
        ui = getattr(prefs, "ui", None)
        self._custom_categories = _dedupe_sidebar_labels(
            getattr(ui, "preset_manager_custom_categories", [])
        )
        self._custom_sources = _dedupe_sidebar_labels(
            getattr(ui, "preset_manager_custom_sources", [])
        )
        self._custom_gateway_icons = _normalise_gateway_icons(
            getattr(ui, "preset_manager_gateway_icons", {})
        )

    def _save_sidebar_preferences(self) -> bool:
        try:
            prefs = AIEffectPreferences.load(self._sidebar_preferences_path)
            prefs.ui.preset_manager_custom_categories = _dedupe_sidebar_labels(
                self._custom_categories
            )
            prefs.ui.preset_manager_custom_sources = _dedupe_sidebar_labels(self._custom_sources)
            prefs.ui.preset_manager_gateway_icons = _normalise_gateway_icons(
                self._custom_gateway_icons
            )
            prefs.save(self._sidebar_preferences_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PresetManagerWindow: failed to save sidebar preferences: %s", exc)
            self.statusBar().showMessage(_tr("Could not save sidebar settings: %s") % exc, 5000)
            return False
        self._custom_categories = list(prefs.ui.preset_manager_custom_categories)
        self._custom_sources = list(prefs.ui.preset_manager_custom_sources)
        self._custom_gateway_icons = dict(prefs.ui.preset_manager_gateway_icons)
        return True

    def _build_content_area(self) -> QWidget:
        area = QWidget(self)
        layout = QVBoxLayout(area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame(area)
        header.setObjectName("PresetManagerContentHeader")
        header.setFixedHeight(38)
        hlayout = QHBoxLayout(header)
        hlayout.setContentsMargins(14, 6, 14, 6)
        hlayout.setSpacing(10)
        self._content_title = QLabel(_tr("All Presets"), header)
        self._content_title.setObjectName("PresetManagerContentTitle")
        hlayout.addWidget(self._content_title)
        self._content_count = QLabel(_tr("%d items") % 0, header)
        self._content_count.setObjectName("PresetManagerContentCount")
        hlayout.addWidget(self._content_count)
        hlayout.addStretch(1)
        self._sort_label = QLabel(_tr("Sort:"), header)
        self._sort_label.setObjectName("PresetManagerSortLabel")
        hlayout.addWidget(self._sort_label)
        self._sort_select = QComboBox(header)
        self._sort_select.setObjectName("PresetManagerSortSelect")
        # itemData carries the canonical key the filter pipeline reads
        # so the visible label can be translated without coupling sort
        # behaviour to the user's locale.
        for label, key in (
            (_tr("Recently used"), "recent"),
            (_tr("Name (A-Z)"), "name"),
            (_tr("Category"), "category"),
            (_tr("Created"), "created"),
        ):
            self._sort_select.addItem(label, key)
        self._sort_select.currentIndexChanged.connect(self._on_sort_changed)
        hlayout.addWidget(self._sort_select)
        layout.addWidget(header)

        self._grid_scroll = QScrollArea(area)
        self._grid_scroll.setObjectName("PresetManagerGridScroll")
        self._grid_scroll.setWidgetResizable(True)
        self._grid_viewport = self._grid_scroll.viewport()
        self._grid_viewport.installEventFilter(self)
        self._grid_host = QWidget(self._grid_scroll)
        self._grid_layout = QGridLayout(self._grid_host)
        self._grid_layout.setContentsMargins(
            _GRID_MARGIN,
            _GRID_MARGIN,
            _GRID_MARGIN,
            _GRID_MARGIN,
        )
        self._grid_layout.setSpacing(_GRID_SPACING)
        self._grid_layout.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self._card_factory = PresetCardFactory(
            parent=self._grid_host,
            card_cls=_PresetCard,
            list_row_cls=_PresetListRow,
            compact_card_cls=_PresetCompactCard,
            apply_handler=self._on_card_apply_requested,
            edit_handler=self._on_card_edit_requested,
            delete_handler=self._on_card_delete_requested,
        )
        self._grid_scroll.setWidget(self._grid_host)
        layout.addWidget(self._grid_scroll, stretch=1)

        return area

    def _set_view_mode(self, mode: str) -> None:
        if mode not in {_VIEW_GRID, _VIEW_LIST, _VIEW_COMPACT}:
            return
        if mode == self._view_mode:
            self._sync_view_toggles()
            return
        self._view_mode = mode
        self._grid_columns = 0
        self._sync_view_toggles()
        self.refresh()

    def _sync_view_toggles(self) -> None:
        for button, checked in (
            (self._grid_toggle, self._view_mode == _VIEW_GRID),
            (self._list_toggle, self._view_mode == _VIEW_LIST),
            (self._compact_toggle, self._view_mode == _VIEW_COMPACT),
        ):
            button.blockSignals(True)
            button.setChecked(checked)
            button.blockSignals(False)

    def _create_preset_item(
        self,
        preset: Any,
    ) -> _PresetCard | _PresetListRow | _PresetCompactCard:
        if self._card_factory is None:
            raise RuntimeError("preset card factory is not initialized")
        return self._card_factory.create(preset, self._view_mode)

    # ------------------------------------------------------------------
    # Lifecycle / refresh
    # ------------------------------------------------------------------

    def showEvent(self, event: Any) -> None:  # noqa: N802 — Qt naming
        self.refresh()
        super().showEvent(event)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt naming
        super().changeEvent(event)
        event_type = event.type()
        if event_type == QEvent.Type.LanguageChange:
            self._refresh_translations()
        elif event_type in (
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.PaletteChange,
        ):
            self._request_theme_stylesheet_refresh()

    def _request_theme_stylesheet_refresh(self) -> None:
        if self._theme_stylesheet_pending:
            return
        self._theme_stylesheet_pending = True
        QTimer.singleShot(0, self._apply_theme_stylesheet)

    def _apply_theme_stylesheet(self) -> None:
        self._theme_stylesheet_pending = False
        if self._applying_theme_stylesheet or not is_valid(self):
            return
        theme_name = current_theme_name()
        if self._applied_theme_name == theme_name:
            return
        self._applying_theme_stylesheet = True
        try:
            self.setStyleSheet(_style_sheet_for_theme(theme_name))
            self._applied_theme_name = theme_name
        finally:
            self._applying_theme_stylesheet = False

    def _init_theme_stylesheet_state(self) -> None:
        self._applied_theme_name: str | None = None
        self._theme_stylesheet_pending = False
        self._applying_theme_stylesheet = False

    def _refresh_translations(self) -> None:
        self.setWindowTitle(_tr("AI Effects Manager"))
        self._search_input.setPlaceholderText(_tr("Search presets by name / tag / author..."))
        self._import_button.setText(f"📂 {_tr('Import')}")
        self._new_button.setText(f"+ {_tr('New')}")
        self._grid_toggle.setToolTip(_tr("Grid view"))
        self._list_toggle.setToolTip(_tr("List view"))
        self._compact_toggle.setToolTip(_tr("Small cards"))
        self._content_title.setText(_tr("All Presets"))
        self._sort_label.setText(_tr("Sort:"))
        current = self._sort_select.currentData()
        self._sort_select.blockSignals(True)
        try:
            labels = {
                "recent": _tr("Recently used"),
                "name": _tr("Name (A-Z)"),
                "category": _tr("Category"),
                "created": _tr("Created"),
            }
            for index in range(self._sort_select.count()):
                key = self._sort_select.itemData(index)
                if isinstance(key, str) and key in labels:
                    self._sort_select.setItemText(index, labels[key])
            if isinstance(current, str):
                selected = self._sort_select.findData(current)
                if selected >= 0:
                    self._sort_select.setCurrentIndex(selected)
        finally:
            self._sort_select.blockSignals(False)
        self.refresh()

    def refresh(self) -> None:
        """Re-read the library and rebuild the preset grid.

        Applies the current sidebar filter (category / gateway /
        source) + search query + sort order before card construction.
        Sidebar item counts are recomputed from the FULL library so
        the badges still tell the user how many items each filter
        bucket contains.
        """
        while self._grid_layout.count():
            item = self._grid_layout.takeAt(0)
            if item is None:
                continue
            w = item.widget()
            if w is not None:
                self._dispose_embedded_widget(w)
        self._cards.clear()

        all_presets: list[Any] = []
        if self._library is not None:
            try:
                all_presets = list(self._library.list_all())
            except Exception:
                logger.exception("PresetManagerWindow: library.list_all failed")
                all_presets = []

        self._rebuild_sidebar(all_presets)
        self._update_sidebar_counts(all_presets)
        presets = self._apply_filters_and_sort(all_presets)

        for preset in presets:
            self._cards.append(self._create_preset_item(preset))
        self._relayout_cards(force=True)

        # Show "N of M" when filters are narrowing the visible set so
        # the user can tell at a glance how much was filtered out.
        if len(presets) == len(all_presets):
            self._content_count.setText(_tr("%d items") % len(presets))
        else:
            self._content_count.setText(
                _tr("%(n)d of %(total)d") % {"n": len(presets), "total": len(all_presets)}
            )

    def eventFilter(self, watched: Any, event: Any) -> bool:  # noqa: N802 - Qt naming
        viewport = getattr(self, "_grid_viewport", None)
        if (
            watched is viewport
            and event.type() == QEvent.Type.Resize
            and self._cards
            and is_valid(viewport)
            and is_valid(getattr(self, "_grid_scroll", None))
        ):
            self._relayout_cards()
        return False

    def closeEvent(self, event: Any) -> None:  # noqa: N802 - Qt naming
        self._remove_grid_viewport_event_filter()
        super().closeEvent(event)

    def _remove_grid_viewport_event_filter(self) -> None:
        viewport = getattr(self, "_grid_viewport", None)
        if viewport is None:
            return
        if is_valid(viewport):
            with suppress(RuntimeError):
                viewport.removeEventFilter(self)
        self._grid_viewport = None

    def _grid_columns_for_width(self, width: int | None = None) -> int:
        if self._view_mode == _VIEW_LIST:
            return 1
        if width is None:
            width = self._grid_scroll.viewport().width()
        margins = self._grid_layout.contentsMargins()
        usable = max(1, int(width) - margins.left() - margins.right())
        spacing = self._grid_layout.horizontalSpacing()
        if spacing < 0:
            spacing = self._grid_layout.spacing()
        item_width = _COMPACT_CARD_W if self._view_mode == _VIEW_COMPACT else _CARD_W
        return max(1, (usable + spacing) // (item_width + spacing))

    def _relayout_cards(self, *, force: bool = False) -> None:
        columns = self._grid_columns_for_width()
        if not force and columns == self._grid_columns:
            return
        while self._grid_layout.count():
            self._grid_layout.takeAt(0)
        for idx, card in enumerate(self._cards):
            row, col = divmod(idx, columns)
            self._grid_layout.addWidget(card, row, col)
        self._grid_layout.setColumnStretch(0, 1 if self._view_mode == _VIEW_LIST else 0)
        self._grid_columns = columns
        self._grid_host.updateGeometry()

    # ------------------------------------------------------------------
    # Filter / sort / search pipeline (Phase 6-e-2)
    # ------------------------------------------------------------------
    def _filter_state(self) -> PresetFilterState:
        return PresetFilterState(
            category=self._filter_category,
            gateway=self._filter_gateway,
            source=self._filter_source,
            search_query=self._search_query,
            sort_mode=self._sort_mode,
        )

    def _apply_filters_and_sort(self, presets: list[Any]) -> list[Any]:
        """Filter and sort ``presets`` per current UI state."""
        return self._filter_engine.apply(presets, self._filter_state())

    def _preset_matches_filters(self, preset: Any) -> bool:
        """Return True iff ``preset`` survives every active filter."""
        return self._filter_engine.matches(preset, self._filter_state())

    @staticmethod
    def _preset_search_haystack(preset: Any) -> str:
        """Concatenate name + tags + description into one lower-cased
        string for substring search."""
        return PresetFilterEngine.search_haystack(preset)

    def _sort_presets(self, presets: list[Any]) -> list[Any]:
        """Order by ``self._sort_mode``. Falls back to the input
        order for unknown modes so a future migration cannot blank
        the manager grid."""
        return self._filter_engine.sort(presets, self._sort_mode)

    def _update_sidebar_counts(self, presets: list[Any]) -> None:
        """Refresh the right-hand badge on each sidebar item.

        Counts are computed from the full library — they always show
        the full bucket size, not the filter-narrowed view, so the
        user can tell what each filter would surface before clicking."""
        counts = self._filter_engine.sidebar_counts(presets)
        for item_id, item in self._sidebar_items.items():
            if item_id == "cat:all":
                count = counts.total
            elif item_id.startswith("cat:"):
                count = counts.categories.get(item_id[len("cat:") :], 0)
            elif item_id.startswith("gw:"):
                count = counts.gateways.get(item_id[len("gw:") :], 0)
            elif item_id.startswith("src:"):
                count = counts.sources.get(item_id[len("src:") :], 0)
            else:
                continue
            item.set_count(count)

    # ------------------------------------------------------------------
    # Filter UI signal handlers
    # ------------------------------------------------------------------
    def _on_sidebar_clicked(self, item_id: str) -> None:
        """Toggle the matching filter axis and refresh.

        Clicking the active item in any section turns the filter
        off (returns to that section's "all" mode). Each section is
        independent — picking a Category does NOT clear the active
        Gateway filter, etc.
        """
        if item_id.startswith("cat:"):
            value = item_id[len("cat:") :]
            new_category = None if value == "all" else value
            if self._filter_category == new_category:
                # Same item clicked again → no-op (keeps "All" sticky).
                if new_category is None:
                    return
                # Toggling off a non-All category returns to All.
                new_category = None
            self._filter_category = new_category
            self._sync_section_active("cat:", item_id if new_category is not None else "cat:all")
        elif item_id.startswith("gw:"):
            value = item_id[len("gw:") :]
            new_gateway = None if self._filter_gateway == value else value
            self._filter_gateway = new_gateway
            self._sync_section_active("gw:", item_id if new_gateway is not None else None)
        elif item_id.startswith("src:"):
            value = item_id[len("src:") :]
            new_source = None if self._filter_source == value else value
            self._filter_source = new_source
            self._sync_section_active("src:", item_id if new_source is not None else None)
        else:
            return
        self.refresh()

    def _sync_section_active(self, prefix: str, active_id: str | None) -> None:
        """Apply the visual ``active`` state across one section."""
        for item_id, item in self._sidebar_items.items():
            if item_id.startswith(prefix):
                item.set_active(item_id == active_id)

    def _on_add_category_clicked(self) -> None:
        text, ok = QInputDialog.getText(
            self,
            _tr("Add category"),
            _tr("Category name:"),
        )
        if not ok:
            return
        self._add_custom_sidebar_value("category", text)

    def _on_add_source_clicked(self) -> None:
        text, ok = QInputDialog.getText(
            self,
            _tr("Add source"),
            _tr("Source name:"),
        )
        if not ok:
            return
        self._add_custom_sidebar_value("source", text)

    def _add_custom_sidebar_value(self, section: str, label: str) -> None:
        clean = _clean_sidebar_label(label)
        value = _normalise_sidebar_value(clean)
        if not value:
            return

        if section == "category":
            prefix = "cat:"
            reserved = {"all"}
            custom = self._custom_categories
            duplicate_message = _tr("Category '%s' already exists.")
            added_message = _tr("Added category '%s'.")
        elif section == "source":
            prefix = "src:"
            reserved = _RESERVED_SOURCE_VALUES
            custom = self._custom_sources
            duplicate_message = _tr("Source '%s' already exists.")
            added_message = _tr("Added source '%s'.")
        else:
            return

        if value in reserved or f"{prefix}{value}" in self._sidebar_items:
            self.statusBar().showMessage(duplicate_message % clean, 4000)
            return
        custom.append(clean)
        if not self._save_sidebar_preferences():
            return
        self.statusBar().showMessage(added_message % clean, 4000)
        self.refresh()

    def _on_delete_sidebar_item(self, item_id: str) -> None:
        if item_id.startswith("cat:"):
            value = item_id[len("cat:") :]
            section = "category"
            title = _tr("Delete category")
            prompt = _tr("Remove category '%s' from the sidebar? Presets are not deleted.")
            removed_message = _tr("Removed category '%s'.")
        elif item_id.startswith("src:"):
            value = item_id[len("src:") :]
            section = "source"
            title = _tr("Delete source")
            prompt = _tr("Remove source '%s' from the sidebar? Presets are not deleted.")
            removed_message = _tr("Removed source '%s'.")
        else:
            return

        label = self._sidebar_labels.get(item_id, value)
        reply = QMessageBox.question(
            self,
            title,
            prompt % label,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        if not self._remove_custom_sidebar_value(section, value):
            return
        self.statusBar().showMessage(removed_message % label, 4000)
        self.refresh()

    def _on_edit_gateway_icon(self, item_id: str) -> None:
        if not item_id.startswith("gw:"):
            return
        gateway_id = item_id[len("gw:") :]
        if not gateway_id:
            return
        default_icon = {value: icon for value, icon, _label in _BUILTIN_GATEWAY_ENTRIES}.get(
            gateway_id, "•"
        )
        current_icon = self._custom_gateway_icons.get(gateway_id, default_icon)
        text, ok = QInputDialog.getText(
            self,
            _tr("Change Gateway icon"),
            _tr("Gateway icon (empty resets to default):"),
            text=current_icon,
        )
        if not ok:
            return
        icon = _clean_gateway_icon(text)
        if icon:
            self._custom_gateway_icons[gateway_id] = icon
            message = _tr("Changed Gateway icon for '%s'.")
        else:
            self._custom_gateway_icons.pop(gateway_id, None)
            message = _tr("Reset Gateway icon for '%s'.")
        if not self._save_sidebar_preferences():
            return
        label = self._sidebar_labels.get(item_id, gateway_id)
        self.statusBar().showMessage(message % label, 4000)
        self.refresh()

    def _remove_custom_sidebar_value(self, section: str, value: str) -> bool:
        if section == "category":
            before = len(self._custom_categories)
            self._custom_categories = [
                label
                for label in self._custom_categories
                if _normalise_sidebar_value(label) != value
            ]
            if len(self._custom_categories) == before:
                return False
            if self._filter_category == value:
                self._filter_category = None
        elif section == "source":
            before = len(self._custom_sources)
            self._custom_sources = [
                label for label in self._custom_sources if _normalise_sidebar_value(label) != value
            ]
            if len(self._custom_sources) == before:
                return False
            if self._filter_source == value:
                self._filter_source = None
        else:
            return False
        return self._save_sidebar_preferences()

    def _on_search_text_changed(self, text: str) -> None:
        """Live-filter as the user types. Empty query clears the
        filter entirely (so it doesn't match every preset's empty
        substring trivially — guard against non-string Qt payloads
        too)."""
        query = (text or "").strip().lower()
        if query == self._search_query:
            return
        self._search_query = query
        self.refresh()

    def _on_sort_changed(self, _index: int) -> None:
        data = self._sort_select.currentData()
        if not isinstance(data, str):
            return
        if data == self._sort_mode:
            return
        self._sort_mode = data
        self.refresh()

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_card_apply_requested(self, preset_id: str) -> None:
        """Route a card's "Add AI Effect Layer" context-menu action
        through the host's applier callback (instantiates an AI Effect
        layer on the canvas)."""
        if self._applier is None:
            return
        try:
            self._applier(preset_id)
        except Exception:
            logger.exception("PresetManagerWindow: applier failed for %s", preset_id)

    def _on_card_edit_requested(self, preset_id: str) -> None:
        """Route a card's double-click / "Edit..." action to the Editor."""
        if self._editor_loader is None:
            self.statusBar().showMessage(
                _tr("Edit: Effect Editor is not wired in this session."), 4000
            )
            return
        try:
            self._editor_loader(preset_id)
        except Exception:
            logger.exception("PresetManagerWindow: editor_loader failed for %s", preset_id)

    def _on_card_delete_requested(self, preset_id: str) -> None:
        """Confirm + delete the preset, then refresh the grid.

        Uses :meth:`AIEffectPresetLibrary.delete` which already
        guards against builtin removal — the card hides the menu
        item for builtins as well, so this is double-coverage.
        """
        if self._library is None:
            return
        # Look up the preset's display name for the confirmation
        # dialog. Falls back to the id if the lookup fails so the
        # user still sees something concrete.
        preset_name = preset_id
        try:
            preset = self._library.get(preset_id)
            preset_name = str(getattr(preset, "name", "") or preset_id)
        except Exception:
            logger.debug("delete: preset lookup failed", exc_info=True)
        reply = QMessageBox.question(
            self,
            _tr("Delete preset"),
            _tr("Delete '%s'? This cannot be undone.") % preset_name,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes:
            return
        try:
            removed = self._library.delete(preset_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PresetManager: delete failed for %s: %s", preset_id, exc)
            self.statusBar().showMessage(_tr("Delete failed: %s") % exc, 4000)
            return
        if not removed:
            self.statusBar().showMessage(
                _tr("Cannot delete '%s' (builtin or missing).") % preset_name, 4000
            )
            return
        self.statusBar().showMessage(_tr("Deleted '%s'") % preset_name, 4000)
        self.refresh()

    def _on_import_clicked(self) -> None:
        if self._library is None:
            return
        path, _filter = QFileDialog.getOpenFileName(
            self,
            _tr("Import preset"),
            "",
            _tr("LoomStroke effect (*.lsfx);;All files (*)"),
        )
        if not path:
            return
        try:
            self._library.import_lsfx(Path(path), overwrite=True)
        except Exception as exc:  # noqa: BLE001
            logger.warning("PresetManagerWindow: import failed for %s: %s", path, exc)
            self.statusBar().showMessage(_tr("Import failed: %s") % exc, 4000)
            return
        self.refresh()

    def _on_new_clicked(self) -> None:
        if self._editor_opener is None:
            return
        try:
            self._editor_opener()
        except Exception:
            logger.exception("PresetManagerWindow: editor_opener failed")

    # ------------------------------------------------------------------
    # Workflow drag/drop
    # ------------------------------------------------------------------

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - Qt naming
        if self._workflow_paths_from_mime(event.mimeData()):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - Qt naming
        paths = self._workflow_paths_from_mime(event.mimeData())
        if not paths:
            super().dropEvent(event)
            return
        event.acceptProposedAction()
        self._open_workflow_path_in_editor(paths[0])

    def _workflow_paths_from_mime(self, mime_data: Any) -> list[Path]:
        if mime_data is None or not mime_data.hasUrls():
            return []
        paths: list[Path] = []
        for url in mime_data.urls():
            if not url.isLocalFile():
                continue
            path = Path(url.toLocalFile())
            if path.suffix.lower() == ".json":
                paths.append(path)
        return paths

    def _open_workflow_path_in_editor(self, path: Path) -> None:
        if self._editor_opener is None:
            self.statusBar().showMessage(
                _tr("Edit: Effect Editor is not wired in this session."), 4000
            )
            return
        try:
            editor = self._editor_opener()
        except Exception:
            logger.exception("PresetManagerWindow: editor_opener failed")
            return
        load_workflow_file = getattr(editor, "load_workflow_file", None)
        if not callable(load_workflow_file):
            self.statusBar().showMessage(
                _tr("Edit: Effect Editor is not wired in this session."), 4000
            )
            return
        self._select_gateway_if_available(editor, "comfyui")
        try:
            load_workflow_file(path)
        except Exception:
            logger.exception("PresetManagerWindow: failed to load dropped workflow %s", path)

    @staticmethod
    def _select_gateway_if_available(editor: Any, gateway_id: str) -> None:
        select_gateway = getattr(editor, "select_gateway", None)
        if not callable(select_gateway):
            return
        try:
            select_gateway(gateway_id)
        except Exception:
            logger.exception(
                "PresetManagerWindow: failed to select gateway %s for dropped workflow",
                gateway_id,
            )


__all__ = ["PresetManagerWindow"]
