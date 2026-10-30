"""Popup dialog that embeds the ColorPanel for inline color selection."""

from __future__ import annotations

import json
import logging
import uuid

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.color_state import ColorDescriptor
from loomstroke.platform.cache_paths import get_cache_root
from loomstroke.plugins.color_manager.color_models import (
    DEFAULT_PALETTE_ID,
    ColorPalette,
    PaletteSwatch,
)
from loomstroke.plugins.color_manager.widgets.panel import ColorPanel
from loomstroke.ui.translations import TranslationBinder, translate_with_fallback

logger = logging.getLogger(__name__)


class ColorPickerPopup(QDialog):
    """Modal dialog wrapping the ColorPanel widget for color selection.

    Usage::

        color = ColorPickerPopup.pick_color(initial_qcolor, parent_widget)
        if color is not None and color.isValid():
            ...
    """

    def __init__(
        self,
        initial: QColor,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._translations = TranslationBinder(
            lambda source: translate_with_fallback("ColorPickerPopup", source)
        )
        self._translations.register(self.setWindowTitle, "Color Picker")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.WindowCloseButtonHint)

        self._result_color: QColor | None = None

        # --- Embed ColorPanel ---
        self._panel = ColorPanel(self)

        # Load swatch palettes
        self._palettes = self._load_palettes()
        if self._palettes:
            self._panel.set_palettes(self._palettes)

        # Save on swatch add / deletion
        self._panel.paletteRequested.connect(self._on_palette_requested)
        self._panel.paletteSwatchRemoved.connect(self._on_swatch_removed)

        # Set initial color
        desc = ColorDescriptor(
            (initial.redF(), initial.greenF(), initial.blueF(), initial.alphaF())
        )
        self._panel._apply_descriptor(desc)

        # Track live selection
        self._panel.colorSelected.connect(self._on_color_selected)

        # --- OK / Cancel buttons ---
        btn_layout = QHBoxLayout()
        btn_layout.addStretch(1)
        ok_btn = QPushButton()
        self._translations.register(ok_btn.setText, "OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        cancel_btn = QPushButton()
        self._translations.register(cancel_btn.setText, "Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(ok_btn)
        btn_layout.addWidget(cancel_btn)

        # --- Main layout ---
        layout = QVBoxLayout(self)
        layout.addWidget(self._panel)
        layout.addLayout(btn_layout)
        self.adjustSize()
        self.setFixedSize(self.sizeHint())

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "_translations"):
            self._translations.retranslate()

    @staticmethod
    def _load_palettes() -> list[ColorPalette]:
        """Load palettes synchronously from the cache file."""
        path = get_cache_root() / "palettes.json"
        if not path.exists():
            return []
        try:
            data = path.read_text(encoding="utf-8")
            payload = json.loads(data)
            return [ColorPalette.from_dict(item) for item in payload.get("palettes", [])]
        except Exception as exc:
            logger.debug("Failed to load palettes for popup: %s", exc)
            return []

    def _on_palette_requested(self, action: str, descriptor: ColorDescriptor) -> None:
        """Handle 'Add to Palette' button click."""
        if action != "add":
            return
        if not self._palettes:
            self._palettes = [ColorPalette(id=DEFAULT_PALETTE_ID, name="Default")]
        palette = self._palettes[0]
        r, g, b, _ = descriptor.rgba
        label = f"#{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}"
        swatch = PaletteSwatch(
            id=f"swatch_{uuid.uuid4().hex[:8]}",
            label=label,
            color=descriptor,
        )
        palette.add_swatch(swatch)
        self._panel.set_palettes(self._palettes)
        self._save_palettes(self._palettes)

    def _on_swatch_removed(self, swatch_id: str) -> None:
        """Remove swatch from the first palette and persist to disk."""
        if not self._palettes:
            return
        palette = self._palettes[0]
        try:
            palette.remove_swatch(swatch_id)
        except KeyError:
            return
        self._panel.set_palettes(self._palettes)
        self._save_palettes(self._palettes)

    @staticmethod
    def _save_palettes(palettes: list[ColorPalette]) -> None:
        """Persist palettes synchronously to the cache file."""
        path = get_cache_root() / "palettes.json"
        try:
            payload = {"palettes": [p.to_dict() for p in palettes]}
            path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.debug("Failed to save palettes from popup: %s", exc)

    def _on_color_selected(self, descriptor: ColorDescriptor) -> None:
        r, g, b, a = descriptor.rgba
        self._result_color = QColor.fromRgbF(r, g, b, a)

    def selected_color(self) -> QColor | None:
        return self._result_color

    # ------------------------------------------------------------------
    # Convenience static method (drop-in replacement for QColorDialog)
    # ------------------------------------------------------------------

    @staticmethod
    def pick_color(
        initial: QColor,
        parent: QWidget | None = None,
    ) -> QColor | None:
        """Show the popup and return the selected QColor, or *None* on cancel."""
        dlg = ColorPickerPopup(initial, parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            result = dlg.selected_color()
            return result if result is not None else initial
        return None
