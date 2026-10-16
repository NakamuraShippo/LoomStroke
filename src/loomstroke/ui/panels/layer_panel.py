"""
Layer panel for managing drawing layers.

Provides layer management UI with add, delete, reorder functionality.
Per Master.md specifications.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, cast

from PySide6.QtCore import QEvent, QPoint, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QCursor, QEnterEvent, QIcon, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMenu,
    QSizePolicy,
    QSlider,
    QStyle,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.typing import Signal0, Signal1, Signal2

from ...core.commands import BlendMode
from ...core.event_bus_extensions import get_extended_event_bus
from ...core.events import Events
from ...host_api import HostAPI
from ..ai_effect_preset_drag import ai_effect_preset_id_from_mime
from ..translations import TranslationBinder, translate_with_fallback, translated_literal
from .layer_channels_tabs import LayerChannelsTabs
from .layer_item_widgets import (
    LayerGenerateButton,
    LayerProgressOverlay,
    LayerRowControlGroup,
    clear_layer_row_icon_cache,
)
from .layer_panel_ai_effect_menu import LayerPanelAIEffectMenuBuilder
from .layer_panel_context_menu import LayerPanelContextMenuBuilder
from .layer_panel_thumbnail_manager import LayerPanelThumbnailManager

logger = logging.getLogger(__name__)

THUMBNAIL_SIZE = 32
_SAM3_AI_SELECT_TOOL_ID = "sam3_ai_select"
_SAM3_LAYER_MASK_ACTION_ID = "sam3_ai_select.apply.current_as_layer_mask"
_SAM3_ADD_TO_LAYER_MASK_ACTION_ID = "sam3_ai_select.add.current_to_layer_mask"
_SAM3_TOGGLE_CANDIDATE_OVERLAY_ACTION_ID = "sam3_ai_select.overlay.toggle_candidate"
_SAM3_LAYER_PANEL_MASK_SOURCE = "sam3.layer_panel.create_mask"
_SAM3_LAYER_PANEL_REPLACE_MASK_SOURCE = "sam3.layer_panel.replace_mask"
_SAM3_LAYER_PANEL_ADD_TO_MASK_SOURCE = "sam3.layer_panel.add_to_mask"
_SAM3_LAYER_PANEL_TOGGLE_OVERLAY_SOURCE = "sam3.layer_panel.toggle_candidate_overlay"
_SAM3_LAYER_PANEL_MASK_MERGE_KEY = "sam3-layer-panel-mask"

QtSvgRenderer: Any
try:
    from PySide6.QtSvg import QSvgRenderer as QtSvgRenderer
except ImportError:  # pragma: no cover - optional dependency
    QtSvgRenderer = None


class _DropLineOverlay(QWidget):
    """Transparent overlay that draws a drop-target line over the layer list."""

    _LINE_COLOR = QColor("#4fc3f7")
    _LINE_WIDTH = 2

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._y: int | None = None
        self.hide()

    def set_indicator(self, y: int | None) -> None:
        if y != self._y:
            self._y = y
            if y is None:
                self.hide()
            else:
                self.show()
                self.update()

    def paintEvent(self, event: Any) -> None:  # noqa: N802
        if self._y is None:
            return
        p = QPainter(self)
        pen = QPen(self._LINE_COLOR, self._LINE_WIDTH)
        p.setPen(pen)
        p.drawLine(0, self._y, self.width(), self._y)
        p.end()


class LayerTreeWidget(QTreeWidget):
    """QTreeWidget subclass that handles drops via canvas reparent instead of Qt internal move.

    This avoids the item-widget destruction that occurs when Qt moves
    QTreeWidgetItems internally, which causes blank layer cards.
    """

    # Emitted with (dragged_layer_id, target_layer_id_or_None, drop_position_enum)
    layer_drop_requested = Signal(str, object, int)
    # Emitted when the user drops an AI Effects Manager preset onto the layer list.
    ai_effect_preset_dropped = Signal(str)
    # Emitted when the user drops a real layer onto an AI Effect slot row.
    # Payload: (effect_layer_id, slot_internal_id, source_layer_id)
    # Routed to FilterPanel.apply_slot_bind by the host so the LayerPanel
    # itself never has to import the AI Effect plugin's data model.
    ai_effect_slot_bind_dropped = Signal(str, str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        vp = self.viewport()
        self._drop_overlay = _DropLineOverlay(vp)
        self._drop_overlay.resize(vp.size())

    def dragEnterEvent(self, event: Any) -> None:  # noqa: N802
        if ai_effect_preset_id_from_mime(self._event_mime_data(event)) is not None:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event: Any) -> None:  # noqa: N802
        if ai_effect_preset_id_from_mime(self._event_mime_data(event)) is not None:
            self._drop_overlay.set_indicator(None)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            return
        super().dragMoveEvent(event)
        pos = event.position().toPoint()
        item = self.itemAt(pos)
        if item is not None:
            rect = self.visualItemRect(item)
            drop_pos = self.dropIndicatorPosition()
            if drop_pos == QAbstractItemView.DropIndicatorPosition.AboveItem:
                y = rect.top()
            elif drop_pos == QAbstractItemView.DropIndicatorPosition.BelowItem:
                y = rect.bottom()
            else:
                y = None  # OnItem — no line
            self._drop_overlay.set_indicator(y)
        else:
            self._drop_overlay.set_indicator(None)

    def dragLeaveEvent(self, event: Any) -> None:  # noqa: N802
        self._drop_overlay.set_indicator(None)
        super().dragLeaveEvent(event)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._drop_overlay.resize(self.viewport().size())

    def dropEvent(self, event: Any) -> None:
        self._drop_overlay.set_indicator(None)

        preset_id = ai_effect_preset_id_from_mime(self._event_mime_data(event))
        if preset_id is not None:
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
            self.ai_effect_preset_dropped.emit(preset_id)
            return

        target_item = self.itemAt(event.position().toPoint())
        drop_pos = self.dropIndicatorPosition()
        dragged_items = self.selectedItems()
        if not dragged_items:
            event.ignore()
            return

        dragged_id = dragged_items[0].data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(dragged_id, str):
            event.ignore()
            return

        target_id = None
        if target_item is not None:
            target_id = target_item.data(0, Qt.ItemDataRole.UserRole)

        # AI Effect slot bind: dropping a real layer ONTO a synthetic
        # slot row binds it to that slot. Detected before the normal
        # reparent gate because slot rows aren't "real" canvas layers
        # and need their own routing path.
        if (
            target_item is not None
            and drop_pos == QAbstractItemView.DropIndicatorPosition.OnItem
            and isinstance(target_id, str)
            and target_id.startswith(_SLOT_ROW_ID_PREFIX)
        ):
            # Refuse drops where the source itself is a slot row
            # (synthetic id) — avoids a confusing self-bind loop.
            if dragged_id.startswith(_SLOT_ROW_ID_PREFIX):
                event.ignore()
                return
            # Synthetic id format: "__slot::{effect_id}::{slot_id}".
            # Slot ids that contain "::" themselves are unsupported by
            # this naïve split — flag and skip rather than route to a
            # malformed signal payload.
            tail = target_id[len(_SLOT_ROW_ID_PREFIX) :]
            parts = tail.split("::", 1)
            if len(parts) != 2 or not parts[0] or not parts[1]:
                event.ignore()
                return
            effect_layer_id, slot_internal_id = parts
            event.setDropAction(Qt.DropAction.IgnoreAction)
            event.accept()
            self.ai_effect_slot_bind_dropped.emit(effect_layer_id, slot_internal_id, dragged_id)
            return

        # Reject child drop on non-group items (except effect → raster/vector)
        if target_item is not None and drop_pos == QAbstractItemView.DropIndicatorPosition.OnItem:
            target_type = target_item.data(0, Qt.ItemDataRole.UserRole + 1)
            dragged_type = dragged_items[0].data(0, Qt.ItemDataRole.UserRole + 1)
            if target_type == "group":
                pass  # groups always accept children
            elif dragged_type == "effect" and target_type in ("raster", "vector"):
                pass  # effect can become child of content layer
            else:
                event.ignore()
                return

        # Do NOT call super().dropEvent() — avoid Qt internal item move.
        # Instead, signal the panel to handle reparent via canvas API.
        event.setDropAction(Qt.DropAction.IgnoreAction)
        event.accept()
        self.layer_drop_requested.emit(dragged_id, target_id, drop_pos.value)

    @staticmethod
    def _event_mime_data(event: Any) -> Any:
        mime_data = getattr(event, "mimeData", None)
        if callable(mime_data):
            return mime_data()
        return None


MASK_THUMBNAIL_SIZE = 24

# Synthetic layer_id prefix for virtual slot rows. Must not collide with any
# real canvas layer UUID (those are bare UUID4 strings).
_SLOT_ROW_ID_PREFIX = "__slot::"
# Sentinel layer_type that lets downstream code (badge / drag handler / etc.)
# recognize a synthetic slot row without inspecting ``slot_info`` directly.
_SLOT_ROW_LAYER_TYPE = "ai_effect_slot"


@dataclass(slots=True)
class SlotDisplayInfo:
    """Display-oriented snapshot of one AI Effect input slot.

    Populated by ``_entry_from_layer`` when building synthetic child rows
    under an AI Effect layer. Carries everything the slot-row widget
    needs to render (label, required marker, optional ⊘ toggle) and to
    emit a meaningful signal when the user interacts with it.

    Intentionally flat: does not reference ``AIEffectState`` so that tests
    can construct fixture slot rows without wiring the full state machine.
    """

    effect_layer_id: str
    slot_id: str
    label: str
    required: bool = False
    target_layer_id: str | None = None
    exclude_blend: bool = False


@dataclass(slots=True)
class LayerEntry:
    layer_id: str
    name: str
    visible: bool = True
    opacity: float = 1.0
    locked: bool = False
    alpha_lock: bool = False
    blend_mode: str = "normal"
    thumbnail: QPixmap | None = None
    badge: str = ""
    layer_type: str = "raster"
    children: list[LayerEntry] | None = None
    mask_thumbnail: QPixmap | None = None
    mask_edit_mode: bool = False
    # AI Effect runtime state ("idle" / "queued" / "generating" / "done" /
    # "error"). Populated only when ``layer_type == "ai_effect"`` and
    # ``layer.ai_effect`` is attached. None for all other layer types.
    ai_effect_state: str | None = None
    # ``LayerOrigin.type`` (e.g. ``"ai_effect_output"``) copied from
    # ``_Layer.origin`` so the row styling can apply the green accent to
    # generated output layers. None for layers without origin metadata.
    origin_type: str | None = None
    # Populated only on synthetic AI Effect input-slot rows (layer_type
    # == ``_SLOT_ROW_LAYER_TYPE``). None on every real canvas layer.
    slot_info: SlotDisplayInfo | None = None
    # How many AI Effect slots currently reference this layer (Phase
    # 6-f-styling-d). 0 = no badge; 1+ = render "xN" link-blue chip.
    ref_count: int = 0
    # Comic reference rows point at a real source; never send their synthetic id.
    comic_source_id: str | None = None
    _comic_name: tuple[str, dict[str, Any]] | None = None
    _comic_badge: tuple[str, str] | None = None
    # Live progress fraction (0.0 - 1.0) of the in-flight generation,
    # or None when unknown (indeterminate mode). Populated only for
    # ``ai_effect`` rows whose ``state == "generating"``.
    ai_effect_progress: float | None = None


class _ClickableLabel(QLabel):
    """QLabel that emits clicked signal on mouse press."""

    clicked = Signal()

    def mousePressEvent(self, event: Any) -> None:  # noqa: N802
        self.clicked.emit()
        super().mousePressEvent(event)


_THUMB_BORDER_ACTIVE = "border: 2px solid #4fc3f7;"
_THUMB_BORDER_INACTIVE = "border: 1px solid #5a5a5a;"

# Phase 6-f-styling accent palette. Kept in rgb()/rgba() notation because Qt's
# stylesheet parser reliably handles both across platforms.
_AI_ACCENT_HEX = "#a78bfa"
_AI_ACCENT_DIM = "#6d5ba3"
_AI_BG_RGBA = "rgba(167, 139, 250, 20)"
_AI_BG_RGBA_SELECTED = "rgba(167, 139, 250, 64)"
_OUTPUT_ACCENT_HEX = "#3dbb7e"
_OUTPUT_BG_RGBA = "rgba(61, 187, 126, 20)"
_OUTPUT_BG_RGBA_SELECTED = "rgba(61, 187, 126, 64)"

# Phase 8d: per-row Generate button icon for AI Effect layers. Resolved
# relative to this module so plugins / installed wheels keep working.
_AI_GENERATE_ICON_PATH = Path(__file__).resolve().parent.parent / "icons" / "ai_generate.svg"


class LayerItemWidget(QWidget):
    """Row widget representing a single layer with visibility and lock controls."""

    visibility_changed = Signal(bool)
    lock_changed = Signal(bool)
    alpha_lock_changed = Signal(bool)
    thumbnail_clicked = Signal()
    mask_thumbnail_clicked = Signal()
    # (effect_layer_id, slot_id, new_exclude_blend). Emitted by AI Effect
    # input slot rows only. The parent LayerPanel relays this onto its
    # own ``ai_effect_exclude_blend_toggled`` signal for binder
    # consumption so subscribers do not need to traverse the tree.
    exclude_blend_toggled = Signal(str, str, bool)
    # Emitted when the user clicks the per-row Generate button on an
    # AI Effect layer. Carries the layer_id so LayerPanel can relay it
    # onto its own ``ai_effect_generate_requested`` signal which the
    # host wires straight into AIEffectGenerationController.
    generate_requested = Signal(str)

    def __init__(  # noqa: PLR0915 - one-shot wiring for related row features
        self, entry: LayerEntry, parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        # Remember the row-accent state ("ai_effect" / "ai_effect_output" /
        # None) so set_entry() can re-evaluate when the layer type or origin
        # changes without an intermediate neutral frame.
        self._row_accent: str | None = None
        # Slot-row bookkeeping (populated in _configure_for_slot). Needed by
        # _on_exclude_blend_toggled which runs on a plain click without a
        # LayerEntry argument.
        self._slot_info: SlotDisplayInfo | None = None
        # Last observed required-empty state for the slot row. Used by
        # _apply_row_accent to trigger the 0.5s warning flash exactly
        # once per False→True transition, matching the mockup note
        # "実装: 警告発生時に1回のみ".
        self._last_required_empty: bool = False
        self._controls_hovered = False
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setMouseTracking(True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(3, 2, 3, 2)
        layout.setSpacing(4)

        self.thumbnail_label = _ClickableLabel(self)
        self.thumbnail_label.setFixedSize(THUMBNAIL_SIZE, THUMBNAIL_SIZE)
        self.thumbnail_label.setObjectName("layerThumbnail")
        self.thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail_label.setStyleSheet(f"background-color: #3a3a3a; {_THUMB_BORDER_INACTIVE}")
        self.thumbnail_label.clicked.connect(self.thumbnail_clicked)
        layout.addWidget(self.thumbnail_label)
        self._apply_thumbnail(entry.thumbnail)

        # Mask thumbnail (hidden when no mask)
        self.mask_thumbnail_label = _ClickableLabel(self)
        self._init_mask_thumbnail(layout, entry)

        self.badge_label = QLabel(self)
        self.badge_label.setFixedWidth(14)
        self.badge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge_label.setObjectName("layerTypeBadge")
        layout.addWidget(self.badge_label)
        self._set_badge(entry.badge, self._badge_tooltip(entry))

        self.name_label = QLabel(entry.name, self)
        self.name_label.setMinimumWidth(0)
        self.name_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.name_label.setObjectName("layerNameLabel")
        layout.addWidget(self.name_label, 1)

        self._init_control_buttons(layout, entry)
        self._update_enabled_state(entry.visible)
        # Slot-label placeholder (created eagerly so set_entry can reuse
        # the same widget when a row flips into slot mode without a full
        # rebuild; hidden until _configure_for_slot activates it).
        self.slot_label = QLabel("", self)
        self.slot_label.setObjectName("aiEffectSlotLabel")
        self.slot_label.setVisible(False)
        layout.insertWidget(layout.indexOf(self.name_label), self.slot_label)
        # Reference-count chip — sits right of the name label. Hidden
        # when count==0 so unreferenced layers stay visually quiet.
        self.ref_count_label = QLabel("", self)
        self.ref_count_label.setObjectName("layerRefCount")
        self.ref_count_label.setVisible(False)
        layout.insertWidget(layout.indexOf(self._control_group), self.ref_count_label)
        # ⊘ toggle — shown only on slot rows whose target is bound.
        # Ordering: sits alongside the lock/vis group so that, with those
        # hidden in slot mode, it reads as the trailing row action.
        self.exclude_blend_btn = QToolButton(self)
        self.exclude_blend_btn.setText("⊘")  # CIRCLED DIVISION SLASH
        self.exclude_blend_btn.setCheckable(True)
        self.exclude_blend_btn.setFixedSize(QSize(18, 18))
        self.exclude_blend_btn.setAutoRaise(True)
        self.exclude_blend_btn.setToolTip(self.tr("Exclude from blend (AI input only)"))
        self.exclude_blend_btn.setVisible(False)
        layout.insertWidget(layout.indexOf(self._control_group), self.exclude_blend_btn)
        self.exclude_blend_btn.toggled.connect(self._on_exclude_blend_toggled)

        # Per-row Generate button for AI Effect layers (Phase 6-g).
        # Phase 8d: SVG icon replaces the placeholder "▶" glyph — a
        # filled play triangle with a sparkle that hints at the
        # AI/generative semantics. Falls back to the original glyph
        # if the SVG fails to load (icon_loader returns None for
        # malformed SVG / missing file) so older deployments do not
        # show a blank button. The button is only shown when the row
        # is an AI Effect layer (see ``_configure_generate_button``).
        # Sits left of the alpha-lock toggle so the visibility/lock
        # cluster on the right edge stays visually grouped.
        self._row_layer_id: str = entry.layer_id
        self.generate_btn = LayerGenerateButton(
            icon_path=_AI_GENERATE_ICON_PATH,
            accent_hex=_AI_ACCENT_HEX,
            accent_dim=_AI_ACCENT_DIM,
            parent=self,
        )
        self.generate_btn.clicked.connect(self._on_generate_clicked)
        layout.insertWidget(layout.indexOf(self._control_group), self.generate_btn)
        # Phase 6-f-styling-d: 3px progress bar pinned to the row's
        # bottom edge. Positioned via resizeEvent so Qt layout stays
        # untouched (otherwise the bar would occupy row height and
        # shift every other widget down 3 px).
        self.progress_overlay = LayerProgressOverlay(accent_hex=_AI_ACCENT_HEX, parent=self)
        self.progress_fill = self.progress_overlay.fill
        # 100 ms poll while "generating" — the source layer's
        # ``ai_effect.generation.progress`` mutates outside the panel
        # rebuild cadence, so we pull instead of rebuilding the tree.
        self._progress_timer = QTimer(self)
        self._progress_timer.setInterval(100)
        self._progress_timer.timeout.connect(self._on_progress_tick)
        self._progress_fetcher: Callable[[], tuple[str | None, float | None]] | None = None
        self._apply_row_accent(entry)
        self._configure_for_slot(entry)
        self._configure_ref_count(entry)
        self._configure_progress(entry)
        self._configure_generate_button(entry)

    def _init_mask_thumbnail(self, layout: QHBoxLayout, entry: LayerEntry) -> None:
        self.mask_thumbnail_label.setFixedSize(MASK_THUMBNAIL_SIZE, MASK_THUMBNAIL_SIZE)
        self.mask_thumbnail_label.setObjectName("maskThumbnail")
        self.mask_thumbnail_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.mask_thumbnail_label.setToolTip(self.tr("Click to edit mask"))
        self.mask_thumbnail_label.clicked.connect(self.mask_thumbnail_clicked)
        layout.addWidget(self.mask_thumbnail_label)
        self._apply_mask_thumbnail(entry.mask_thumbnail, entry.mask_edit_mode)

    def _init_control_buttons(self, layout: QHBoxLayout, entry: LayerEntry) -> None:
        self._control_group = LayerRowControlGroup(
            visible=entry.visible,
            alpha_locked=entry.alpha_lock,
            locked=entry.locked,
            parent=self,
        )
        self.alpha_lock_box = self._control_group.alpha_lock_box
        self.alpha_lock_box.toggled.connect(self.alpha_lock_changed)
        self.alpha_lock_box.toggled.connect(self._on_alpha_lock_toggled)

        self.lock_box = self._control_group.lock_box
        self.lock_box.toggled.connect(self.lock_changed)
        self.lock_box.toggled.connect(self._on_lock_toggled)

        self.visibility_box = self._control_group.visibility_box
        self.visibility_box.toggled.connect(self.visibility_changed)
        self.visibility_box.toggled.connect(self._on_visibility_toggled)
        self._on_alpha_lock_toggled(entry.alpha_lock)
        self._on_lock_toggled(entry.locked)
        self._on_visibility_toggled(entry.visible)
        layout.addWidget(self._control_group)

    @staticmethod
    def _row_control_icon(icon_key: str) -> QIcon | None:
        return LayerRowControlGroup._row_control_icon(icon_key)

    def _set_row_control_icon(
        self,
        button: QToolButton,
        *,
        icon_key: str,
        fallback_text: str,
        show: bool,
    ) -> None:
        self._control_group._set_icon(
            button,
            icon_key=icon_key,
            fallback_text=fallback_text,
            show=show,
        )

    def _refresh_generate_button_icon(self) -> None:
        self.generate_btn.refresh_icon()

    def refresh_theme_icons(self) -> None:
        self._refresh_generate_button_icon()
        self._control_group.refresh_theme_icons()
        self._update_control_button_styles()

    def set_entry(self, entry: LayerEntry) -> None:
        self.name_label.setText(entry.name)
        self._set_badge(entry.badge, self._badge_tooltip(entry))
        self.set_locked(entry.locked)
        self.set_alpha_locked(entry.alpha_lock)
        self.set_visibility(entry.visible)
        self._apply_thumbnail(entry.thumbnail)
        self._apply_mask_thumbnail(entry.mask_thumbnail, entry.mask_edit_mode)
        self._apply_row_accent(entry)
        self._configure_for_slot(entry)
        self._configure_ref_count(entry)
        self._configure_progress(entry)
        self._configure_generate_button(entry)

    def set_visibility(self, visible: bool) -> None:
        if self.visibility_box.isChecked() != visible:
            self.visibility_box.blockSignals(True)
            self.visibility_box.setChecked(visible)
            self.visibility_box.blockSignals(False)
        self._on_visibility_toggled(visible)
        self._update_enabled_state(visible)

    def set_locked(self, locked: bool) -> None:
        if self.lock_box.isChecked() != locked:
            self.lock_box.blockSignals(True)
            self.lock_box.setChecked(locked)
            self.lock_box.blockSignals(False)
        self._on_lock_toggled(locked)

    def set_alpha_locked(self, alpha_locked: bool) -> None:
        if self.alpha_lock_box.isChecked() != alpha_locked:
            self.alpha_lock_box.blockSignals(True)
            self.alpha_lock_box.setChecked(alpha_locked)
            self.alpha_lock_box.blockSignals(False)
        self._on_alpha_lock_toggled(alpha_locked)

    def enterEvent(self, event: QEnterEvent) -> None:  # noqa: N802 - Qt override
        self._set_controls_hovered(True)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        cursor_pos = self.mapFromGlobal(QCursor.pos())
        self._set_controls_hovered(self.rect().contains(cursor_pos))
        super().leaveEvent(event)

    def _set_controls_hovered(self, hovered: bool) -> None:
        if self._controls_hovered == hovered:
            return
        self._controls_hovered = hovered
        self._control_group.set_hovered(hovered)
        self._update_control_button_styles()

    def _update_enabled_state(self, enabled: bool) -> None:
        self.name_label.setEnabled(enabled)
        self.thumbnail_label.setEnabled(enabled)
        self.badge_label.setEnabled(enabled)

    def _set_badge(self, badge: str, tooltip: str = "") -> None:
        text = (badge or "").strip().upper()
        self.badge_label.setText(text)
        self.badge_label.setVisible(bool(text))
        self.badge_label.setToolTip(tooltip)

    # ------------------------------------------------------------------
    # Phase 6-f-styling-a: row accent (purple AI / green output)
    # ------------------------------------------------------------------
    @staticmethod
    def _row_accent_kind(entry: LayerEntry) -> str | None:
        """Return ``"ai_effect"`` / ``"ai_effect_output"`` / ``"ai_effect_slot"`` / None.

        - Synthetic slot rows (identified by ``slot_info is not None``) win
          first — their ``layer_type`` is already a sentinel but checking
          slot_info is the canonical contract.
        - An explicit ``LayerOrigin.type == "ai_effect_output"`` wins over
          the layer_type because an AI output layer is always a regular
          raster on the canvas side — only the origin metadata flags it
          as generated.
        """
        if entry.slot_info is not None:
            return "ai_effect_slot"
        if entry.origin_type == "ai_effect_output":
            return "ai_effect_output"
        if entry.layer_type == "ai_effect":
            return "ai_effect"
        return None

    def _apply_row_accent(self, entry: LayerEntry) -> None:
        """Apply purple / green row accent styling for AI Effect family rows.

        Plain layers (raster / vector / group / effect / mask) are rendered
        with ``_row_accent == None`` and keep the default transparent
        background so the existing QTreeWidget alternating-row colours and
        selection highlight remain unchanged. The accent style paints a
        left border + subtle background tint on the ``LayerItemWidget``
        itself (not the tree item), matching
        ``AIeffect_design/01_ui_mockups/layers_panel/layers_panel_v6_final.html``.
        """
        accent = self._row_accent_kind(entry)
        self._row_accent = accent
        self.setObjectName(self._row_accent_object_name(accent))
        if accent == "ai_effect":
            self.setStyleSheet(
                "QWidget#aiEffectRow {"
                f" background-color: {_AI_BG_RGBA};"
                f" border-left: 2px solid {_AI_ACCENT_HEX};"
                " padding-left: 2px;"
                " }"
            )
        elif accent == "ai_effect_output":
            self.setStyleSheet(
                "QWidget#aiOutputRow {"
                f" background-color: {_OUTPUT_BG_RGBA};"
                f" border-left: 3px solid {_OUTPUT_ACCENT_HEX};"
                " padding-left: 1px;"
                " }"
            )
        elif accent == "ai_effect_slot":
            info = entry.slot_info
            is_required_empty = bool(
                info is not None and info.required and info.target_layer_id is None
            )
            settled_style = self._slot_row_settled_stylesheet(is_required_empty)
            self.setStyleSheet(settled_style)
            # Trigger the one-shot 0.5s flash only on False→True transitions
            # so a steady-state warning does not keep repainting on every
            # rebuild.  _last_required_empty is updated unconditionally so
            # True→False (slot got wired) is observed for future calls.
            if is_required_empty and not self._last_required_empty:
                self._schedule_required_empty_flash(settled_style)
            self._last_required_empty = is_required_empty
        else:
            self.setStyleSheet("")
            self._last_required_empty = False

    @staticmethod
    def _row_accent_object_name(accent: str | None) -> str:
        if accent == "ai_effect":
            return "aiEffectRow"
        if accent == "ai_effect_output":
            return "aiOutputRow"
        if accent == "ai_effect_slot":
            return "aiEffectSlotRow"
        return "layerRow"

    # ------------------------------------------------------------------
    # Phase 6-f-styling-c: required-empty warning stylesheet + flash
    # ------------------------------------------------------------------
    @staticmethod
    def _slot_row_settled_stylesheet(is_required_empty: bool) -> str:
        """Return the steady-state slot row stylesheet.

        Split out so the flash routine can schedule a "transition from
        bright to settled" by re-applying this exact string after 500ms.
        Keeping it static avoids capturing ``self`` in the QTimer lambda,
        which otherwise keeps the widget alive long after its tree item
        is destroyed.
        """
        if is_required_empty:
            return (
                "QWidget#aiEffectSlotRow {"
                " background-color: rgba(224, 108, 117, 26);"
                " border-left: 2px solid #e06c75;"
                " padding-left: 2px;"
                " }"
            )
        return (
            "QWidget#aiEffectSlotRow {"
            " background-color: rgba(167, 139, 250, 12);"
            f" border-left: 2px dashed {_AI_ACCENT_HEX};"
            " padding-left: 2px;"
            " }"
        )

    def _schedule_required_empty_flash(self, settled_style: str) -> None:
        """Paint a bright red flash for 500ms, then restore the settled style.

        Called only on False→True transitions of the required-empty state.
        Uses a single-shot ``QTimer`` so no persistent timer needs
        teardown when the widget is destroyed (Qt auto-deletes pending
        single-shot timers with their parent).
        """
        flash_style = (
            "QWidget#aiEffectSlotRow {"
            " background-color: rgba(224, 108, 117, 82);"
            " border-left: 2px solid #e06c75;"
            " padding-left: 2px;"
            " }"
        )
        self.setStyleSheet(flash_style)
        QTimer.singleShot(500, lambda: self._restore_slot_style(settled_style))

    def _restore_slot_style(self, settled_style: str) -> None:
        """Restore the settled slot row stylesheet after a flash.

        Guards against the case where the widget flipped out of slot
        mode (or into a non-warning state) during the 500ms window —
        only reapply if the row is still a slot showing the same warn
        accent we started with. Also tolerates the window after the
        C++ widget has been destroyed (common in unit tests that tear
        down a panel before the 500 ms fires); a destroyed QWidget
        raises ``RuntimeError`` which we swallow rather than crash.
        """
        try:
            if self._row_accent != "ai_effect_slot":
                return
            self.setStyleSheet(settled_style)
        except RuntimeError:
            return

    # ------------------------------------------------------------------
    # Phase 6-f-styling-c: ⊘ button — exclude-from-blend slot action
    # ------------------------------------------------------------------
    def _set_exclude_blend_button_state(self, *, visible: bool, checked: bool) -> None:
        """Update the ⊘ button checked state and apply active/inactive QSS.

        Signals are blocked during the programmatic update so
        ``exclude_blend_toggled`` only fires for genuine user clicks,
        never for a rebuild that merely re-applies the current
        ``SlotRef.exclude_blend`` value.
        """
        btn = self.exclude_blend_btn
        btn.blockSignals(True)
        try:
            btn.setChecked(checked)
        finally:
            btn.blockSignals(False)
        btn.setVisible(visible)
        if checked:
            btn.setStyleSheet(
                "QToolButton {"
                f" color: #ffffff; background-color: {_AI_ACCENT_HEX};"
                f" border: 1px solid {_AI_ACCENT_HEX};"
                " border-radius: 2px; font-weight: bold;"
                " }"
            )
        else:
            btn.setStyleSheet(
                "QToolButton {"
                " color: #828282; background: transparent;"
                " border: 1px solid transparent;"
                " }"
                " QToolButton:hover { background-color: #3a3a3a; color: #e5e5e5; }"
            )

    @Slot(bool)
    def _on_exclude_blend_toggled(self, checked: bool) -> None:
        """Forward a user click on ⊘ as a panel-level signal.

        ``blockSignals`` in ``_set_exclude_blend_button_state`` suppresses
        programmatic updates, so this slot only runs for genuine user
        interaction. A missing slot_info (widget in layer mode) is a
        defensive no-op.
        """
        info = self._slot_info
        if info is None:
            return
        self.exclude_blend_toggled.emit(info.effect_layer_id, info.slot_id, checked)

    # ------------------------------------------------------------------
    # Phase 6-g: Per-row Generate button (moved from Filters panel)
    # ------------------------------------------------------------------
    def _configure_generate_button(self, entry: LayerEntry) -> None:
        """Show the Generate button only on real AI Effect layer rows.

        Hidden everywhere else: synthetic slot rows already host the
        ⊘ button in that slot, output / raster / vector / group / mask
        layers don't have a generation concept attached, and the row
        accent already telegraphs which layer is the AI Effect.

        Disabled while a job is in flight (state == "generating") or
        queued so the user can't double-fire while the gateway is
        already running. The Filters panel's mid-generation lock
        applied the same rule.
        """
        is_ai_effect = entry.layer_type == "ai_effect" and entry.slot_info is None
        self._row_layer_id = entry.layer_id
        self.generate_btn.configure(
            layer_id=entry.layer_id,
            is_ai_effect=is_ai_effect,
            state=entry.ai_effect_state,
        )

    @Slot()
    def _on_generate_clicked(self) -> None:
        """Forward a click on the per-row Generate button as a signal.

        The panel relays this onto its own
        ``ai_effect_generate_requested`` signal which the host wires
        to :class:`AIEffectGenerationController` — same destination
        the Filters panel's button used to feed.
        """
        if self._row_layer_id:
            self.generate_requested.emit(self._row_layer_id)

    # ------------------------------------------------------------------
    # Phase 6-f-styling-d: reference count + generating progress bar
    # ------------------------------------------------------------------
    def _configure_ref_count(self, entry: LayerEntry) -> None:
        """Show ``xN`` link-blue chip on layers referenced by AI Effects."""
        count = int(entry.ref_count)
        if count <= 0 or entry.slot_info is not None:
            # Slot rows never show the chip — the chip is for source
            # layers, not for slot-row target references.
            self.ref_count_label.setVisible(False)
            self.ref_count_label.setText("")
            return
        self.ref_count_label.setText(f"×{count}")  # noqa: RUF001
        self.ref_count_label.setVisible(True)
        self.ref_count_label.setStyleSheet(
            "QLabel#layerRefCount {"
            " color: #61afef;"
            " background-color: rgba(97, 175, 239, 30);"
            " border: 1px solid rgba(97, 175, 239, 76);"
            " border-radius: 2px;"
            " padding: 0 3px;"
            " font-size: 9px;"
            " }"
        )

    def _configure_progress(self, entry: LayerEntry) -> None:
        """Install the 3px progress overlay while the row is generating.

        Hides and stops the poll timer in every other state (idle /
        queued / done / error) so the widget draws zero extra CPU
        when no generation is in flight.
        """
        state = entry.ai_effect_state
        if state != "generating" or entry.slot_info is not None:
            self.progress_overlay.setVisible(False)
            if self._progress_timer.isActive():
                self._progress_timer.stop()
            return
        self.progress_overlay.setVisible(True)
        self._reposition_progress_overlay()
        self._apply_progress_value(entry.ai_effect_progress)
        if not self._progress_timer.isActive() and self._progress_fetcher is not None:
            self._progress_timer.start()

    def set_progress_fetcher(
        self, fetcher: Callable[[], tuple[str | None, float | None]] | None
    ) -> None:
        """Install (or clear) the progress polling callback.

        The panel installs this in ``_populate_tree_item`` for AI Effect
        rows. Passing ``None`` detaches any previous fetcher and stops
        the timer, preventing a stale closure from outliving the source
        layer after a canvas clear.
        """
        self._progress_fetcher = fetcher
        if fetcher is None and self._progress_timer.isActive():
            self._progress_timer.stop()

    @Slot()
    def _on_progress_tick(self) -> None:
        """Poll the source layer for a fresh (state, progress) pair.

        Stops itself when the layer transitions out of "generating" so
        the 100 ms timer never out-lives the underlying job. A fetcher
        raising an exception is downgraded to "job ended" — better to
        hide the bar than loop on a bad closure.

        Also re-enables the Generate button when the layer leaves the
        in-flight states. Without this the button stays greyed out
        after error / cancel because:
        - ``AIEffectController.dispatch`` / ``force_error`` only
          mutates internal dicts; it does NOT emit ``LAYER_CHANGED``.
        - ``LayerPanel._refresh`` only runs on ``LAYER_CHANGED``.
        Success path is fine because ``_commit_success`` is followed
        by ``apply_image`` which DOES emit LAYER_CHANGED — the
        symptom shows up only on error / cancel.
        """
        fetcher = self._progress_fetcher
        if fetcher is None:
            self._progress_timer.stop()
            self.progress_overlay.setVisible(False)
            return
        try:
            state, progress = fetcher()
        except Exception:  # pragma: no cover - defensive
            logger.debug("progress fetch failed", exc_info=True)
            state, progress = (None, None)
        if state not in ("queued", "generating"):
            self._progress_timer.stop()
            self.progress_overlay.setVisible(False)
            self.generate_btn.setEnabled(True)
            return
        self._apply_progress_value(progress)

    def _apply_progress_value(self, progress: float | None) -> None:
        """Resize the fill rectangle inside the overlay.

        Determinate (0.0 <= progress <= 1.0): fill width = progress * total.
        Indeterminate (progress is None): render a muted 30% bar so the
        user still sees motion without requiring a full animation pump
        (beta scope — a proper sweep animation is deferred).
        """
        self.progress_overlay.apply_progress_value(progress, self)

    def _reposition_progress_overlay(self) -> None:
        """Pin the overlay to the bottom edge of the row."""
        self.progress_overlay.reposition_to_host(self)

    def resizeEvent(self, event: Any) -> None:  # noqa: N802
        """Reposition the progress overlay on width changes.

        Using resizeEvent (instead of a layout contribution) keeps the
        bar visually overlaid without disturbing the row's sizeHint,
        which QTreeWidget uses for row height calculations.  The fill
        rectangle is recomputed on the next poll tick or set_entry —
        here we only realign the overlay frame itself.
        """
        super().resizeEvent(event)
        if self.progress_overlay.isVisible():
            self._reposition_progress_overlay()

    # ------------------------------------------------------------------
    # Phase 6-f-styling-b: synthetic slot-row appearance
    # ------------------------------------------------------------------
    def _configure_for_slot(self, entry: LayerEntry) -> None:
        """Reshape the row when it is displaying an AI Effect input slot.

        Idempotent: called from both ``__init__`` and ``set_entry`` so the
        widget converges on the right appearance even if the same widget
        instance switches between a real-layer row and a slot row across
        rebuilds. When ``slot_info`` is None the method restores the
        default (layer-row) configuration.
        """
        info = entry.slot_info
        is_slot = info is not None
        # Remember the slot context — ``_on_exclude_blend_toggled`` runs
        # on a bare click and needs the effect_layer_id / slot_id pair
        # without traversing the tree.
        self._slot_info = info
        # Lock / visibility / alpha-lock boxes are meaningless for slots
        # (the underlying target layer still controls its own visibility
        # in its own row). Hide rather than destroy so the widget can
        # flip back to layer mode on a future set_entry call.
        self._control_group.set_controls_visible(not is_slot)
        self._control_group.setVisible(not is_slot)
        self._update_control_button_styles()
        # Mask thumbnail never applies to a slot row.
        if is_slot:
            self.mask_thumbnail_label.setVisible(False)

        if not is_slot:
            self.slot_label.setVisible(False)
            self.slot_label.setText("")
            # Clear any italic/placeholder style left over from a prior
            # slot-mode configuration on this same widget instance.
            self.name_label.setStyleSheet("")
            # ⊘ button is slot-only; hide so layer rows never grow an
            # unexpected trailing button when recycled.
            self._set_exclude_blend_button_state(visible=False, checked=False)
            return

        # In slot mode the Type badge (e.g. "AI"/"FX") is replaced by a
        # compact slot-id label with a distinct style so the user can
        # tell "Source" from "Mask" at a glance.
        self._set_badge("", "")
        assert info is not None  # Narrow for mypy — is_slot implies info
        suffix = " *" if info.required else ""
        self.slot_label.setText(f"{info.label}{suffix}")
        self.slot_label.setVisible(True)
        self.slot_label.setStyleSheet(
            "QLabel#aiEffectSlotLabel {"
            f" color: {_AI_ACCENT_HEX};"
            " background-color: rgba(167, 139, 250, 38);"
            " padding: 0 3px;"
            " border-radius: 2px;"
            " font-size: 9px;"
            " font-weight: 600;"
            " }"
        )
        # When the slot target is unresolved, italicise the placeholder
        # name so it reads as "absent" rather than as a real layer name.
        is_empty = info.target_layer_id is None
        italic_style = "QLabel { font-style: italic; color: #828282; }" if is_empty else ""
        self.name_label.setStyleSheet(italic_style)
        # ⊘ toggle reflects ``exclude_blend`` — only meaningful for a
        # bound slot, so the button stays hidden for empty slots.
        self._set_exclude_blend_button_state(
            visible=not is_empty,
            checked=info.exclude_blend,
        )

    @staticmethod
    def _badge_tooltip(entry: LayerEntry) -> str:
        """Compose a hover tooltip for the layer-type badge.

        Today only AI Effect layers have a runtime state worth surfacing;
        other layer types get an empty tooltip (current behaviour).
        """
        if entry.layer_type == "ai_effect" and entry.ai_effect_state:
            return f"AI Effect: {entry.ai_effect_state}"
        return ""

    @Slot(bool)
    def _on_visibility_toggled(self, checked: bool) -> None:
        self._update_control_button_styles()

    @Slot(bool)
    def _on_alpha_lock_toggled(self, checked: bool) -> None:
        self._update_control_button_styles()

    @Slot(bool)
    def _on_lock_toggled(self, checked: bool) -> None:
        # ``@Slot(bool)`` keeps ``connect`` from registering a dynamic
        # slot. Without the decoration each LayerItemWidget instance
        # emits a Qt warning ("Registering dynamic slot
        # _on_lock_toggled(bool)…") at signal-bind time — and the
        # layer panel rebuilds many of these per refresh, so a single
        # layer click produced a flood of identical warnings in the
        # log. The behaviour itself was unaffected; this is purely a
        # log-noise fix.
        self._update_control_button_styles()

    def _update_control_button_styles(self) -> None:
        self._control_group.update_styles()

    def _update_control_button_icons(self) -> None:
        self._control_group._update_icons()

    def _visibility_button_style(self, visible: bool) -> str:
        return self._control_group._visibility_button_style(visible)

    def _state_button_style(
        self,
        *,
        active: bool,
        active_color: str,
        inactive_color: str,
        bold: bool,
    ) -> str:
        return self._control_group._state_button_style(
            active=active,
            active_color=active_color,
            inactive_color=inactive_color,
            bold=bold,
        )

    def _apply_thumbnail(self, pixmap: QPixmap | None) -> None:
        if pixmap is None or pixmap.isNull():
            self.thumbnail_label.setPixmap(QPixmap())
            return
        scaled = pixmap.scaled(
            THUMBNAIL_SIZE,
            THUMBNAIL_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.thumbnail_label.setPixmap(scaled)

    def _apply_mask_thumbnail(self, pixmap: QPixmap | None, mask_edit_mode: bool) -> None:
        has_mask = pixmap is not None and not pixmap.isNull()
        self.mask_thumbnail_label.setVisible(has_mask)
        if pixmap is None or pixmap.isNull():
            # No mask: layer thumbnail always active style
            self.thumbnail_label.setStyleSheet(
                f"background-color: #3a3a3a; {_THUMB_BORDER_INACTIVE}"
            )
            return
        scaled = pixmap.scaled(
            MASK_THUMBNAIL_SIZE,
            MASK_THUMBNAIL_SIZE,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.FastTransformation,
        )
        self.mask_thumbnail_label.setPixmap(scaled)
        # Highlight the active target (layer or mask)
        if mask_edit_mode:
            self.thumbnail_label.setStyleSheet(
                f"background-color: #3a3a3a; {_THUMB_BORDER_INACTIVE}"
            )
            self.mask_thumbnail_label.setStyleSheet(
                f"background-color: #3a3a3a; {_THUMB_BORDER_ACTIVE}"
            )
        else:
            self.thumbnail_label.setStyleSheet(f"background-color: #3a3a3a; {_THUMB_BORDER_ACTIVE}")
            self.mask_thumbnail_label.setStyleSheet(
                f"background-color: #3a3a3a; {_THUMB_BORDER_INACTIVE}"
            )


class LayerPanel(QWidget):
    """Panel used to display and manage document layers."""

    # Legacy index-based signals (deprecated, kept for backward compat).
    # New internal subscribers must use the ID-based signals below; the legacy
    # signals are emitted only as a compatibility bridge for top-level rows.
    layer_selected = Signal(int)
    layer_visibility_changed = Signal(int, bool)
    layer_locked_changed = Signal(int, bool)
    layer_opacity_changed = Signal(int, float)
    layer_added = Signal()
    mask_layer_added = Signal()
    layer_deleted = Signal(int)
    layer_reordered = Signal(int, int)

    layer_duplicated = Signal()

    # ID-based signals (Phase 9b)
    layer_selected_by_id = Signal(str)
    layer_visibility_changed_by_id = Signal(str, bool)
    layer_locked_changed_by_id = Signal(str, bool)
    layer_alpha_lock_changed_by_id = Signal(str, bool)
    layer_opacity_changed_by_id = Signal(str, float)
    layer_deleted_by_id = Signal(str)
    layer_reparented = Signal(str, str, int)  # layer_id, new_parent_id ("" = root), position

    # Phase 6-f-styling-c: AI Effect slot-row ⊘ toggle
    # (effect_layer_id, slot_id, new_exclude_blend)
    ai_effect_exclude_blend_toggled = Signal(str, str, bool)

    # Drag-and-drop bind for AI Effect input slots.
    # Payload: (effect_layer_id, slot_internal_id, source_layer_id).
    # Fires when the user drops a real layer onto a synthetic slot row;
    # the host wires this to ``FilterPanel.apply_slot_bind`` so the
    # binding flows through the same write-back path as the combo
    # picker (``FilterPanel._write_slot_ref``).
    ai_effect_slot_bind_dropped = Signal(str, str, str)

    # AI Effects Manager preset dropped onto the layer list.
    # Payload: preset_id. The panel also routes it through the same
    # applier as the Add AI Effect Layer footer menu.
    ai_effect_preset_dropped = Signal(str)

    # Phase 6-g: Per-row Generate button click. Carries the AI Effect
    # ``layer_id`` whose generation should be kicked off — wired in
    # ``app.py`` to the same ``_on_ai_effect_generate_requested`` slot
    # the (now-removed) Filters panel button used to feed.
    ai_effect_generate_requested = Signal(str)

    def __init__(self, host_api: HostAPI | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host_api: HostAPI | None = host_api
        self._entries: list[LayerEntry] = []
        self._widget_map: dict[str, LayerItemWidget] = {}
        self._item_map: dict[str, QTreeWidgetItem] = {}
        self._all_entries: dict[str, LayerEntry] = {}
        self._thumbnail_cache: dict[str, tuple[int, int, QPixmap]] = (
            {}
        )  # lid → (tex_ver, cache_ver, pixmap)
        self._layer_op_busy = False  # guard against concurrent layer operations
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(30)  # coalesce within 30ms
        self._refresh_timer.timeout.connect(self._on_refresh_timeout)
        self._placeholder_seq = 0
        self._active_layer_id: str | None = None
        self._selected_layer_id: str | None = None
        self._suppress_reorder_signal = False
        self._suppress_opacity_signal = False
        self._suppress_blend_signal = False
        self._event_bus = get_extended_event_bus()
        self._event_bus_ids: set[int] = set()
        self._translation_binder = TranslationBinder(self._translate_runtime)
        self._translations_ready = False
        self._event_handlers: list[Any] = []
        self._event_tokens: list[Any] = []
        self._ai_effect_preset_applier: Callable[[str], Any] | None = None
        self._ai_effect_preset_library_getter: Callable[[], list[Any]] | None = None
        self._ai_effect_menu_refs: list[QMenu] = []
        self._ai_effect_menu_builder = LayerPanelAIEffectMenuBuilder(self)
        self._context_menu_builder = LayerPanelContextMenuBuilder(self)
        self._thumbnail_manager = LayerPanelThumbnailManager(self, THUMBNAIL_SIZE)
        self._lock_tooltip = self._ts("Toggle layer lock")
        self._visibility_tooltip = self._ts("Toggle layer visibility")
        self._blend_tooltip = self._ts("Layer effects between this layer and layers below")
        self._opacity_tooltip = self._ts("Layer opacity")
        self._translation_binder.register(self._set_lock_tooltip, self._lock_tooltip)
        self._translation_binder.register(self._set_visibility_tooltip, self._visibility_tooltip)
        self._translation_binder.register(self._set_blend_tooltip, self._blend_tooltip)
        self._translation_binder.register(self._set_opacity_tooltip, self._opacity_tooltip)
        self._blend_mode_labels = {
            BlendMode.PASS_THROUGH.value: self._ts("Pass Through"),
            BlendMode.NORMAL.value: self._ts("Normal"),
            BlendMode.DISSOLVE.value: self._ts("Dissolve"),
            BlendMode.DARKEN.value: self._ts("Darken"),
            BlendMode.MULTIPLY.value: self._ts("Multiply"),
            BlendMode.COLOR_BURN.value: self._ts("Color Burn"),
            BlendMode.LINEAR_BURN.value: self._ts("Linear Burn"),
            BlendMode.DARKER_COLOR.value: self._ts("Darker Color"),
            BlendMode.LIGHTEN.value: self._ts("Lighten"),
            BlendMode.SCREEN.value: self._ts("Screen"),
            BlendMode.COLOR_DODGE.value: self._ts("Color Dodge"),
            BlendMode.LINEAR_DODGE.value: self._ts("Linear Dodge (Add)"),
            BlendMode.LIGHTER_COLOR.value: self._ts("Lighter Color"),
            BlendMode.OVERLAY.value: self._ts("Overlay"),
            BlendMode.SOFT_LIGHT.value: self._ts("Soft Light"),
            BlendMode.HARD_LIGHT.value: self._ts("Hard Light"),
            BlendMode.VIVID_LIGHT.value: self._ts("Vivid Light"),
            BlendMode.LINEAR_LIGHT.value: self._ts("Linear Light"),
            BlendMode.PIN_LIGHT.value: self._ts("Pin Light"),
            BlendMode.HARD_MIX.value: self._ts("Hard Mix"),
            BlendMode.DIFFERENCE.value: self._ts("Difference"),
            BlendMode.EXCLUSION.value: self._ts("Exclusion"),
            BlendMode.SUBTRACT.value: self._ts("Subtract"),
            BlendMode.DIVIDE.value: self._ts("Divide"),
            BlendMode.HUE.value: self._ts("Hue"),
            BlendMode.SATURATION.value: self._ts("Saturation"),
            BlendMode.COLOR.value: self._ts("Color"),
            BlendMode.LUMINOSITY.value: self._ts("Luminosity"),
        }

        self._setup_ui()
        self._translations_ready = True
        self._connect_ui()
        self._subscribe_events()
        self._add_default_layers()

    # ------------------------------------------------------------------
    # UI setup
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        self._setup_layer_layout_base(layout)
        self._setup_blend_row(layout)
        self._setup_opacity_row(layout)
        self._channels_tabs = LayerChannelsTabs(self._host_api, self)
        layout.addWidget(self._channels_tabs, 1)
        self._setup_layer_list(self._channels_tabs.layers_layout)
        self._setup_layer_buttons(self._channels_tabs.layers_layout)

    def _setup_layer_layout_base(self, layout: QVBoxLayout) -> None:
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(6)

    def _setup_blend_row(self, layout: QVBoxLayout) -> None:
        blend_row = QHBoxLayout()
        blend_row.setSpacing(6)
        blend_text = self._ts("Layer Effects")
        blend_label = QLabel(blend_text, self)
        blend_label.setWordWrap(True)
        blend_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        self._translation_binder.register(blend_label.setText, blend_text)
        blend_row.addWidget(blend_label)

        self._blend_combo = QComboBox(self)
        self._blend_combo.setMinimumWidth(0)
        self._blend_combo.setMinimumContentsLength(6)
        self._blend_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon
        )
        self._blend_combo.setToolTip(self._blend_tooltip)
        self._translation_binder.register(self._blend_combo.setToolTip, self._blend_tooltip)
        self._populate_blend_modes()
        blend_row.addWidget(self._blend_combo, 1)
        layout.addLayout(blend_row)

    def _setup_opacity_row(self, layout: QVBoxLayout) -> None:
        opacity_row = QHBoxLayout()
        opacity_row.setSpacing(6)
        opacity_text = self._ts("Opacity")
        opacity_label = QLabel(opacity_text, self)
        opacity_label.setWordWrap(True)
        opacity_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
        self._translation_binder.register(opacity_label.setText, opacity_text)
        opacity_row.addWidget(opacity_label)

        self._opacity_slider = QSlider(Qt.Orientation.Horizontal, self)
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setSingleStep(1)
        self._opacity_slider.setPageStep(5)
        self._opacity_slider.setValue(100)
        self._opacity_slider.setToolTip(self._opacity_tooltip)
        self._translation_binder.register(self._opacity_slider.setToolTip, self._opacity_tooltip)
        opacity_row.addWidget(self._opacity_slider, 1)

        self._opacity_value = QLabel("100%", self)
        self._opacity_value.setFixedWidth(36)
        self._opacity_value.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        opacity_row.addWidget(self._opacity_value)
        layout.addLayout(opacity_row)

    def _setup_layer_list(self, layout: QVBoxLayout) -> None:
        separator_top = QFrame(self)
        separator_top.setFrameShape(QFrame.Shape.HLine)
        separator_top.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator_top)

        self._list = LayerTreeWidget(self)
        self._list.setHeaderHidden(True)
        self._list.setColumnCount(1)
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self._list.setAlternatingRowColors(True)
        self._list.setRootIsDecorated(True)
        self._list.setIndentation(16)
        self._list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self._list.setDragEnabled(True)
        self._list.setAcceptDrops(True)
        self._list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        # Suppress Qt's built-in item highlight (selected/hover) during drag.
        # LayerItemWidget handles active-layer indication via thumbnail border.
        # selected:active gets a faint tint so the dragged item is identifiable.
        self._list.setStyleSheet(
            "QTreeWidget::item:hover { background: transparent; }"
            "QTreeWidget::item:selected { background: transparent; }"
            "QTreeWidget::item:selected:active { background: rgba(79,195,247,18); }"
        )
        layout.addWidget(self._list)

    _LAYER_ICON_DIR = Path(__file__).resolve().parent.parent
    _LAYER_ICON_MAP: ClassVar[dict[str, tuple[str, QStyle.StandardPixmap]]] = {
        "add": ("icons/layer_add.svg", QStyle.StandardPixmap.SP_FileDialogNewFolder),
        "delete": ("icons/layer_delete.svg", QStyle.StandardPixmap.SP_TrashIcon),
        "duplicate": ("icons/layer_duplicate.svg", QStyle.StandardPixmap.SP_FileDialogDetailedView),
        "mask": ("icons/layer_mask.svg", QStyle.StandardPixmap.SP_DialogApplyButton),
        "filter": ("icons/layer_effect.svg", QStyle.StandardPixmap.SP_DesktopIcon),
        "ai_effect": (
            "resources/layer_icons/layer_ai_effect.svg",
            QStyle.StandardPixmap.SP_ComputerIcon,
        ),
        "group": ("resources/layer_icons/layer_group.svg", QStyle.StandardPixmap.SP_DirIcon),
        "vector": (
            "resources/layer_icons/layer_vector.svg",
            QStyle.StandardPixmap.SP_ArrowRight,
        ),
    }

    def _setup_layer_buttons(self, layout: QVBoxLayout) -> None:
        separator_bottom = QFrame(self)
        separator_bottom.setFrameShape(QFrame.Shape.HLine)
        separator_bottom.setFrameShadow(QFrame.Shadow.Sunken)
        layout.addWidget(separator_bottom)

        button_row = QHBoxLayout()
        button_row.setSpacing(4)

        add_text = self._ts("Add")
        self._add_btn = self._create_compact_icon_button(add_text, "add")
        button_row.addWidget(self._add_btn)

        delete_text = self._ts("Delete")
        self._delete_btn = self._create_compact_icon_button(delete_text, "delete")
        button_row.addWidget(self._delete_btn)

        duplicate_text = self._ts("Duplicate")
        self._duplicate_btn = self._create_compact_icon_button(duplicate_text, "duplicate")
        self._duplicate_btn.setEnabled(False)
        button_row.addWidget(self._duplicate_btn)

        mask_text = self._ts("Mask")
        self._add_mask_btn = self._create_compact_icon_button(mask_text, "mask")
        button_row.addWidget(self._add_mask_btn)

        filter_text = self._ts("Add Filter Layer")
        self._add_filter_btn = self._create_compact_icon_button(filter_text, "filter")
        self._add_effect_btn = self._add_filter_btn  # Legacy widget lookup.
        button_row.addWidget(self._add_filter_btn)

        ai_effect_text = self._ts("Add AI Effect Layer")
        self._add_ai_effect_btn = self._create_compact_icon_button(ai_effect_text, "ai_effect")
        self._add_ai_effect_btn.setEnabled(False)
        button_row.addWidget(self._add_ai_effect_btn)

        group_text = self._ts("Group")
        self._group_btn = self._create_compact_icon_button(group_text, "group")
        self._group_btn.setEnabled(False)
        button_row.addWidget(self._group_btn)

        vector_text = self._ts("Add Vector Layer")
        self._add_vector_btn = self._create_compact_icon_button(vector_text, "vector")
        button_row.addWidget(self._add_vector_btn)

        button_row.addStretch()

        layout.addLayout(button_row)

    def _create_compact_icon_button(
        self,
        tooltip: str,
        icon_key: str,
    ) -> QToolButton:
        button = QToolButton(self)
        button.setProperty("loomstroke_layer_icon_key", icon_key)
        self._apply_compact_icon_button_icon(button, icon_key)
        self._translation_binder.register(button.setToolTip, tooltip)
        button.setIconSize(QSize(16, 16))
        button.setFixedSize(QSize(24, 24))
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
        button.setAutoRaise(True)
        return button

    def _apply_compact_icon_button_icon(self, button: QToolButton, icon_key: str) -> None:
        from loomstroke.ui.icon_loader import load_svg_icon  # noqa: PLC0415

        svg_name, fallback = self._LAYER_ICON_MAP[icon_key]
        svg_icon = load_svg_icon(self._LAYER_ICON_DIR / svg_name)
        button.setIcon(svg_icon if svg_icon is not None else self.style().standardIcon(fallback))

    def _refresh_theme_icons(self) -> None:
        if not getattr(self, "_translations_ready", False) or not hasattr(
            self,
            "_widget_map",
        ):
            return
        clear_layer_row_icon_cache()
        for attr_name in (
            "_add_btn",
            "_delete_btn",
            "_duplicate_btn",
            "_add_mask_btn",
            "_add_filter_btn",
            "_add_ai_effect_btn",
            "_group_btn",
            "_add_vector_btn",
        ):
            button = getattr(self, attr_name, None)
            if not isinstance(button, QToolButton):
                continue
            icon_key = button.property("loomstroke_layer_icon_key")
            if isinstance(icon_key, str):
                self._apply_compact_icon_button_icon(button, icon_key)
        for widget in self._widget_map.values():
            widget.refresh_theme_icons()

    def _connect_ui(self) -> None:
        self._list.currentItemChanged.connect(self._on_item_changed)
        self._list.layer_drop_requested.connect(self._on_layer_drop_requested)
        # Bubble slot-bind drop events up so the host's app.py wiring
        # picks them up alongside ``ai_effect_exclude_blend_toggled``.
        self._list.ai_effect_slot_bind_dropped.connect(self.ai_effect_slot_bind_dropped)
        self._list.ai_effect_preset_dropped.connect(self._on_ai_effect_preset_dropped)
        self._list.customContextMenuRequested.connect(self._on_context_menu_requested)
        self._list.itemDoubleClicked.connect(self._on_item_double_clicked)
        self._blend_combo.currentIndexChanged.connect(self._on_blend_changed)
        self._opacity_slider.valueChanged.connect(self._on_opacity_slider_changed)

        self._add_btn.clicked.connect(self._on_add_clicked)
        self._add_mask_btn.clicked.connect(self._on_add_mask_clicked)
        self._add_filter_btn.clicked.connect(self._on_add_filter_layer_clicked)
        self._add_ai_effect_btn.clicked.connect(self._on_add_ai_effect_layer_clicked)
        self._delete_btn.clicked.connect(self._on_delete_clicked)
        self._duplicate_btn.clicked.connect(self._on_duplicate_clicked)
        self._group_btn.clicked.connect(self._on_group_clicked)
        self._add_vector_btn.clicked.connect(self._on_add_vector_layer_clicked)
        self._list.itemSelectionChanged.connect(self._on_selection_changed)

    def _subscribe_events(self) -> None:
        self._subscribe_to_bus(self._event_bus)
        host = self._host_api
        host_bus = getattr(host, "events", None) if host else None
        if host_bus is not None:
            self._subscribe_to_bus(host_bus)

    def _subscribe_to_bus(self, bus: Any) -> None:
        if bus is None or id(bus) in self._event_bus_ids:
            return
        subscribe = getattr(bus, "on", None) or getattr(bus, "subscribe", None)
        if not callable(subscribe):
            return

        language_handler = self._on_language_changed
        if language_handler not in self._event_handlers:
            self._event_handlers.append(language_handler)
        self._event_tokens.append(subscribe(Events.LANGUAGE_CHANGED, language_handler))

        page_handler = self._on_active_page_changed
        if page_handler not in self._event_handlers:
            self._event_handlers.append(page_handler)
        self._event_tokens.append(subscribe(Events.ACTIVE_PAGE_CHANGED, page_handler))
        self._event_bus_ids.add(id(bus))

    def _on_language_changed(self, _payload: Any) -> None:
        self.refresh_translations()

    def _on_active_page_changed(self, _payload: Any) -> None:
        self.refresh()

    def refresh_translations(self) -> None:
        if not getattr(self, "_translations_ready", False):
            return
        current_mode = self._blend_combo.currentData()
        self._translation_binder.set_translator(self._translate_runtime)
        self._translation_binder.retranslate()
        self._channels_tabs.refresh_translations()
        self._populate_blend_modes()
        if isinstance(current_mode, str):
            self._set_blend_mode(current_mode)
        self._apply_row_tooltips()
        from .comic_frame_rows import refresh_comic_rows  # noqa: PLC0415

        refresh_comic_rows(self)

    def _apply_row_tooltips(self) -> None:
        for widget in self._widget_map.values():
            widget.lock_box.setToolTip(self._lock_tooltip)
            widget.visibility_box.setToolTip(self._visibility_tooltip)

    def _set_lock_tooltip(self, text: str) -> None:
        self._lock_tooltip = text

    def _set_visibility_tooltip(self, text: str) -> None:
        self._visibility_tooltip = text

    def _set_blend_tooltip(self, text: str) -> None:
        self._blend_tooltip = text

    def _set_opacity_tooltip(self, text: str) -> None:
        self._opacity_tooltip = text

    def _translate_runtime(self, text: str) -> str:
        return translate_with_fallback("LayerPanel", text)

    def _ts(self, text: str) -> str:
        return translated_literal(self._translate_runtime, text)

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        event_type = event.type()
        if event_type == QEvent.Type.LanguageChange:
            self.refresh_translations()
        elif event_type in (
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.PaletteChange,
        ):
            self._refresh_theme_icons()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_host_api(self, host_api: HostAPI | None) -> None:
        if host_api is self._host_api:
            return
        self._host_api = host_api
        self._channels_tabs.set_host_api(host_api)
        self._subscribe_events()
        self._add_default_layers()

    def refresh_thumbnail(self, layer_id: str) -> None:
        """Regenerate and apply the thumbnail for a single layer.

        Much cheaper than a full ``refresh()`` — reuses the existing Qt tree
        widget items and only rescales the one affected layer's image.  Used
        after paint strokes so the main thread is not blocked by a full panel
        rebuild.
        """
        widget = self._widget_map.get(layer_id)
        if widget is None:
            return
        canvas = getattr(self._host_api, "canvas", None) if self._host_api else None
        if canvas is None:
            return
        # Find layer by ID (flat + nested search)
        layer = self._find_canvas_layer(canvas, layer_id)
        if layer is None:
            return
        # Evict stale cache entry so _layer_thumbnail regenerates it
        self._thumbnail_cache.pop(layer_id, None)
        thumb = self._layer_thumbnail(layer)
        widget._apply_thumbnail(thumb)

    def _find_canvas_layer(self, canvas: Any, layer_id: str) -> Any:
        """Return the layer object with *layer_id* from *canvas*, or None."""

        def _search(layers: list[Any]) -> Any:
            for layer in layers:
                if getattr(layer, "id", None) == layer_id:
                    return layer
                children = getattr(layer, "children", None)
                if children:
                    found = _search(children)
                    if found is not None:
                        return found
            return None

        return _search(getattr(canvas, "_layers", []))

    def refresh(self) -> None:
        """Schedule a coalesced panel rebuild.

        Rapid successive calls (e.g. layer-add spam) are merged into a
        single rebuild that fires after the timer elapses, keeping the
        main thread responsive.
        """
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()

    def _on_refresh_timeout(self) -> None:
        """Called when the coalesced refresh timer fires."""
        self._add_default_layers()
        self._layer_op_busy = False

    def _acquire_layer_op(self) -> bool:
        """Try to acquire the layer-operation guard.

        Returns True if the operation may proceed, False if another
        operation is still in progress and the caller should bail out.
        A safety timeout ensures the guard is released even if the
        expected refresh/release path is never reached.
        """
        if self._layer_op_busy:
            return False
        self._layer_op_busy = True
        # Safety timeout: release guard after 500ms even if no refresh arrives
        QTimer.singleShot(500, self._safety_release_layer_op)
        return True

    def _release_layer_op(self) -> None:
        """Release the layer-operation guard."""
        self._layer_op_busy = False

    def _safety_release_layer_op(self) -> None:
        """Fallback release in case the normal release path was missed."""
        if self._layer_op_busy:
            logger.debug("layer_op guard safety release triggered")
            self._layer_op_busy = False

    def select_layer_by_id(self, layer_id: str) -> bool:
        """Select a layer by ID in the tree. Returns True if found."""
        from .comic_balloon_rows import logical_layer_id  # noqa: PLC0415

        layer_id = logical_layer_id(getattr(self, "_layer_lookup", {}), layer_id)
        item = self._item_map.get(layer_id)
        if item is None:
            return False
        self._list.blockSignals(True)
        self._list.setCurrentItem(item)
        self._list.blockSignals(False)
        self._selected_layer_id = layer_id
        return True

    # ------------------------------------------------------------------
    # Data population
    # ------------------------------------------------------------------

    def _add_default_layers(self) -> None:
        entries = self._entries_from_host()
        if not entries:
            entries = self._placeholder_entries()
        self._apply_entries(entries)
        # Prune thumbnail cache for removed layers
        if self._all_entries:
            stale = self._thumbnail_cache.keys() - self._all_entries.keys()
            for key in stale:
                del self._thumbnail_cache[key]

    def _entries_from_host(self) -> list[LayerEntry]:
        host = self._host_api
        if not host:
            return []
        canvas = getattr(host, "canvas", None)
        if canvas is None:
            return []

        # Capture global cache version for thumbnail cache keying
        self._current_cache_version: int = getattr(canvas, "_layer_cache_version", 0)

        layers: list[Any] | None = getattr(canvas, "_layers", None)
        # Flat UUID→layer map for synthetic slot row target resolution
        # (Phase 6-f-styling-b). Built once per rebuild to avoid O(n^2)
        # lookups while walking nested groups.
        self._layer_lookup = self._build_layer_lookup_map(
            layers if isinstance(layers, list) else []
        )
        # Snapshot the canvas ``_reference_registry`` (Phase 6-f-styling-d).
        # Absent when no AI Effect has ever been added — treated as a
        # zero-count oracle so plain documents incur no extra work.
        self._ref_registry = getattr(canvas, "_reference_registry", None)
        result: list[LayerEntry] = []
        if isinstance(layers, list) and layers:
            for layer in reversed(layers):
                from .comic_balloon_rows import visible_row  # noqa: PLC0415

                if not visible_row(layer):
                    continue
                if (getattr(layer, "metadata", None) or {}).get(
                    "loomstroke.comictools.frame_part", {}
                ).get("role") == "retired_panel":
                    continue
                result.append(self._entry_from_layer(layer))
        elif hasattr(canvas, "get_layers"):
            try:
                refs = canvas.get_layers()
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Failed to query layers from canvas: %s", exc)
                return []
            for ref in reversed(refs):
                result.append(self._entry_from_layer(ref))

        active_layer = getattr(canvas, "active_layer", None)
        from .comic_balloon_rows import logical_layer_id  # noqa: PLC0415

        self._active_layer_id = logical_layer_id(
            self._layer_lookup, getattr(active_layer, "id", None)
        )
        return result

    def _entry_from_layer(  # noqa: PLR0912, PLR0915 - layer → entry aggregation
        self, layer: Any
    ) -> LayerEntry:
        """Build a LayerEntry (with children) from a canvas layer object."""
        layer_id = getattr(layer, "id", "")
        name = getattr(layer, "name", "") or layer_id or "Layer"
        visible = bool(getattr(layer, "visible", True))
        opacity = float(getattr(layer, "opacity", 1.0))
        locked = bool(getattr(layer, "locked", False))
        alpha_lock = bool(getattr(layer, "alpha_lock", False))
        blend_mode_value = getattr(layer, "blend_mode", "normal")
        thumbnail = self._layer_thumbnail(layer)
        badge = self._layer_badge(layer)
        layer_type = str(getattr(layer, "layer_type", "raster"))
        if isinstance(blend_mode_value, BlendMode):
            blend_mode = blend_mode_value.value
        else:
            blend_mode = str(blend_mode_value)

        # Annotate display name
        display_name = name
        if bool(getattr(layer, "clipping", False)):
            display_name = f"\u231f {display_name}"

        # Mask thumbnail (shown as separate widget, not text)
        mask_thumbnail: QPixmap | None = None
        mask_edit_mode = False
        layer_mask = getattr(layer, "layer_mask", None)
        if isinstance(layer_mask, QImage) and not layer_mask.isNull():
            mask_thumbnail = self._thumbnail_from_qimage(layer_mask)
            mask_edit_mode = bool(getattr(layer, "mask_edit_mode", False))

        # Build children entries recursively
        children_entries: list[LayerEntry] | None = None
        from .comic_balloon_rows import row_children, visible_row  # noqa: PLC0415

        raw_children = row_children(layer)
        if raw_children:
            children_entries = [
                self._entry_from_layer(child)
                for child in reversed(raw_children)
                if visible_row(child)
            ]

        # Phase 6-f-styling-b: append synthetic slot rows under AI Effect
        # layers so the mockup's "input slot child rows" visual is driven
        # directly by ``AIEffectState`` without polluting the canvas tree.
        if layer_type == "ai_effect":
            slot_children = self._build_slot_entries(layer)
            if slot_children:
                if children_entries is None:
                    children_entries = []
                children_entries.extend(slot_children)

        # AI Effect runtime state (populated only for ai_effect layers).
        ai_effect_state: str | None = None
        ai_effect_progress: float | None = None
        if layer_type == "ai_effect":
            ai_effect = getattr(layer, "ai_effect", None)
            state_value = getattr(ai_effect, "state", None)
            if isinstance(state_value, str) and state_value:
                ai_effect_state = state_value
            # Snapshot live progress for determinate display (Phase
            # 6-f-styling-d). The widget's 100 ms poller refreshes this
            # between rebuilds so the bar animates smoothly.
            generation = getattr(ai_effect, "generation", None)
            raw_progress = getattr(generation, "progress", None)
            if isinstance(raw_progress, (int, float)):
                ai_effect_progress = float(raw_progress)

        # Phase 6-f-styling-d: reference count badge. Only queried when a
        # registry exists on the canvas — the lookup is O(1) but calling
        # ``ref_count`` on every rebuild for every layer would waste
        # cycles in documents that never touch AI Effect.
        ref_count = 0
        registry = getattr(self, "_ref_registry", None)
        if registry is not None:
            try:
                ref_count = int(registry.ref_count(layer_id))
            except Exception:  # pragma: no cover - defensive
                logger.debug("ref_count lookup failed for %s", layer_id, exc_info=True)
                ref_count = 0

        # Layer origin metadata (``LayerOrigin.type``). Only the value
        # field is extracted — the rest of the LayerOrigin record is
        # never rendered in the panel.
        origin_type: str | None = None
        origin = getattr(layer, "origin", None)
        origin_type_value = getattr(origin, "type", None)
        if isinstance(origin_type_value, str) and origin_type_value:
            origin_type = origin_type_value

        entry = LayerEntry(
            layer_id,
            display_name,
            visible,
            opacity,
            locked,
            alpha_lock,
            blend_mode,
            thumbnail,
            badge,
            layer_type,
            children_entries,
            mask_thumbnail,
            mask_edit_mode,
            ai_effect_state,
            origin_type,
            slot_info=None,
            ref_count=ref_count,
            ai_effect_progress=ai_effect_progress,
        )
        from .comic_balloon_rows import decorate_balloon_entry  # noqa: PLC0415
        from .comic_frame_rows import decorate_frame_entry  # noqa: PLC0415

        entry = decorate_balloon_entry(entry, layer, self)

        return decorate_frame_entry(
            entry, layer, self._entry_from_layer, LayerEntry, self._layer_lookup
        )

    @staticmethod
    def _build_layer_lookup_map(layers: Any) -> dict[str, Any]:
        """Flatten the canvas layer tree into a UUID→layer map.

        Used by :meth:`_build_slot_entries` to resolve a ``SlotRef.layer_id``
        back to its source layer (for thumbnail + name display). Group
        children are walked recursively so a slot can reference a layer
        nested inside a group.
        """
        result: dict[str, Any] = {}

        def _walk(items: Any) -> None:
            if not isinstance(items, list):
                return
            for item in items:
                lid = getattr(item, "id", None)
                if isinstance(lid, str) and lid:
                    result[lid] = item
                _walk(getattr(item, "children", None))

        _walk(layers)
        return result

    def _build_slot_entries(self, effect_layer: Any) -> list[LayerEntry]:
        """Return synthetic ``LayerEntry`` rows for each declared input slot.

        Returns an empty list when:
        - The layer has no ``ai_effect`` attached.
        - The manifest snapshot is malformed or lacks ``input_slots``.
        - All entries in ``input_slots`` are malformed.

        Exceptions are swallowed (with a debug log) rather than propagated
        because a broken manifest should degrade the panel display to
        "no slot rows" rather than bring down the entire rebuild.
        """
        ai_state = getattr(effect_layer, "ai_effect", None)
        if ai_state is None:
            return []
        snapshot = getattr(ai_state, "preset_snapshot", None)
        if not isinstance(snapshot, dict):
            return []
        manifest = snapshot.get("manifest")
        if not isinstance(manifest, dict):
            return []
        slots_meta = manifest.get("input_slots")
        if not isinstance(slots_meta, list) or not slots_meta:
            return []

        slot_refs = getattr(ai_state, "slot_refs", None) or {}
        effect_id = str(getattr(effect_layer, "id", "") or "")
        entries: list[LayerEntry] = []
        for meta in slots_meta:
            if not isinstance(meta, dict):
                continue
            internal_id = meta.get("internal_id")
            if not isinstance(internal_id, str) or not internal_id:
                continue
            label = meta.get("display_name") or internal_id
            required = bool(meta.get("required", False))
            ref = slot_refs.get(internal_id) if isinstance(slot_refs, dict) else None
            target_id: str | None = None
            exclude_blend = False
            if ref is not None:
                raw_target = getattr(ref, "layer_id", None)
                if isinstance(raw_target, str) and raw_target:
                    target_id = raw_target
                exclude_blend = bool(getattr(ref, "exclude_blend", False))

            slot_info = SlotDisplayInfo(
                effect_layer_id=effect_id,
                slot_id=internal_id,
                label=str(label),
                required=required,
                target_layer_id=target_id,
                exclude_blend=exclude_blend,
            )
            display_name, target_thumb = self._resolve_slot_target(target_id, required)
            synthetic_id = f"{_SLOT_ROW_ID_PREFIX}{effect_id}::{internal_id}"
            entries.append(
                LayerEntry(
                    layer_id=synthetic_id,
                    name=display_name,
                    visible=True,
                    opacity=1.0,
                    locked=False,
                    alpha_lock=False,
                    blend_mode="normal",
                    thumbnail=target_thumb,
                    badge="",
                    layer_type=_SLOT_ROW_LAYER_TYPE,
                    children=None,
                    mask_thumbnail=None,
                    mask_edit_mode=False,
                    ai_effect_state=None,
                    origin_type=None,
                    slot_info=slot_info,
                )
            )
        return entries

    def _resolve_slot_target(
        self, target_id: str | None, required: bool
    ) -> tuple[str, QPixmap | None]:
        """Resolve a slot ref into (display_name, thumbnail).

        - target_id None: placeholder text; required → "(not set)",
          optional → "(optional) drag a layer". Thumbnail is None so
          the slot widget can render a dashed "+" placeholder.
        - target_id not in lookup: treat as "missing" (source layer
          deleted). Surface the truncated UUID so authors can find the
          dangling reference without the app crashing.
        - target_id valid: use the source layer's display name and
          thumbnail (reuses the same cache path as regular rows).
        """
        if not target_id:
            if required:
                return (self._ts("(not set)"), None)
            return (self._ts("(optional) drag a layer"), None)
        lookup = getattr(self, "_layer_lookup", {}) or {}
        target_layer = lookup.get(target_id)
        if target_layer is None:
            return (self._ts("(missing layer)"), None)
        name = getattr(target_layer, "name", "") or target_id
        thumb = self._layer_thumbnail(target_layer)
        return (str(name), thumb)

    @staticmethod
    def _layer_badge(layer: Any) -> str:  # noqa: PLR0911
        layer_type = str(getattr(layer, "layer_type", "raster")).strip().lower()
        layer_name = str(getattr(layer, "name", "")).strip().lower()
        if layer_type == "mask":
            return "M"
        if layer_type == "group":
            return "G"
        if layer_type == "effect":
            return "FX"
        if layer_type == "ai_effect":
            return "AI"
        if layer_type == "3d":
            return "3D"
        if layer_type != "vector":
            return ""
        if layer_name.startswith("text layer"):
            return "T"
        vector_objects = list(getattr(layer, "vector_objects", []) or [])
        if LayerPanel._layer_contains_text_objects(vector_objects):
            return "T"
        vector_kind = str(getattr(layer, "vector_kind", "draw")).strip().lower()
        if vector_kind == "shape" and (
            not vector_objects
            or LayerPanel._layer_contains_parametric_shape_objects(vector_objects)
        ):
            return "S"
        return "V"

    @staticmethod
    def _layer_contains_text_objects(objects: list[Any]) -> bool:
        for obj in objects:
            meta = getattr(obj, "meta", None)
            if not isinstance(meta, dict):
                continue
            payload = meta.get("text_tool")
            if isinstance(payload, dict):
                return True
        return False

    @staticmethod
    def _layer_contains_parametric_shape_objects(objects: list[Any]) -> bool:
        for obj in objects:
            meta = getattr(obj, "meta", None)
            if isinstance(meta, dict) and isinstance(meta.get("shape_manager_parametric"), dict):
                return True
        return False

    def _layer_thumbnail(self, layer: Any) -> QPixmap | None:
        manager = getattr(self, "_thumbnail_manager", None)
        if not isinstance(manager, LayerPanelThumbnailManager):
            manager = LayerPanelThumbnailManager(self, THUMBNAIL_SIZE)
            self._thumbnail_manager = manager
        return manager.layer_thumbnail(layer)

    @staticmethod
    def _qimage_from_array(data: Any) -> QImage | None:
        return LayerPanelThumbnailManager.qimage_from_array(data)

    @staticmethod
    def _thumbnail_from_qimage(image: QImage) -> QPixmap | None:
        return LayerPanelThumbnailManager.thumbnail_from_qimage(image, THUMBNAIL_SIZE)

    def _placeholder_entries(self) -> list[LayerEntry]:
        self._placeholder_seq += 1
        base = self._placeholder_seq
        return [
            LayerEntry(
                layer_id=f"placeholder-{base}-bg",
                name=self._translate_runtime("Background"),
                visible=True,
                opacity=1.0,
                locked=False,
                blend_mode="normal",
            ),
            LayerEntry(
                layer_id=f"placeholder-{base}-layer",
                name=self._translate_runtime("Layer 1"),
                visible=True,
                opacity=1.0,
                locked=False,
                blend_mode="normal",
            ),
        ]

    def _apply_entries(self, entries: list[LayerEntry]) -> None:
        from .comic_balloon_rows import logical_layer_id  # noqa: PLC0415
        from .comic_frame_rows import (  # noqa: PLC0415
            capture_frame_expansion,
            restore_frame_expansion,
        )

        expanded_frames = capture_frame_expansion(self)
        selected_id = self._selected_layer_id or self._active_layer_id
        selected_id = logical_layer_id(getattr(self, "_layer_lookup", {}), selected_id)
        self._entries = entries
        self._widget_map.clear()
        self._item_map.clear()
        self._all_entries.clear()

        # Save scroll position before rebuild
        scrollbar = self._list.verticalScrollBar()
        saved_scroll = scrollbar.value() if scrollbar else 0

        self._list.blockSignals(True)
        self._list.clear()
        for entry in entries:
            item = QTreeWidgetItem(self._list)
            self._populate_tree_item(item, entry)
        self._list.blockSignals(False)
        self._list.expandAll()
        restore_frame_expansion(self, expanded_frames)

        # Restore selection
        target_item = self._item_map.get(selected_id) if selected_id else None
        if target_item is None and entries:
            target_item = self._list.topLevelItem(0)
        if target_item is not None:
            self._list.setCurrentItem(target_item)
            lid = target_item.data(0, Qt.ItemDataRole.UserRole)
            self._selected_layer_id = lid
        else:
            self._selected_layer_id = None

        # Restore scroll position after rebuild
        if scrollbar:
            scrollbar.setValue(saved_scroll)

        self._update_controls_state()

    def _populate_tree_item(self, item: QTreeWidgetItem, entry: LayerEntry) -> None:
        """Configure a QTreeWidgetItem with widget and data, recursing into children."""
        item.setData(0, Qt.ItemDataRole.UserRole, entry.layer_id)
        item.setData(0, Qt.ItemDataRole.UserRole + 1, entry.layer_type)
        widget = LayerItemWidget(entry, self._list)
        from .comic_frame_rows import apply_comic_row_labels  # noqa: PLC0415

        apply_comic_row_labels(widget, entry)
        widget.lock_box.setToolTip(self._lock_tooltip)
        widget.visibility_box.setToolTip(self._visibility_tooltip)
        # Synthetic slot rows are read-only decorations — they must never
        # participate in selection (would emit layer_selected_by_id with a
        # fake UUID) nor be draggable themselves (would confuse the reparent
        # handler). They DO accept drops though, so authors can drag any
        # raster/vector layer onto a slot row to bind it as the slot's
        # source layer (mirroring the Effect Panel combo's bind path).
        if entry.layer_type == "comic_frame_parts":
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            widget.setEnabled(False)
        elif entry.comic_source_id:
            item.setFlags(Qt.ItemFlag.ItemIsEnabled)
            from .comic_frame_reference_row import configure_reference_row  # noqa: PLC0415

            configure_reference_row(
                widget,
                entry.comic_source_id,
                self.select_layer_by_id,
                self.layer_selected_by_id.emit,
            )
        elif entry.slot_info is not None:
            item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDropEnabled)
            # Only slot rows emit exclude_blend_toggled — connecting here
            # (not in __init__) keeps the signal inert for recycled row
            # widgets that currently display a real layer.
            widget.exclude_blend_toggled.connect(self.ai_effect_exclude_blend_toggled)
        else:
            self._connect_row_signals(widget, entry.layer_id)
            # Per-row Generate button — only AI Effect rows light up
            # the button, so a single connect here covers every row
            # without leaking signals from non-AI layers.
            widget.generate_requested.connect(self.ai_effect_generate_requested)
        # Phase 6-f-styling-d: give the widget a 100 ms poll closure
        # targeting its own ``ai_effect.generation.progress`` so the
        # progress bar animates between tree rebuilds.  A layer without
        # ai_effect data gets a no-op fetcher that stops the timer on
        # the next tick.
        if entry.layer_type == "ai_effect":
            widget.set_progress_fetcher(self._make_progress_fetcher(entry.layer_id))
        else:
            widget.set_progress_fetcher(None)
        item.setSizeHint(0, QSize(0, widget.sizeHint().height()))
        self._list.setItemWidget(item, 0, widget)
        self._widget_map[entry.layer_id] = widget
        self._item_map[entry.layer_id] = item
        self._all_entries[entry.layer_id] = entry

        if entry.children:
            for child_entry in entry.children:
                child_item = QTreeWidgetItem(item)
                self._populate_tree_item(child_item, child_entry)

    def _make_progress_fetcher(
        self, layer_id: str
    ) -> Callable[[], tuple[str | None, float | None]]:
        """Return a closure that reads (state, progress) off the live layer.

        The closure captures *layer_id* only — the canvas layer object
        is re-resolved on each call so a delete between ticks simply
        yields ``(None, None)`` and the widget stops its timer. Storing
        a reference to the layer directly would risk keeping a dead
        object alive past tombstone clearing.
        """

        def _fetch() -> tuple[str | None, float | None]:
            host = self._host_api
            canvas = getattr(host, "canvas", None) if host else None
            if canvas is None:
                return (None, None)
            layer = self._find_canvas_layer(canvas, layer_id)
            if layer is None:
                return (None, None)
            ai_effect = getattr(layer, "ai_effect", None)
            if ai_effect is None:
                return (None, None)
            state = getattr(ai_effect, "state", None)
            state_str = state if isinstance(state, str) else None
            generation = getattr(ai_effect, "generation", None)
            raw_progress = getattr(generation, "progress", None)
            progress = float(raw_progress) if isinstance(raw_progress, (int, float)) else None
            return (state_str, progress)

        return _fetch

    def _connect_row_signals(self, widget: LayerItemWidget, layer_id: str) -> None:
        widget.visibility_changed.connect(
            lambda state, lid=layer_id: self._handle_visibility_changed(lid, state)
        )
        widget.lock_changed.connect(
            lambda state, lid=layer_id: self._handle_lock_changed(lid, state)
        )
        widget.alpha_lock_changed.connect(
            lambda state, lid=layer_id: self._handle_alpha_lock_changed(lid, state)
        )
        widget.thumbnail_clicked.connect(
            lambda lid=layer_id: self._toggle_mask_edit_mode(lid, False)
        )
        widget.mask_thumbnail_clicked.connect(
            lambda lid=layer_id: self._toggle_mask_edit_mode(lid, True)
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_for_layer(self, layer_id: str | None) -> int | None:
        """Return top-level index for a layer ID."""
        if layer_id is None:
            return None
        return self._top_level_index_for_layer(layer_id)

    def _top_level_index_for_layer(self, layer_id: str) -> int | None:
        """Find the top-level entry index for a layer or its ancestor."""
        for i, entry in enumerate(self._entries):
            if entry.layer_id == layer_id:
                return i
        # Search in children -> return parent's index
        for i, entry in enumerate(self._entries):
            if entry.children and self._find_in_children(entry.children, layer_id):
                return i
        return None

    @staticmethod
    def _find_in_children(children: list[LayerEntry], layer_id: str) -> bool:
        for child in children:
            if child.layer_id == layer_id:
                return True
            if child.children and LayerPanel._find_in_children(child.children, layer_id):
                return True
        return False

    def _find_entry_by_id(self, layer_id: str | None) -> LayerEntry | None:
        if layer_id is None:
            return None
        return self._all_entries.get(layer_id)

    def _populate_blend_modes(self, is_group: bool = False) -> None:
        self._suppress_blend_signal = True
        self._blend_combo.clear()
        groups = [
            (self._ts("Normal"), [BlendMode.NORMAL, BlendMode.DISSOLVE]),
            (
                self._ts("Darken"),
                [
                    BlendMode.DARKEN,
                    BlendMode.MULTIPLY,
                    BlendMode.COLOR_BURN,
                    BlendMode.LINEAR_BURN,
                    BlendMode.DARKER_COLOR,
                ],
            ),
            (
                self._ts("Lighten"),
                [
                    BlendMode.LIGHTEN,
                    BlendMode.SCREEN,
                    BlendMode.COLOR_DODGE,
                    BlendMode.LINEAR_DODGE,
                    BlendMode.LIGHTER_COLOR,
                ],
            ),
            (
                self._ts("Contrast"),
                [
                    BlendMode.OVERLAY,
                    BlendMode.SOFT_LIGHT,
                    BlendMode.HARD_LIGHT,
                    BlendMode.VIVID_LIGHT,
                    BlendMode.LINEAR_LIGHT,
                    BlendMode.PIN_LIGHT,
                    BlendMode.HARD_MIX,
                ],
            ),
            (
                self._ts("Inversion"),
                [
                    BlendMode.DIFFERENCE,
                    BlendMode.EXCLUSION,
                    BlendMode.SUBTRACT,
                    BlendMode.DIVIDE,
                ],
            ),
            (
                self._ts("Component"),
                [
                    BlendMode.HUE,
                    BlendMode.SATURATION,
                    BlendMode.COLOR,
                    BlendMode.LUMINOSITY,
                ],
            ),
        ]
        # Add "Pass Through" at the top for group layers
        if is_group:
            self._blend_combo.addItem(
                self._format_blend_label("pass_through"),
                "pass_through",
            )
            self._blend_combo.insertSeparator(self._blend_combo.count())
        model = self._blend_combo.model()
        for index, (label, modes) in enumerate(groups):
            if index > 0:
                self._blend_combo.insertSeparator(self._blend_combo.count())
            header_index = self._blend_combo.count()
            self._blend_combo.addItem(f"[{label}]")
            if hasattr(model, "item"):
                header_item = model.item(header_index)
                if header_item is not None:
                    header_item.setEnabled(False)
            for mode in modes:
                self._blend_combo.addItem(self._format_blend_label(mode.value), mode.value)
        self._suppress_blend_signal = False

    def _format_blend_label(self, value: str) -> str:
        source = self._blend_mode_labels.get(value)
        if source:
            return self._translate_runtime(source)
        return value.replace("_", " ").title()

    def _set_opacity_value(self, value: int) -> None:
        clamped = max(0, min(100, int(value)))
        if self._opacity_slider.value() != clamped:
            self._suppress_opacity_signal = True
            self._opacity_slider.setValue(clamped)
            self._suppress_opacity_signal = False
        self._opacity_value.setText(f"{clamped}%")

    def _set_blend_mode(self, mode: str) -> None:
        normalized = str(mode).strip().lower().replace(" ", "_")
        index = self._blend_combo.findData(normalized)
        if index < 0:
            index = self._blend_combo.findData("normal")
        index = max(index, 0)
        if self._blend_combo.currentIndex() != index:
            self._suppress_blend_signal = True
            self._blend_combo.setCurrentIndex(index)
            self._suppress_blend_signal = False

    def _emit_blend_mode_changed(self, index: int, mode: str, layer_id: str | None = None) -> None:
        host = self._host_api
        if not host:
            return
        events = getattr(host, "events", None)
        if events is None:
            return
        payload = {"index": index, "mode": mode, "layer_id": layer_id}
        emit = getattr(events, "emit", None)
        publish = getattr(events, "publish", None)
        if callable(emit):
            emit("layer.blend_mode_changed", payload)
        elif callable(publish):
            publish("layer.blend_mode_changed", payload)

    def _handle_visibility_changed(self, layer_id: str, visible: bool) -> None:
        entry = self._find_entry_by_id(layer_id)
        if entry:
            entry.visible = visible
        self.layer_visibility_changed_by_id.emit(layer_id, visible)
        # Legacy index-based signal for top-level layers
        for i, e in enumerate(self._entries):
            if e.layer_id == layer_id:
                self.layer_visibility_changed.emit(i, visible)
                return

    def _handle_lock_changed(self, layer_id: str, locked: bool) -> None:
        entry = self._find_entry_by_id(layer_id)
        if entry:
            entry.locked = locked
        self.layer_locked_changed_by_id.emit(layer_id, locked)
        # Legacy index-based signal for top-level layers
        for i, e in enumerate(self._entries):
            if e.layer_id == layer_id:
                self.layer_locked_changed.emit(i, locked)
                return

    def _handle_alpha_lock_changed(self, layer_id: str, alpha_locked: bool) -> None:
        entry = self._find_entry_by_id(layer_id)
        if entry:
            entry.alpha_lock = alpha_locked
        self.layer_alpha_lock_changed_by_id.emit(layer_id, alpha_locked)

    def _handle_opacity_changed(self, layer_id: str, opacity: float) -> None:
        entry = self._find_entry_by_id(layer_id)
        if entry:
            entry.opacity = opacity
        self.layer_opacity_changed_by_id.emit(layer_id, opacity)
        # Legacy index-based signal for top-level layers
        for i, e in enumerate(self._entries):
            if e.layer_id == layer_id:
                self.layer_opacity_changed.emit(i, opacity)
                return

    def _update_nested_layer_property(self, layer_id: str, method: str, value: Any) -> None:
        """Call a canvas method for a nested (non-top-level) layer."""
        canvas = self._get_canvas()
        if canvas is None:
            return
        fn = getattr(canvas, method, None)
        if callable(fn):
            try:
                fn(layer_id, value)
            except Exception:
                logger.debug("Failed to call %s for layer %s", method, layer_id, exc_info=True)

    def _update_controls_state(self) -> None:
        current = self._list.currentItem()
        has_selection = current is not None
        self._delete_btn.setEnabled(has_selection)
        self._duplicate_btn.setEnabled(has_selection)
        self._sync_top_controls()

    def _sync_top_controls(self) -> None:
        current = self._list.currentItem()
        if current is None:
            self._blend_combo.setEnabled(False)
            self._opacity_slider.setEnabled(False)
            self._opacity_value.setEnabled(False)
            self._set_opacity_value(100)
            self._populate_blend_modes(is_group=False)
            self._set_blend_mode("normal")
            return
        layer_id = current.data(0, Qt.ItemDataRole.UserRole)
        entry = self._find_entry_by_id(layer_id)
        if entry is None:
            return
        is_group = entry.layer_type == "group"
        self._populate_blend_modes(is_group=is_group)
        self._blend_combo.setEnabled(True)
        self._opacity_slider.setEnabled(True)
        self._opacity_value.setEnabled(True)
        self._set_opacity_value(round(entry.opacity * 100))
        self._set_blend_mode(entry.blend_mode)

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    @Slot(int)
    def _on_blend_changed(self, _index: int) -> None:
        if self._suppress_blend_signal:
            return
        current = self._list.currentItem()
        if current is None:
            return
        mode = self._blend_combo.currentData()
        if not isinstance(mode, str):
            return
        layer_id = current.data(0, Qt.ItemDataRole.UserRole)
        entry = self._find_entry_by_id(layer_id)
        if entry:
            entry.blend_mode = mode
        row = self._top_level_index_for_layer(layer_id)
        if row is not None:
            self._emit_blend_mode_changed(row, mode, layer_id=layer_id)

    @Slot(int)
    def _on_opacity_slider_changed(self, value: int) -> None:
        if self._suppress_opacity_signal:
            return
        self._set_opacity_value(value)
        current = self._list.currentItem()
        if current is None:
            return
        layer_id = current.data(0, Qt.ItemDataRole.UserRole)
        opacity = max(0.0, min(1.0, value / 100.0))
        self._handle_opacity_changed(layer_id, opacity)

    def _on_item_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        if current is None:
            self._selected_layer_id = None
            self._update_controls_state()
            return
        layer_id = current.data(0, Qt.ItemDataRole.UserRole)
        # Phase 6-f-styling-b regression guard: synthetic slot rows are
        # non-selectable, but Qt still routes keyboard-focus / explicit
        # setCurrentItem calls through them as the *current* item. If we
        # propagated their synthetic UUID ("__slot::...") as if it were a
        # real layer selection, PanelSelectionBinder would fail to resolve
        # it and call AIEffectPanel.clear() — the user-visible bug was
        # "(no preset selected)" showing right after picking an AI Effect
        # layer whose first child is an empty required slot. We keep
        # ``_selected_layer_id`` pinned to the previous real layer so the
        # next refresh rehydrates selection onto the same AI Effect row.
        if isinstance(layer_id, str) and layer_id.startswith(_SLOT_ROW_ID_PREFIX):
            self._update_controls_state()
            return
        self._selected_layer_id = layer_id
        self._update_controls_state()
        self.layer_selected_by_id.emit(layer_id)
        # Legacy index-based signal
        row = self._top_level_index_for_layer(layer_id)
        if row is not None:
            self.layer_selected.emit(row)

    def _on_layer_drop_requested(
        self, dragged_id: str, target_id: object, drop_pos_int: int
    ) -> None:
        """Handle a custom drop request — reparent via canvas API, then rebuild tree."""
        canvas = self._get_canvas()
        if canvas is None or not isinstance(target_id, str):
            return

        drop_pos = QAbstractItemView.DropIndicatorPosition(drop_pos_int)
        on_item = QAbstractItemView.DropIndicatorPosition.OnItem
        above_item = QAbstractItemView.DropIndicatorPosition.AboveItem

        if drop_pos == on_item:
            # Drop ON a group → move dragged layer into the group as last child
            self._submit_reparent(canvas, dragged_id, target_id, -1)
        else:
            # Drop above/below an item → reorder at the same parent level
            self._handle_reorder_drop(canvas, dragged_id, target_id, drop_pos == above_item)

        # _submit_reparent → reparent_layer → Events.LAYER_CHANGED already
        # triggers panel.refresh() (30 ms coalescing timer) via the event bus.
        # Calling _add_default_layers() here caused a second full rebuild
        # ~30 ms later, blocking the main thread for ~400 ms total.
        self.refresh()

    def _handle_reorder_drop(
        self,
        canvas: Any,
        dragged_id: str,
        target_id: str,
        is_above: bool,
    ) -> None:
        """Compute canvas insertion index and reparent for above/below drops."""
        parent_id = self._find_parent_of(target_id)
        sibling_list = self._get_canvas_sibling_list(target_id)
        if sibling_list is None:
            return
        target_idx = next(
            (i for i, layer in enumerate(sibling_list) if layer.id == target_id), None
        )
        if target_idx is None:
            return

        # UI top-to-bottom is reversed from canvas bottom-to-top at root level
        if parent_id is None:
            canvas_pos = target_idx + 1 if is_above else target_idx
        else:
            canvas_pos = target_idx if is_above else target_idx + 1

        self._submit_reparent(canvas, dragged_id, parent_id, canvas_pos)

    @staticmethod
    def _submit_reparent(canvas: Any, layer_id: str, parent_id: str | None, pos: int) -> None:
        try:
            from loomstroke.core.commands import ReparentLayerCommand  # noqa: PLC0415

            submit = getattr(canvas, "submit", None)
            if callable(submit):
                cmd = ReparentLayerCommand(layer_id, parent_id, pos)
                submit(cmd)
            else:
                reparent = getattr(canvas, "reparent_layer", None)
                if callable(reparent):
                    reparent(layer_id, parent_id, pos)
        except Exception:
            logger.debug("reparent failed for %s", layer_id, exc_info=True)

    def _find_parent_of(self, layer_id: str) -> str | None:
        """Return the parent layer ID for a given layer, or None if root."""
        canvas = self._get_canvas()
        if canvas is None:
            return None
        layer = self._resolve_canvas_layer(layer_id)
        if layer is None:
            return None
        return getattr(layer, "parent_id", None)

    def _get_canvas_sibling_list(self, layer_id: str) -> list[Any] | None:
        """Return the sibling list containing the given layer ID."""
        canvas = self._get_canvas()
        if canvas is None:
            return None
        find_parent = getattr(canvas, "_find_parent", None)
        if not callable(find_parent):
            return None
        result = find_parent(layer_id)
        if result is None:
            return None
        return cast("list[Any] | None", result[0])

    def _on_add_clicked(self) -> None:
        if not self._acquire_layer_op():
            return
        self.layer_added.emit()

    def _on_add_vector_layer_clicked(self) -> None:
        if not self._acquire_layer_op():
            return
        canvas = getattr(self._host_api, "canvas", None)
        if canvas is None:
            return
        add_vector_layer = getattr(canvas, "add_vector_layer", None)
        if callable(add_vector_layer):
            try:
                add_vector_layer()
            except Exception:
                logger.debug("add_vector_layer failed", exc_info=True)
        cast(Any, self)._refresh()

    def _on_duplicate_clicked(self) -> None:
        if not self._acquire_layer_op():
            return
        self.layer_duplicated.emit()

    def _on_add_mask_clicked(self) -> None:
        if not self._acquire_layer_op():
            return
        layer_id = self._selected_layer_id
        if not layer_id:
            current = self._list.currentItem()
            if current is not None:
                layer_id = current.data(0, Qt.ItemDataRole.UserRole)
        if layer_id:
            layer = self._resolve_canvas_layer(layer_id)
            has_mask = layer is not None and getattr(layer, "layer_mask", None) is not None
            if has_mask:
                self._remove_layer_mask(layer_id)
            else:
                self._add_layer_mask(layer_id)
        else:
            self._release_layer_op()

    def _on_add_filter_layer_clicked(self) -> None:
        """Show the filter type menu and add a standalone filter layer."""
        menu = QMenu(self)
        self._populate_filter_layer_menu(menu)
        menu.exec(self._add_filter_btn.mapToGlobal(QPoint(0, self._add_filter_btn.height())))

    def _populate_filter_layer_menu(self, menu: QMenu) -> None:
        # CT-21: Adjustment / Filter / Fill Layer groups live in a delegate.
        self._appearance_menu().populate_filter_layer_menu(menu)

    def _appearance_menu(self) -> Any:
        builder = getattr(self, "_appearance_menu_builder", None)
        if builder is None:
            from .layer_panel_appearance_menu import (  # noqa: PLC0415
                LayerPanelAppearanceMenuBuilder,
            )

            builder = LayerPanelAppearanceMenuBuilder(self)
            self._appearance_menu_builder = builder
        return builder

    # ------------------------------------------------------------------
    # Phase 6-f-add-ai-effect-menu: independent Add AI Effect Layer
    # popup. The footer button picks a category first, then a preset,
    # while the regular Add Filter Layer popup remains for non-AI
    # effect layers only.
    # ------------------------------------------------------------------
    def _ai_effect_menu(self) -> LayerPanelAIEffectMenuBuilder:
        builder = getattr(self, "_ai_effect_menu_builder", None)
        if not isinstance(builder, LayerPanelAIEffectMenuBuilder):
            builder = LayerPanelAIEffectMenuBuilder(self)
            self._ai_effect_menu_builder = builder
        return builder

    def set_ai_effect_preset_applier(
        self,
        applier: Callable[[str], Any] | None,
        library_getter: Callable[[], list[Any]] | None = None,
    ) -> None:
        """Register the callback that applies an AI preset + lists them.

        - ``applier(preset_id)`` materialises the preset as a canvas AI
          Effect layer. Pass ``None`` to disable the popup.
        - ``library_getter()`` returns the current preset list. Invoked
          on every button click so exports done in the Effect Editor
          appear without a restart.

        Both callbacks are stored on the panel; a missing library getter
        degrades the submenu to "(No presets installed)".
        """
        self._ai_effect_preset_applier = applier
        self._ai_effect_preset_library_getter = library_getter
        button = getattr(self, "_add_ai_effect_btn", None)
        if button is not None:
            button.setEnabled(applier is not None)

    def _on_add_ai_effect_layer_clicked(self) -> None:
        """Show category -> preset menu and add an AI Effect Layer."""
        self._ai_effect_menu().show_add_menu()

    def _rebuild_add_ai_effect_menu(self, menu: QMenu) -> None:
        """Refresh the Add AI Effect Layer popup from the library getter."""
        self._ai_effect_menu().rebuild(menu)

    def _ai_effect_preset_sort_key(self, preset: Any) -> tuple[str, str]:
        return LayerPanelAIEffectMenuBuilder.preset_sort_key(preset)

    def _on_add_ai_effect_triggered(self, preset_id: str) -> None:
        """Invoke the wired applier — mirrors Filter menu path."""
        self._ai_effect_menu().on_triggered(preset_id)

    def _on_ai_effect_preset_dropped(self, preset_id: str) -> None:
        """Apply a preset dropped from AI Effects Manager."""
        self.ai_effect_preset_dropped.emit(preset_id)
        self._ai_effect_menu().on_triggered(preset_id)

    def _add_standalone_filter_layer(self, filter_type: str) -> None:
        canvas = self._get_canvas()
        if canvas is None:
            return
        try:
            fn = getattr(canvas, "add_filter_layer", None) or getattr(
                canvas, "add_effect_layer", None
            )
            if callable(fn):
                fn(None, filter_type)
                self._add_default_layers()
        except Exception:
            logger.debug("add standalone filter layer failed: %s", filter_type, exc_info=True)

    # Compatibility delegates for historical panel integrations.
    def _on_add_effect_layer_clicked(self) -> None:
        self._on_add_filter_layer_clicked()

    def _populate_effect_layer_menu(self, menu: QMenu) -> None:
        self._populate_filter_layer_menu(menu)

    def _add_standalone_effect_layer(self, effect_type: str) -> None:
        self._add_standalone_filter_layer(effect_type)

    def _populate_effect_menu(self, menu: QMenu, layer_id: str) -> None:
        self._populate_filter_menu(menu, layer_id)

    def _add_effect_to_layer(self, layer_id: str, effect_type: str) -> None:
        self._add_filter_to_layer(layer_id, effect_type)

    def _build_mask_and_effect_menu(
        self, menu: QMenu, layer_id: str, layer: Any, is_group: bool
    ) -> None:
        self._build_mask_and_filter_menu(menu, layer_id, layer, is_group)

    def _on_group_clicked(self) -> None:
        """Group the currently selected layers."""
        if not self._acquire_layer_op():
            return
        selected_ids = self._filter_toplevel_selected_ids()
        if len(selected_ids) < 2:
            self._release_layer_op()
            return
        canvas = self._get_canvas()
        if canvas is None:
            self._release_layer_op()
            return
        try:
            from loomstroke.core.commands import GroupLayersCommand  # noqa: PLC0415

            cmd = GroupLayersCommand(layer_ids=selected_ids)
            submit = getattr(canvas, "submit", None)
            if callable(submit):
                submit(cmd)
            else:
                group_fn = getattr(canvas, "group_layers", None)
                if callable(group_fn):
                    group_fn(selected_ids)
            self._add_default_layers()
            self._release_layer_op()
        except Exception:
            self._release_layer_op()
            logger.debug("group_layers failed", exc_info=True)

    def _on_ungroup_clicked(self, group_id: str) -> None:
        """Ungroup the specified group layer."""
        if not self._acquire_layer_op():
            return
        canvas = self._get_canvas()
        if canvas is None:
            self._release_layer_op()
            return
        try:
            from loomstroke.core.commands import UngroupLayerCommand  # noqa: PLC0415

            cmd = UngroupLayerCommand(group_id=group_id)
            submit = getattr(canvas, "submit", None)
            if callable(submit):
                submit(cmd)
            else:
                ungroup_fn = getattr(canvas, "ungroup_layer", None)
                if callable(ungroup_fn):
                    ungroup_fn(group_id)
            self._add_default_layers()
            self._release_layer_op()
        except Exception:
            self._release_layer_op()
            logger.debug("ungroup_layer failed for %s", group_id, exc_info=True)

    def _on_selection_changed(self) -> None:
        """Update group button enabled state based on multi-selection."""
        selected = self._list.selectedItems()
        self._group_btn.setEnabled(len(selected) >= 2)

    def _get_selected_layer_ids(self) -> list[str]:
        """Return layer IDs of all selected items."""
        ids: list[str] = []
        for item in self._list.selectedItems():
            lid = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(lid, str) and lid:
                ids.append(lid)
        return ids

    def _filter_toplevel_selected_ids(self) -> list[str]:
        """Return selected layer IDs excluding children whose parent is also selected.

        When a tree item with children (e.g. a layer with effect sub-layers) is
        selected, ExtendedSelection may include the child items as well.  For
        grouping, only sibling layers should be passed to ``group_layers``.
        """
        selected_items = self._list.selectedItems()
        # Build set of selected layer IDs (not Python id()) for reliable comparison
        selected_lid_set: set[str] = set()
        for item in selected_items:
            lid = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(lid, str) and lid:
                selected_lid_set.add(lid)
        ids: list[str] = []
        for item in selected_items:
            # Skip effect layers — they cannot be grouped independently
            ltype = item.data(0, Qt.ItemDataRole.UserRole + 1)
            if ltype == "effect":
                continue
            parent_item = item.parent()
            if parent_item is not None:
                parent_lid = parent_item.data(0, Qt.ItemDataRole.UserRole)
                if isinstance(parent_lid, str) and parent_lid in selected_lid_set:
                    continue  # skip children whose parent is also selected
            lid = item.data(0, Qt.ItemDataRole.UserRole)
            if isinstance(lid, str) and lid:
                ids.append(lid)
        return ids

    def _on_delete_clicked(self) -> None:
        if not self._acquire_layer_op():
            return
        current = self._list.currentItem()
        if current is None:
            self._release_layer_op()
            return
        layer_id = current.data(0, Qt.ItemDataRole.UserRole)
        self.layer_deleted_by_id.emit(layer_id)

    def _on_context_menu_requested(self, pos: QPoint) -> None:
        self._context_menu_builder.show(pos)

    def _build_edit_actions(self, menu: QMenu, layer_id: str) -> None:
        """Populate rename / merge / rasterize actions in context menu."""
        self._context_menu_builder.build_edit_actions(menu, layer_id)

    def _build_mask_and_filter_menu(
        self, menu: QMenu, layer_id: str, layer: object, is_group: bool
    ) -> None:
        """Populate layer-mask and filter sub-items in context menu."""
        self._context_menu_builder.build_mask_and_filter_menu(menu, layer_id, layer, is_group)

    def _build_sam3_layer_mask_menu_action(self, menu: QMenu, layer_id: str) -> Any | None:
        """Add the optional SAM3 layer-mask action when the SAM3 tool is registered."""
        action_state = self._sam3_context_action_state(_SAM3_LAYER_MASK_ACTION_ID)
        if action_state is None:
            return None

        action = menu.addAction(self._sam3_layer_mask_action_label(layer_id))
        action.setEnabled(
            bool(action_state.get("enabled", False))
            and bool(layer_id)
            and not layer_id.startswith(_SLOT_ROW_ID_PREFIX)
            and self._resolve_canvas_layer(layer_id) is not None
        )
        action.triggered.connect(
            lambda _checked=False, lid=layer_id: (
                self._dispatch_sam3_layer_mask_action_from_layer_panel(lid)
            )
        )
        return action

    def _build_sam3_add_to_layer_mask_menu_action(self, menu: QMenu, layer_id: str) -> Any | None:
        """Add the optional SAM3 add-to-mask action when the target layer has a mask."""
        if not self._layer_has_mask(layer_id):
            return None
        action_state = self._sam3_context_action_state(_SAM3_ADD_TO_LAYER_MASK_ACTION_ID)
        if action_state is None:
            return None

        action = menu.addAction(self._ts("Add Candidate to Existing Mask"))
        action.setEnabled(
            bool(action_state.get("enabled", False))
            and bool(layer_id)
            and not layer_id.startswith(_SLOT_ROW_ID_PREFIX)
            and self._resolve_canvas_layer(layer_id) is not None
        )
        action.triggered.connect(
            lambda _checked=False, lid=layer_id: (
                self._dispatch_sam3_add_to_layer_mask_action_from_layer_panel(lid)
            )
        )
        return action

    def _build_sam3_toggle_candidate_overlay_menu_action(
        self,
        menu: QMenu,
        layer_id: str,
    ) -> Any | None:
        """Add the optional SAM3 candidate overlay visibility toggle."""
        action_state = self._sam3_context_action_state(_SAM3_TOGGLE_CANDIDATE_OVERLAY_ACTION_ID)
        if action_state is None:
            return None

        action = menu.addAction(self._ts("Toggle Candidate Overlay"))
        action.setCheckable(True)
        action.setChecked(bool(action_state.get("checked", False)))
        action.setEnabled(
            bool(action_state.get("enabled", False))
            and bool(layer_id)
            and not layer_id.startswith(_SLOT_ROW_ID_PREFIX)
            and self._resolve_canvas_layer(layer_id) is not None
        )
        action.triggered.connect(
            lambda _checked=False, lid=layer_id: (
                self._dispatch_sam3_toggle_candidate_overlay_action_from_layer_panel(lid)
            )
        )
        return action

    def _sam3_layer_mask_action_label(self, layer_id: str) -> str:
        if self._layer_has_mask(layer_id):
            return self._ts("Replace Layer Mask with Candidate")
        return self._ts("Create SAM3 Mask from Layer")

    def _sam3_context_action_state(self, action_id: str) -> Mapping[str, Any] | None:
        plugin = self._sam3_ai_select_tool_plugin()
        if plugin is None:
            return None

        snapshot_fn = getattr(plugin, "get_context_actions_snapshot", None)
        if not callable(snapshot_fn):
            return None
        try:
            snapshot = snapshot_fn()
        except Exception:
            logger.debug("SAM3 layer panel action snapshot failed", exc_info=True)
            return None
        if not isinstance(snapshot, Mapping):
            return None

        actions = snapshot.get("actions")
        if not isinstance(actions, list):
            return None
        for action in actions:
            if isinstance(action, Mapping) and action.get("id") == action_id:
                return action
        return None

    def _sam3_ai_select_tool_plugin(self) -> object | None:
        host = self._host_api
        manager = getattr(host, "_tool_manager", None) if host is not None else None
        if manager is None:
            return None
        tools = getattr(manager, "_tools", None)
        if not isinstance(tools, Mapping):
            return None
        state = tools.get(_SAM3_AI_SELECT_TOOL_ID)
        return getattr(state, "plugin", None) if state is not None else None

    def _dispatch_sam3_layer_mask_action_from_layer_panel(self, layer_id: str) -> bool:
        return self._dispatch_sam3_tool_action_from_layer_panel(
            layer_id,
            _SAM3_LAYER_MASK_ACTION_ID,
            self._sam3_layer_mask_action_source(layer_id),
        )

    def _dispatch_sam3_add_to_layer_mask_action_from_layer_panel(self, layer_id: str) -> bool:
        return self._dispatch_sam3_tool_action_from_layer_panel(
            layer_id,
            _SAM3_ADD_TO_LAYER_MASK_ACTION_ID,
            _SAM3_LAYER_PANEL_ADD_TO_MASK_SOURCE,
        )

    def _dispatch_sam3_toggle_candidate_overlay_action_from_layer_panel(
        self,
        layer_id: str,
    ) -> bool:
        return self._dispatch_sam3_tool_action_from_layer_panel(
            layer_id,
            _SAM3_TOGGLE_CANDIDATE_OVERLAY_ACTION_ID,
            _SAM3_LAYER_PANEL_TOGGLE_OVERLAY_SOURCE,
            merge_key=None,
        )

    def _dispatch_sam3_tool_action_from_layer_panel(
        self,
        layer_id: str,
        action_id: str,
        source: str,
        *,
        merge_key: str | None = _SAM3_LAYER_PANEL_MASK_MERGE_KEY,
    ) -> bool:
        if not layer_id or layer_id.startswith(_SLOT_ROW_ID_PREFIX):
            return False
        host = self._host_api
        manager = getattr(host, "_tool_manager", None) if host is not None else None
        dispatch = getattr(manager, "dispatch_tool_action", None)
        if not callable(dispatch):
            return False

        try:
            item = getattr(self, "_item_map", {}).get(layer_id)
            layer_list = getattr(self, "_list", None)
            if item is not None and layer_list is not None and layer_list.currentItem() is not item:
                layer_list.setCurrentItem(item)
        except Exception:
            logger.debug("SAM3 layer panel selection sync failed for %s", layer_id, exc_info=True)

        try:
            payload = {"source": source}
            if merge_key is not None:
                payload["merge_key"] = merge_key
            handled = bool(
                dispatch(
                    _SAM3_AI_SELECT_TOOL_ID,
                    action_id,
                    payload,
                )
            )
        except Exception:
            logger.debug("SAM3 layer panel tool action failed for %s", layer_id, exc_info=True)
            return False
        if handled:
            self._add_default_layers()
        return handled

    def _sam3_layer_mask_action_source(self, layer_id: str) -> str:
        if self._layer_has_mask(layer_id):
            return _SAM3_LAYER_PANEL_REPLACE_MASK_SOURCE
        return _SAM3_LAYER_PANEL_MASK_SOURCE

    def _layer_has_mask(self, layer_id: str) -> bool:
        layer = self._resolve_canvas_layer(layer_id) if layer_id else None
        return layer is not None and getattr(layer, "layer_mask", None) is not None

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        layer_id = item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(layer_id, str) and layer_id:
            from .comic_balloon_rows import edit_balloon  # noqa: PLC0415

            if edit_balloon(self, self._resolve_canvas_layer(layer_id)):
                return
            self._rename_layer(layer_id)

    def _rename_layer(self, layer_id: str) -> None:
        layer = self._resolve_canvas_layer(layer_id)
        if layer is None:
            return
        current_name = str(getattr(layer, "name", ""))
        new_name, ok = QInputDialog.getText(
            self,
            self._ts("Rename Layer"),
            self._ts("Layer name:"),
            text=current_name,
        )
        if not ok or not new_name.strip() or new_name == current_name:
            return
        layer.name = new_name.strip()
        self._add_default_layers()

    def _toggle_clipping(self, layer_id: str) -> None:
        canvas = self._get_canvas()
        if canvas is None:
            return
        layer = self._resolve_canvas_layer(layer_id)
        current = bool(getattr(layer, "clipping", False)) if layer else False
        try:
            from loomstroke.core.commands import ToggleClippingCommand  # noqa: PLC0415

            cmd = ToggleClippingCommand(layer_id=layer_id, enabled=not current)
            submit = getattr(canvas, "submit", None)
            if callable(submit):
                submit(cmd)
            else:
                fn = getattr(canvas, "set_clipping", None)
                if callable(fn):
                    fn(layer_id, not current)
            self._add_default_layers()
        except Exception:
            logger.debug("set_clipping failed for %s", layer_id, exc_info=True)

    def _add_layer_mask(self, layer_id: str) -> None:
        canvas = self._get_canvas()
        if canvas is None:
            self._release_layer_op()
            return
        try:
            from loomstroke.core.commands import AddLayerMaskCommand  # noqa: PLC0415

            submit = getattr(canvas, "submit", None)
            if callable(submit):
                submit(AddLayerMaskCommand(layer_id=layer_id))
            else:
                fn = getattr(canvas, "add_layer_mask", None)
                if callable(fn):
                    fn(layer_id)
            # Auto-enter mask edit mode (like Photoshop)
            self._toggle_mask_edit_mode(layer_id, True)
            self._add_default_layers()
            self._release_layer_op()
        except Exception:
            self._release_layer_op()
            logger.debug("add_layer_mask failed for %s", layer_id, exc_info=True)

    def _remove_layer_mask(self, layer_id: str) -> None:
        canvas = self._get_canvas()
        if canvas is None:
            self._release_layer_op()
            return
        try:
            from loomstroke.core.commands import RemoveLayerMaskCommand  # noqa: PLC0415

            submit = getattr(canvas, "submit", None)
            if callable(submit):
                submit(RemoveLayerMaskCommand(layer_id=layer_id))
            else:
                fn = getattr(canvas, "remove_layer_mask", None)
                if callable(fn):
                    fn(layer_id)
            self._add_default_layers()
            self._release_layer_op()
        except Exception:
            self._release_layer_op()
            logger.debug("remove_layer_mask failed for %s", layer_id, exc_info=True)

    def _toggle_mask_edit_mode(self, layer_id: str, enabled: bool) -> None:
        canvas = self._get_canvas()
        if canvas is None:
            return
        fn = getattr(canvas, "set_mask_edit_mode", None)
        if callable(fn):
            fn(layer_id, enabled)
        self._add_default_layers()

    def _populate_filter_menu(self, menu: QMenu, layer_id: str) -> None:
        # CT-21: adjustments first, then live filters (same commands as before).
        self._appearance_menu().populate_layer_filter_menu(menu, layer_id)

    def _add_filter_to_layer(self, layer_id: str, filter_type: str) -> None:
        canvas = self._get_canvas()
        if canvas is None:
            return
        try:
            from loomstroke.core.commands import AddFilterLayerCommand  # noqa: PLC0415

            cmd = AddFilterLayerCommand(parent_layer_id=layer_id, filter_type=filter_type)
            submit = getattr(canvas, "submit", None)
            if callable(submit):
                submit(cmd)
            else:
                fn = getattr(canvas, "add_filter_layer", None) or getattr(
                    canvas, "add_effect_layer", None
                )
                if callable(fn):
                    fn(layer_id, filter_type)
            self._add_default_layers()
        except Exception:
            logger.debug("add_filter_layer failed for %s", layer_id, exc_info=True)

    def _can_rasterize_layer(self, layer_id: str) -> bool:
        layer = self._resolve_canvas_layer(layer_id)
        if layer is None:
            return False
        if bool(getattr(layer, "locked", False)):
            return False
        return getattr(layer, "layer_type", "raster") == "vector"

    def _rasterize_layer(self, layer_id: str) -> None:
        host = self._host_api
        if not host:
            return
        canvas = getattr(host, "canvas", None)
        if canvas is None:
            return
        rasterize = getattr(canvas, "rasterize_layer", None)
        if not callable(rasterize):
            logger.debug("Canvas does not support rasterize_layer")
            return
        try:
            if rasterize(layer_id):
                self._add_default_layers()
        except Exception:
            logger.debug("Rasterize failed for layer %s", layer_id, exc_info=True)

    def _is_shape_layer(self, layer_id: str) -> bool:
        """Return True if the layer is a shape vector layer."""
        canvas = getattr(self._host_api, "canvas", None)
        if canvas is None:
            return False
        layers = getattr(canvas, "_layers", None)
        if not layers:
            return False
        for layer in layers:
            if getattr(layer, "id", None) == layer_id:
                return (
                    getattr(layer, "layer_type", "") == "vector"
                    and getattr(layer, "vector_kind", "draw") == "shape"
                )
        return False

    def _convert_to_vector(self, layer_id: str) -> None:
        """Convert a shape layer to a draw-vector layer."""
        canvas = getattr(self._host_api, "canvas", None)
        if canvas is None:
            return
        set_kind = getattr(canvas, "set_vector_kind", None)
        if callable(set_kind):
            name = self._unique_vector_layer_name(layer_id)
            try:
                set_kind(layer_id, "draw", name=name)
            except TypeError:
                set_kind(layer_id, "draw")
        cast(Any, self)._refresh()

    def _unique_vector_layer_name(self, layer_id: str) -> str:
        canvas = getattr(self._host_api, "canvas", None)
        layers = getattr(canvas, "_layers", None) or []
        existing = {
            str(getattr(layer, "name", ""))
            for layer in layers
            if getattr(layer, "id", None) != layer_id
        }
        n = 1
        while True:
            name = f"Vector Layer {n}"
            if name not in existing:
                return name
            n += 1

    def _can_merge_down(self, layer_id: str) -> bool:
        """Return True if the layer can be merged down."""
        host = self._host_api
        if not host:
            return False
        canvas = getattr(host, "canvas", None)
        if canvas is None:
            return False
        layers = getattr(canvas, "_layers", None)
        if not layers or len(layers) < 2:
            return False
        # Find index
        for i, layer in enumerate(layers):
            if getattr(layer, "id", None) == layer_id:
                if i <= 0:
                    return False
                if bool(getattr(layer, "locked", False)):
                    return False
                target = layers[i - 1]
                return not bool(getattr(target, "locked", False))
        return False

    def _on_merge_down_requested(self) -> None:
        """Trigger merge-down action registered in main window."""
        from PySide6.QtGui import QAction  # noqa: PLC0415
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415

        win = QApplication.activeWindow()
        if win is None:
            return
        for action in win.findChildren(QAction):
            if action.property("shortcut_action_id") == "layer.merge_down":
                action.trigger()
                return

    def _can_merge_visible(self) -> bool:
        """Return True when at least two visible layers exist."""
        host = self._host_api
        if not host:
            return False
        canvas = getattr(host, "canvas", None)
        if canvas is None:
            return False
        layers = getattr(canvas, "_layers", None)
        if not layers:
            return False
        visible_count = sum(
            1
            for ly in layers
            if getattr(ly, "visible", False) and getattr(ly, "image", None) is not None
        )
        return visible_count >= 2

    def _on_merge_visible_requested(self) -> None:
        """Trigger merge-visible action registered in main window."""
        from PySide6.QtGui import QAction  # noqa: PLC0415
        from PySide6.QtWidgets import QApplication  # noqa: PLC0415

        win = QApplication.activeWindow()
        if win is None:
            return
        for action in win.findChildren(QAction):
            if action.property("shortcut_action_id") == "layer.merge_visible":
                action.trigger()
                return

    def _is_mask_layer(self, layer_id: str) -> bool:
        layer = self._resolve_canvas_layer(layer_id)
        return layer is not None and getattr(layer, "layer_type", "raster") == "mask"

    def _load_mask_as_selection(self, layer_id: str) -> None:
        canvas = self._get_canvas()
        if canvas is None:
            return
        fn = getattr(canvas, "load_mask_as_selection", None)
        if callable(fn):
            fn(layer_id)

    def _invert_mask_layer(self, layer_id: str) -> None:
        canvas = self._get_canvas()
        if canvas is None:
            return
        fn = getattr(canvas, "invert_mask_layer", None)
        if callable(fn) and fn(layer_id):
            self._add_default_layers()

    def _get_canvas(self) -> Any | None:
        host = self._host_api
        if not host:
            return None
        return getattr(host, "canvas", None)

    def _resolve_canvas_layer(self, layer_id: str) -> Any | None:
        """Find a layer anywhere in the canvas tree by ID."""
        host = self._host_api
        if not host:
            return None
        canvas = getattr(host, "canvas", None)
        if canvas is None:
            return None
        # Use tree-aware search if available
        finder = getattr(canvas, "_find_layer_by_id_tree", None)
        if callable(finder):
            return finder(layer_id)
        # Fallback: flat search
        layers = getattr(canvas, "_layers", None)
        if not isinstance(layers, list):
            return None
        for layer in layers:
            if getattr(layer, "id", None) == layer_id:
                return layer
        return None


if TYPE_CHECKING:  # pragma: no cover - typing aid only
    LayerPanel.layer_selected = cast(Signal1[int], LayerPanel.layer_selected)  # type: ignore[assignment]
    LayerPanel.layer_visibility_changed = cast(
        Signal2[int, bool],
        LayerPanel.layer_visibility_changed,
    )  # type: ignore[assignment]
    LayerPanel.layer_locked_changed = cast(
        Signal2[int, bool],
        LayerPanel.layer_locked_changed,
    )  # type: ignore[assignment]
    LayerPanel.layer_opacity_changed = cast(
        Signal2[int, float],
        LayerPanel.layer_opacity_changed,
    )  # type: ignore[assignment]
    LayerPanel.layer_added = cast(Signal0, LayerPanel.layer_added)  # type: ignore[assignment]
    LayerPanel.mask_layer_added = cast(Signal0, LayerPanel.mask_layer_added)  # type: ignore[assignment]
    LayerPanel.layer_deleted = cast(Signal1[int], LayerPanel.layer_deleted)  # type: ignore[assignment]
    LayerPanel.layer_reordered = cast(
        Signal2[int, int],
        LayerPanel.layer_reordered,
    )  # type: ignore[assignment]
    LayerPanel.layer_selected_by_id = cast(Signal1[str], LayerPanel.layer_selected_by_id)  # type: ignore[assignment]
    LayerPanel.layer_visibility_changed_by_id = cast(
        Signal2[str, bool],
        LayerPanel.layer_visibility_changed_by_id,
    )  # type: ignore[assignment]
    LayerPanel.layer_locked_changed_by_id = cast(
        Signal2[str, bool],
        LayerPanel.layer_locked_changed_by_id,
    )  # type: ignore[assignment]
    LayerPanel.layer_opacity_changed_by_id = cast(
        Signal2[str, float],
        LayerPanel.layer_opacity_changed_by_id,
    )  # type: ignore[assignment]
    LayerPanel.layer_deleted_by_id = cast(Signal1[str], LayerPanel.layer_deleted_by_id)  # type: ignore[assignment]
