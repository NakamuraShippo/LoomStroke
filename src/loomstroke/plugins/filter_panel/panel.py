"""Filter parameter editing panel widget."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.filters import FilterParamSchema, FilterRegistry
from loomstroke.host_api import HostAPI
from loomstroke.platform.async_task_owner import AsyncTaskOwner
from loomstroke.plugins.filter_panel import ai_effect_section as _ai_effect_section
from loomstroke.plugins.filter_panel.ai_effect_section import AIEffectPanelSection
from loomstroke.ui._dock_safe import is_valid
from loomstroke.ui.theme import current_theme_name
from loomstroke.ui.widgets.spinbox_sizing import fit_spinbox_to_values

logger = logging.getLogger(__name__)

AUTO_CONNECT_COOLDOWN_S = _ai_effect_section.AUTO_CONNECT_COOLDOWN_S
_TRIGGER_CHOICES = _ai_effect_section._TRIGGER_CHOICES
FILTER_PANEL_SPINBOX_EXTRA_DIGITS = 3


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("FilterPanel", text, use_fallback=True)


def _payload_value(payload: Any, key: str, default: Any = None) -> Any:
    if isinstance(payload, Mapping):
        return payload.get(key, default)
    return getattr(payload, key, default)


_AI_SECTION_ATTRIBUTE_NAMES = frozenset(
    {
        "_current_ai_effect_id",
        "_ai_effect_group",
        "_ai_effect_widgets",
        "_ai_slot_combos",
        "_ai_slot_rows",
        "_ai_trigger_combo",
        "_ai_generate_button",
        "_ai_server_combo",
        "_ai_output_to_self_checkbox",
        "_ai_trigger_timer",
        "_object_info_fetch_task",
        "_preview_label",
        "_auto_connect_banner",
        "_auto_connect_banner_visible",
        "_last_auto_connect_attempt_t",
        "_auto_connect_task",
    }
)

_AI_SECTION_METHOD_NAMES = frozenset(
    {
        "_get_selected_ai_effect",
        "_refresh_ai_effect",
        "_build_ai_effect_ui",
        "_sync_ai_effect_values",
        "_sync_ai_effect_param_ui_state",
        "_clear_ai_effect_ui",
        "_build_runtime_enrichment",
        "_target_server_id_for_state",
        "_cached_object_info_for_state",
        "_schedule_object_info_fetch_for_panel",
        "_fetch_object_info_and_rebuild",
        "_auto_connect_and_fetch",
        "_mount_registry_connection",
        "_call_service_get_object_info",
        "_on_ai_effect_preview_event",
        "_show_auto_connect_failure_toast",
        "_show_auto_connect_failure_banner",
        "_hide_auto_connect_failure_banner",
        "_on_auto_connect_retry_clicked",
        "_on_auto_connect_dismiss_clicked",
        "_enrich_param_entry",
        "_build_param_node_resolver",
        "_build_node_group",
        "_build_ai_slots_ui",
        "_build_ai_slot_row",
        "_collect_source_layer_candidates",
        "_select_slot_target",
        "_on_ai_slot_combo_changed",
        "_on_ai_slot_clear_clicked",
        "_write_slot_ref",
        "_build_ai_action_bar",
        "_build_ai_server_section",
        "_populate_ai_server_combo",
        "_build_ai_output_to_self_section",
        "_select_trigger_value",
        "_on_ai_trigger_changed",
        "_select_ai_server_value",
        "_on_ai_server_changed",
        "_select_output_to_self",
        "_apply_slot_disable_state",
        "_on_ai_output_to_self_toggled",
        "_on_ai_generate_clicked",
        "request_generate",
        "_apply_seed_mode_pre_generate",
        "apply_exclude_blend_toggle",
        "apply_slot_bind",
        "_on_ai_param_changed",
        "_maybe_schedule_auto_generate",
        "_resolve_debounce_ms",
        "_collect_ai_param_ui_state",
        "_on_ai_trigger_timer_fired",
        "_commit_current_ai_effect_values",
        "set_preview_image",
    }
)


class _SliderSpinRow(QWidget):
    """QSlider + QSpinBox/QDoubleSpinBox compound for bounded numeric params.

    Mirrors the AI Effect ``IntParamWidget`` / ``FloatParamWidget`` UX so
    regular filter layers expose the same drag-to-explore + type-to-precision
    behaviour. Float sliders run on integer positions and map to floats
    via a ratio, matching the heuristic used in
    ``plugins/ai_effect/ui/param_widgets.py``.
    """

    valueChanged = Signal(object)

    def __init__(
        self,
        *,
        is_float: bool,
        lo: float,
        hi: float,
        default: float,
        step: float | None,
        decimals: int,
        slider_steps: int,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._is_float = is_float
        self._slider_min = float(lo)
        self._slider_range = max(0.0, float(hi) - float(lo))
        self._slider_steps = max(0, int(slider_steps))

        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        self._spin: QDoubleSpinBox | QSpinBox
        if is_float:
            spin = QDoubleSpinBox(self)
            spin.setDecimals(decimals)
            spin.setMinimum(float(lo))
            spin.setMaximum(float(hi))
            if step is not None and step > 0:
                spin.setSingleStep(float(step))
            spin.setValue(float(default))
            self._spin = spin
        else:
            ispin = QSpinBox(self)
            ispin.setMinimum(int(lo))
            ispin.setMaximum(int(hi))
            if step is not None and step > 0:
                ispin.setSingleStep(int(step))
            ispin.setValue(int(default))
            self._spin = ispin
        fit_spinbox_to_values(
            self._spin,
            (default,),
            extra_digits=FILTER_PANEL_SPINBOX_EXTRA_DIGITS,
        )

        self._slider = QSlider(Qt.Orientation.Horizontal, self)
        self._slider.setMinimum(0)
        self._slider.setMaximum(self._slider_steps if is_float else int(hi))
        if not is_float:
            self._slider.setMinimum(int(lo))
            if step is not None and step > 0:
                self._slider.setSingleStep(int(step))
        self._slider.setMinimumWidth(30)
        self._slider.setValue(self._encode(self._spin.value()))

        self._slider.valueChanged.connect(self._on_slider_changed)
        self._spin.valueChanged.connect(self._on_spin_changed)

        layout.addWidget(self._slider, stretch=1)
        layout.addWidget(self._spin)

    def _encode(self, value: float) -> int:
        """Map a float value to an integer slider position."""
        if not self._is_float:
            return int(value)
        if self._slider_steps <= 0 or self._slider_range <= 0:
            return 0
        ratio = (float(value) - self._slider_min) / self._slider_range
        pos = round(ratio * self._slider_steps)
        return max(0, min(self._slider_steps, int(pos)))

    def _decode(self, pos: int) -> float:
        """Map an integer slider position to a float value."""
        if not self._is_float:
            return float(pos)
        if self._slider_steps <= 0:
            return self._slider_min
        ratio = pos / self._slider_steps
        return self._slider_min + ratio * self._slider_range

    def _on_slider_changed(self, pos: int) -> None:
        new_value = self._decode(pos)
        self._spin.blockSignals(True)
        try:
            self._spin.setValue(new_value if self._is_float else int(new_value))
        finally:
            self._spin.blockSignals(False)
        self.valueChanged.emit(self.value())

    def _on_spin_changed(self, value: float) -> None:
        self._slider.blockSignals(True)
        try:
            self._slider.setValue(self._encode(value))
        finally:
            self._slider.blockSignals(False)
        self.valueChanged.emit(self.value())

    def value(self) -> float | int:
        if self._is_float:
            return float(self._spin.value())
        return int(self._spin.value())

    def setValue(self, value: Any) -> None:  # noqa: N802 — Qt naming convention
        try:
            v = float(value) if self._is_float else int(value)
        except (TypeError, ValueError):
            return
        self._spin.setValue(v)


class FilterPanel(QWidget, AsyncTaskOwner):
    """Panel that displays and edits parameters for the selected filter layer."""

    # Emitted when the user clicks Generate on a bound AI Effect layer.
    # ``layer_id`` is the AI Effect layer whose state should be run.
    # Wired to the runtime AIEffectGenerationController in app.py.
    ai_generate_requested = Signal(str)

    def __getattr__(self, name: str) -> Any:
        legacy_name = {
            "_current_effect_id": "_current_filter_id",
            "_effect_graph": "_filter_graph",
        }.get(name)
        if legacy_name is not None:
            return getattr(self, legacy_name)
        if name in _AI_SECTION_ATTRIBUTE_NAMES or name in _AI_SECTION_METHOD_NAMES:
            section = self.__dict__.get("_ai_section")
            if section is not None:
                return getattr(section, name)
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        name = {
            "_current_effect_id": "_current_filter_id",
            "_effect_graph": "_filter_graph",
        }.get(name, name)
        if name in _AI_SECTION_ATTRIBUTE_NAMES or name in _AI_SECTION_METHOD_NAMES:
            section = self.__dict__.get("_ai_section")
            if section is not None:
                setattr(section, name, value)
                return
        super().__setattr__(name, value)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        AsyncTaskOwner.__init__(self)
        self._host: HostAPI | None = None
        self._event_tokens: list[Any] = []
        self._current_filter_id: str | None = None
        self._selected_layer_id: str | None = None
        self._updating = False
        self._param_widgets: dict[str, QWidget] = {}
        self._ai_section = AIEffectPanelSection(self)
        self._ai_section.ai_generate_requested.connect(self.ai_generate_requested.emit)
        # Photoshop-style interactive graph (Levels / Curves). Created on
        # demand inside ``_build_param_ui`` for tone-adjustment effects;
        # left as ``None`` for effects with no LUT-based visualisation.
        self._filter_graph: QWidget | None = None
        # Submit throttle for regular-effect param edits. The widget can
        # emit ``valueChanged`` per drag tick (60-240Hz); each submit
        # triggers a full canvas ``_recompose``. We use a leading-edge
        # 16ms cooldown to coalesce bursts: the first edit submits
        # immediately so users see live feedback, subsequent edits within
        # 16ms queue up and fire once at the end of the cooldown. Pen
        # tool live-preview is unaffected because it goes through a
        # different command path (``PaintCommand``), not this throttle.
        self._param_submit_cooldown_ms = 16
        self._param_submit_in_cooldown = False
        self._param_submit_pending_key: str | None = None
        self._param_submit_timer = QTimer(self)
        self._param_submit_timer.setSingleShot(True)
        self._param_submit_timer.timeout.connect(self._on_param_submit_cooldown_expired)
        # Last-submitted snapshot for the same-value guard. Cleared
        # whenever ``_set_param_values`` pushes external state into the
        # widgets so that a subsequent user edit submits even if it
        # coincides with the previously-submitted value.
        self._last_submitted_params: dict[str, Any] | None = None
        # Canvas echoes regular-effect param edits through
        # layer.changed(effect_params_changed). While the user is
        # dragging, that echo is our own write and must not push an
        # older submitted tick back into the widgets.
        self._pending_self_param_events = 0
        self._applied_theme_name: str | None = None
        self._theme_stylesheet_pending = False
        self._applying_theme_stylesheet = False
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        # Two-tier layout: an outer QVBoxLayout holds a QScrollArea
        # whose viewport widget owns the *real* root layout. All
        # existing ``self._root_layout.insertWidget(...)`` /
        # ``removeWidget(...)`` callers target the inner layout,
        # so groups + param widgets live inside the scroll viewport.
        # When the dock height isn't tall enough to fit a long preset
        # (think 20+ params each as label + slider+spin), the
        # vertical scrollbar appears automatically. Horizontal
        # scrolling is suppressed because the panel's WrapAllRows
        # form policy already handles narrow widths.
        self.setObjectName("FilterPanelRoot")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._scroll_area = QScrollArea(self)
        self._scroll_area.setObjectName("aiEffectScroll")
        self._scroll_area.setFrameShape(QScrollArea.Shape.NoFrame)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        outer.addWidget(self._scroll_area)

        self._scroll_content = QWidget(self._scroll_area)
        self._scroll_content.setObjectName("aiEffectScrollContent")
        self._scroll_area.setWidget(self._scroll_content)

        self._root_layout = QVBoxLayout(self._scroll_content)
        self._root_layout.setContentsMargins(8, 8, 8, 8)
        self._root_layout.setSpacing(8)

        # Allow the dock host to shrink the panel below the natural
        # min size of an AI Effect param row (slider + spinbox + label
        # column would otherwise pin the dock to ~280px). 160px keeps
        # widgets usable without forcing a horizontal scrollbar at the
        # default position.
        self.setMinimumWidth(160)

        self._placeholder = QLabel(_ts("No filter selected"))
        self._placeholder.setObjectName("aiEffectPlaceholder")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._root_layout.addWidget(self._placeholder)

        self._group: QGroupBox | None = None
        self._form: QFormLayout | None = None
        self._root_layout.addStretch()

        # Apply the modern dark theme last so child object names are
        # already in place — QSS attaches via ``#objectName`` selectors,
        # so naming has to happen first.
        self._apply_modern_stylesheet()

    def _apply_modern_stylesheet(self) -> None:
        """Apply the v5 mockup's dark / purple-accent theme via QSS.

        The stylesheet is scoped under ``#FilterPanelRoot`` so the
        styling never leaks to the regular-effect form path or to
        unrelated panels rendered in the same QApplication. Object
        names assigned in :meth:`_setup_ui`, :meth:`_build_ai_effect_ui`,
        and :meth:`_build_node_group` map to the per-element rules
        below; widgets without a target name fall through to the
        application's default palette.

        Spec reference: ``AIeffect_design/01_ui_mockups/effect_panel/
        effect_panel_v5_final.html``.
        """
        theme_name = current_theme_name()
        if self._applying_theme_stylesheet:
            return
        if self._applied_theme_name == theme_name:
            self._apply_inline_theme_styles()
            return
        self._applying_theme_stylesheet = True

        if theme_name == "light":
            self.setStyleSheet("")
            self._applied_theme_name = theme_name
            self._applying_theme_stylesheet = False
            self._apply_inline_theme_styles()
            return
        self.setStyleSheet("""
            /* ---------- Root surface ---------- */
            QWidget#FilterPanelRoot,
            QWidget#aiEffectScrollContent {
                background-color: #1a1a1a;
                color: #e5e5e5;
            }
            QScrollArea#aiEffectScroll {
                background-color: #1a1a1a;
                border: none;
            }
            QWidget#FilterPanelRoot QLabel {
                color: #b8b8b8;
                background-color: transparent;
            }

            QLabel#aiEffectPlaceholder {
                color: #5a5a5a;
                font-style: italic;
                padding: 24px 12px;
            }

            /* ---------- Section headings ---------- */
            QLabel#aiSectionHeading,
            QLabel#aiTriggerLabel {
                color: #828282;
                font-size: 10px;
                font-weight: 700;
                letter-spacing: 0.04em;
                text-transform: uppercase;
                padding: 4px 0 2px 0;
            }

            QLabel#aiSlotLabel {
                color: #b8b8b8;
                font-size: 11px;
            }

            /* ---------- Preset title group ---------- */
            QGroupBox#aiEffectPresetGroup {
                background-color: #1a1a1a;
                border: 1px solid #111111;
                border-radius: 4px;
                margin-top: 14px;
                padding-top: 10px;
                font-weight: 600;
            }
            QGroupBox#aiEffectPresetGroup::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 10px;
                padding: 2px 8px;
                color: #a78bfa;
                background-color: #242424;
                border: 1px solid #111111;
                border-radius: 2px;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.04em;
            }

            /* ---------- Per-node group ---------- */
            QGroupBox#aiNodeGroup {
                background-color: #1a1a1a;
                border: 1px solid #2e2e2e;
                border-left: 3px solid #61afef;
                border-radius: 3px;
                margin-top: 14px;
                padding-top: 8px;
            }
            QGroupBox#aiNodeGroup::title {
                subcontrol-origin: margin;
                subcontrol-position: top left;
                left: 4px;
                padding: 2px 6px;
                color: #61afef;
                background-color: #242424;
                font-size: 11px;
                font-weight: 700;
            }
            QGroupBox#aiNodeGroup::indicator {
                width: 10px;
                height: 10px;
            }

            /* ---------- Combos (slot binders + trigger) ---------- */
            QWidget#FilterPanelRoot QComboBox {
                background-color: #242424;
                color: #e5e5e5;
                border: 1px solid #3a3a3a;
                border-radius: 2px;
                padding: 3px 6px;
                selection-background-color: #6d5ba3;
                min-height: 22px;
            }
            QWidget#FilterPanelRoot QComboBox:hover {
                border-color: #454545;
            }
            QWidget#FilterPanelRoot QComboBox:focus {
                border-color: #a78bfa;
            }
            QWidget#FilterPanelRoot QComboBox::drop-down {
                border: none;
                width: 18px;
            }
            QWidget#FilterPanelRoot QComboBox QAbstractItemView {
                background-color: #242424;
                color: #e5e5e5;
                border: 1px solid #3a3a3a;
                selection-background-color: #6d5ba3;
                selection-color: #ffffff;
                outline: 0;
            }

            /* ---------- Text inputs / spinboxes ---------- */
            QWidget#FilterPanelRoot QLineEdit,
            QWidget#FilterPanelRoot QSpinBox,
            QWidget#FilterPanelRoot QDoubleSpinBox,
            QWidget#FilterPanelRoot QTextEdit,
            QWidget#FilterPanelRoot QPlainTextEdit {
                background-color: #242424;
                color: #e5e5e5;
                border: 1px solid #3a3a3a;
                border-radius: 2px;
                padding: 3px 6px;
                selection-background-color: #6d5ba3;
            }
            QWidget#FilterPanelRoot QLineEdit:focus,
            QWidget#FilterPanelRoot QSpinBox:focus,
            QWidget#FilterPanelRoot QDoubleSpinBox:focus,
            QWidget#FilterPanelRoot QTextEdit:focus,
            QWidget#FilterPanelRoot QPlainTextEdit:focus {
                border-color: #a78bfa;
            }
            QWidget#FilterPanelRoot QLineEdit,
            QWidget#FilterPanelRoot QSpinBox,
            QWidget#FilterPanelRoot QDoubleSpinBox {
                padding: 1px 7px;
                min-height: 16px;
                max-height: 18px;
            }
            /* ---------- Sliders (purple fill + white thumb) ----------
               Slider 全体の高さは ``handle`` の height + 上下マージン
               に支配される。3px グルーヴ + 8px ハンドル (margin -3px)
               → ウィジェット高さ ~14px に収まる (旧設計は 22px 級)。 */
            QWidget#FilterPanelRoot QSlider::groove:horizontal {
                background-color: #2e2e2e;
                height: 3px;
                border-radius: 1px;
            }
            QWidget#FilterPanelRoot QSlider::sub-page:horizontal {
                background-color: #a78bfa;
                border-radius: 1px;
            }
            QWidget#FilterPanelRoot QSlider::add-page:horizontal {
                background-color: #2e2e2e;
                border-radius: 1px;
            }
            QWidget#FilterPanelRoot QSlider::handle:horizontal {
                background-color: #ffffff;
                width: 8px;
                height: 8px;
                margin: -3px 0;
                border-radius: 4px;
                border: 1px solid #c8c8c8;
            }
            QWidget#FilterPanelRoot QSlider::handle:horizontal:hover {
                background-color: #f5f5f5;
                border-color: #a78bfa;
            }

            /* ---------- Tool buttons (slot clear "x" etc.) ---------- */
            QWidget#FilterPanelRoot QToolButton {
                background-color: #242424;
                color: #b8b8b8;
                border: 1px solid #3a3a3a;
                border-radius: 2px;
                padding: 2px 6px;
                min-width: 22px;
                min-height: 22px;
            }
            QWidget#FilterPanelRoot QToolButton:hover {
                background-color: #2e2e2e;
                color: #e06c75;
                border-color: #454545;
            }

            /* ---------- Generate button (purple gradient hero) ---------- */
            QPushButton#aiGenerateButton {
                background-color: #8b5cf6;
                border: 1px solid #a78bfa;
                color: #ffffff;
                font-size: 12px;
                font-weight: 700;
                letter-spacing: 0.04em;
                padding: 8px 12px;
                border-radius: 3px;
                min-height: 28px;
            }
            QPushButton#aiGenerateButton:hover {
                background-color: #9f78fa;
            }
            QPushButton#aiGenerateButton:pressed {
                background-color: #6d5ba3;
            }
            QPushButton#aiGenerateButton:disabled {
                background-color: #2e2e2e;
                color: #5a5a5a;
                border-color: #3a3a3a;
            }

            /* ---------- Form layout label tone ---------- */
            QWidget#FilterPanelRoot QFormLayout QLabel {
                color: #b8b8b8;
                font-size: 11px;
            }

            /* ---------- Scrollbars (subtle dark) ---------- */
            QWidget#FilterPanelRoot QScrollBar:vertical {
                background-color: #1a1a1a;
                width: 10px;
                margin: 0;
            }
            QWidget#FilterPanelRoot QScrollBar::handle:vertical {
                background-color: #3a3a3a;
                border-radius: 3px;
                min-height: 24px;
            }
            QWidget#FilterPanelRoot QScrollBar::handle:vertical:hover {
                background-color: #454545;
            }
            QWidget#FilterPanelRoot QScrollBar::add-line:vertical,
            QWidget#FilterPanelRoot QScrollBar::sub-line:vertical {
                height: 0;
            }
            QWidget#FilterPanelRoot QScrollBar::add-page:vertical,
            QWidget#FilterPanelRoot QScrollBar::sub-page:vertical {
                background: none;
            }
        """)
        self._applied_theme_name = theme_name
        self._applying_theme_stylesheet = False
        self._apply_inline_theme_styles()

    def _apply_inline_theme_styles(self) -> None:
        if is_valid(self._preview_label):
            self._preview_label.setStyleSheet(self._preview_thumbnail_stylesheet())
        if is_valid(self._auto_connect_banner):
            self._auto_connect_banner.setStyleSheet(self._auto_connect_banner_stylesheet())

    def _preview_thumbnail_stylesheet(self) -> str:
        if current_theme_name() == "light":
            return (
                "#aiEffectPreviewThumbnail {"
                "  background-color: #f0f2f5;"
                "  border: 1px solid #b7bfcc;"
                "  border-radius: 4px;"
                "  min-height: 40px;"
                "}"
            )
        return (
            "#aiEffectPreviewThumbnail {"
            "  background-color: #1e1e1e;"
            "  border: 1px solid #3a3a3a;"
            "  border-radius: 4px;"
            "  min-height: 40px;"
            "}"
        )

    def _auto_connect_banner_stylesheet(self) -> str:
        if current_theme_name() == "light":
            return (
                "#aiAutoConnectBanner {"
                "  background-color: #fff1f1;"
                "  border: 1px solid #d58a8a;"
                "  border-radius: 4px;"
                "}"
                "#aiAutoConnectBanner QLabel { color: #6f2b2b; }"
            )
        return (
            "#aiAutoConnectBanner {"
            "  background-color: #4a2a2a;"
            "  border: 1px solid #8b4a4a;"
            "  border-radius: 4px;"
            "}"
            "#aiAutoConnectBanner QLabel { color: #f0d0d0; }"
        )

    def _build_param_ui(self, filter_type: str, schemas: list[FilterParamSchema]) -> None:
        """Build form widgets for the given filter parameter schemas."""
        self._clear_param_ui()

        filter_cls = FilterRegistry.get(filter_type)
        title = _ts(filter_cls().name()) if filter_cls else filter_type
        self._group = QGroupBox(title, self)
        self._form = QFormLayout(self._group)
        self._form.setContentsMargins(6, 6, 6, 6)
        # WrapAllRows: when the dock is dragged narrow, each label
        # stacks above its widget instead of clamping the panel min
        # width to ``label_col + widget_col``. Lets users squeeze the
        # Effects panel into a sidebar.
        self._form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        self._form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)

        # Photoshop-style graph for tone-adjustment effects. Built before
        # the form rows so it sits at the top of the group box.
        self._build_filter_graph(filter_type)

        for schema in schemas:
            widget = self._create_param_widget(schema)
            if widget is not None:
                self._form.addRow(_ts(schema.label), widget)
                self._param_widgets[schema.key] = widget

        # Wire spin/slider edits → graph so dragging a slider redraws the
        # curve live. Graph→spin runs through ``_on_filter_graph_value_changed``.
        if self._filter_graph is not None:
            self._wire_filter_graph_signals()

        # Insert before stretch
        self._root_layout.insertWidget(0, self._group)
        self._placeholder.hide()

    def _build_filter_graph(self, filter_type: str) -> None:
        """Insert a Levels graph at the top of the form, if applicable.

        Curves now declares its own ``curve_points`` schema entry, so its
        graph is created via :meth:`_create_param_widget` like any other
        param widget — no special-case path needed here.
        """
        from loomstroke.plugins.filter_panel.filter_graph_widgets import (  # noqa: PLC0415
            LevelsGraphWidget,
        )

        graph: QWidget | None = None
        if filter_type == "levels":
            graph = LevelsGraphWidget(self._group)
        if graph is None:
            return
        # ``addRow`` with a single widget spans the whole form column; the
        # graph stays centred above the param sliders.
        assert self._form is not None
        self._form.addRow(graph)
        self._filter_graph = graph

    def _wire_filter_graph_signals(self) -> None:
        """Hook spin↔graph two-way binding once both halves exist."""
        graph = self._filter_graph
        if graph is None:
            return
        # graph → spin (then submit). Always uses object payload because
        # the graph emits ``Signal(str, object)`` for both int and float keys.
        emitter = getattr(graph, "value_changed", None)
        if emitter is not None:
            emitter.connect(self._on_filter_graph_value_changed)
        # spin → graph: re-push the full param dict so derived markers
        # (e.g. gamma's screen position depends on in_black/in_white) stay
        # consistent regardless of which slider the user moved.
        for widget in self._param_widgets.values():
            value_signal = getattr(widget, "valueChanged", None)
            if value_signal is None:
                continue
            value_signal.connect(self._sync_graph_from_widgets)

    def _sync_graph_from_widgets(self, *_args: Any) -> None:
        graph = self._filter_graph
        if graph is None:
            return
        setter = getattr(graph, "set_params", None)
        if callable(setter):
            setter(self._collect_params())

    def _on_filter_graph_value_changed(self, key: str, value: Any) -> None:
        """Drag a graph marker → push the new value through the spinbox."""
        widget = self._param_widgets.get(key)
        if widget is None:
            return
        # Suppress the spin's automatic submit so we can drive a single
        # ``_on_param_changed`` after the value lands. Avoids a redundant
        # double-submit per drag tick.
        self._updating = True
        try:
            setter = getattr(widget, "setValue", None)
            if callable(setter):
                setter(value)
        finally:
            self._updating = False
        self._on_param_changed(key)

    def _clear_param_ui(self) -> None:
        self._param_widgets.clear()
        # Graph lives inside the QGroupBox, so deleteLater on the group
        # tears it down too — we just drop our cached pointer.
        self._filter_graph = None
        # Cancel any in-flight throttle so the next effect's edits aren't
        # accidentally suppressed by leftover cooldown state.
        self._param_submit_timer.stop()
        self._param_submit_in_cooldown = False
        self._param_submit_pending_key = None
        self._last_submitted_params = None
        self._pending_self_param_events = 0
        if self._group is not None:
            self._root_layout.removeWidget(self._group)
            self._group.deleteLater()
            self._group = None
            self._form = None
        self._placeholder.show()

    @staticmethod
    def _float_slider_metrics(lo: float, hi: float, step: float | None) -> tuple[int, int]:
        """Pick (decimals, slider_steps) using the AI Effect float heuristic.

        Range size dictates display precision and how many integer slider
        positions cover the span. Same buckets as
        ``plugins/ai_effect/ui/param_widgets.FloatParamWidget`` so users get
        consistent feel across regular and AI effect layers.
        """
        rng_size = max(0.0, hi - lo)
        if rng_size <= 0:
            return 2, 0
        if rng_size <= 1.0:
            decimals, default_step = 3, 0.001
        elif rng_size <= 10:
            decimals, default_step = 2, 0.01
        elif rng_size <= 100:
            decimals, default_step = 1, 0.1
        else:
            decimals = 0
            default_step = max(1.0, rng_size / 1000.0)
        effective_step = step if (step is not None and step > 0) else default_step
        # A large range must not discard a schema's fractional editing step.
        step_decimals = max(0, -Decimal(str(effective_step)).normalize().as_tuple().exponent)
        decimals = max(decimals, min(15, step_decimals))
        slider_steps = min(2000, max(1, round(rng_size / effective_step)))
        return decimals, slider_steps

    def _create_param_widget(  # noqa: PLR0911 - linear param-type dispatch
        self, schema: FilterParamSchema
    ) -> QWidget | None:
        if schema.param_type == "float":
            if schema.min_val is not None and schema.max_val is not None:
                lo = float(schema.min_val)
                hi = float(schema.max_val)
                step = float(schema.step) if schema.step is not None else None
                decimals, slider_steps = self._float_slider_metrics(lo, hi, step)
                row = _SliderSpinRow(
                    is_float=True,
                    lo=lo,
                    hi=hi,
                    default=float(schema.default),
                    step=step,
                    decimals=decimals,
                    slider_steps=slider_steps,
                    parent=self,
                )
                row.valueChanged.connect(lambda _v, k=schema.key: self._on_param_changed(k))
                return row
            # Unbounded float — slider would be meaningless, fall back to plain spin.
            spin = QDoubleSpinBox(self)
            spin.setDecimals(2)
            if schema.step is not None:
                spin.setSingleStep(float(schema.step))
            spin.setValue(float(schema.default))
            fit_spinbox_to_values(
                spin,
                (float(schema.default),),
                extra_digits=FILTER_PANEL_SPINBOX_EXTRA_DIGITS,
            )
            spin.valueChanged.connect(lambda _v, k=schema.key: self._on_param_changed(k))
            return spin
        if schema.param_type == "int":
            if schema.min_val is not None and schema.max_val is not None:
                lo_i = int(schema.min_val)
                hi_i = int(schema.max_val)
                step_i = float(schema.step) if schema.step is not None else None
                row = _SliderSpinRow(
                    is_float=False,
                    lo=float(lo_i),
                    hi=float(hi_i),
                    default=float(schema.default),
                    step=step_i,
                    decimals=0,
                    slider_steps=hi_i - lo_i,
                    parent=self,
                )
                row.valueChanged.connect(lambda _v, k=schema.key: self._on_param_changed(k))
                return row
            ispin = QSpinBox(self)
            if schema.step is not None:
                ispin.setSingleStep(int(schema.step))
            ispin.setValue(int(schema.default))
            fit_spinbox_to_values(
                ispin,
                (int(schema.default),),
                extra_digits=FILTER_PANEL_SPINBOX_EXTRA_DIGITS,
            )
            ispin.valueChanged.connect(lambda _v, k=schema.key: self._on_param_changed(k))
            return ispin
        if schema.param_type == "enum":
            return self._create_enum_widget(schema)
        if schema.param_type == "gradient_stops":
            from .gradient_stops_widget import GradientStopsWidget  # noqa: PLC0415

            w = GradientStopsWidget(self)
            if schema.default is not None:
                w.setValue(schema.default)
            w.stops_changed.connect(lambda k=schema.key: self._on_param_changed(k))
            return w
        if schema.param_type == "curve_points":
            from .filter_graph_widgets import CurvesGraphWidget  # noqa: PLC0415

            curve = CurvesGraphWidget(self)
            if schema.default is not None:
                curve.setValue(schema.default)
            curve.valueChanged.connect(lambda _v, k=schema.key: self._on_param_changed(k))
            return curve
        logger.debug("Unsupported param type: %s", schema.param_type)
        return None

    def _create_enum_widget(self, schema: FilterParamSchema) -> QComboBox:
        """Stable value in ``itemData``, translated label as text (CT-02 tone
        pattern / origin / preview policy).  A language switch rebuilds the
        form, so labels follow without touching the stored value."""
        combo = QComboBox(self)
        for choice in schema.choices or []:
            combo.addItem(_ts(self._enum_choice_label(str(choice))), str(choice))
        default_index = combo.findData(str(schema.default))
        if default_index >= 0:
            combo.setCurrentIndex(default_index)
        combo.currentIndexChanged.connect(lambda _i, k=schema.key: self._on_param_changed(k))
        return combo

    @staticmethod
    def _enum_choice_label(choice: str) -> str:
        """Source label for an enum choice id (``moire_safe`` -> ``Moire Safe``)."""
        return choice.replace("_", " ").title()

    # ------------------------------------------------------------------
    # Host binding
    # ------------------------------------------------------------------

    def set_host(self, host: HostAPI) -> None:
        self._host = host
        self._ai_section.set_host(host)
        bus = getattr(host, "events", None)
        if bus is None:
            return
        subscribe = getattr(bus, "on", None)
        if not callable(subscribe):
            return
        # Store strong references to bound methods so that the EventBus
        # weak-ref subscriptions survive garbage collection.
        self._handler_layer_selected = self._on_layer_selected
        self._handler_layer_event = self._on_layer_event
        self._handler_ai_effect_preview = self._on_ai_effect_preview_event
        self._event_tokens.append(subscribe("layer.selected", self._handler_layer_selected))
        self._event_tokens.append(subscribe("layer.changed", self._handler_layer_event))
        self._event_tokens.append(subscribe("effect.params.changed", self._handler_layer_event))
        # Phase 8c-2 preview wiring: app.py emits ``ai_effect.preview``
        # whenever the controller's ``on_preview`` callback fires, so
        # the panel can render a thumbnail without holding a direct
        # reference to the controller.
        self._event_tokens.append(subscribe("ai_effect.preview", self._handler_ai_effect_preview))
        self._refresh()

    def detach_host(self) -> None:
        self._ai_section.detach_host()
        self.cancel_owned_tasks()
        self._host = None
        self._event_tokens.clear()
        self._current_filter_id = None
        self._clear_param_ui()

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _on_layer_selected(self, payload: Any = None) -> None:
        """Track which layer is selected via event bus."""
        lid = _payload_value(payload, "layer_id") or _payload_value(payload, "id")
        if isinstance(lid, str):
            self._selected_layer_id = lid
        self._refresh()

    def _on_layer_event(self, payload: Any = None) -> None:
        change_type = _payload_value(payload, "change_type")
        if change_type == "effect_params_changed":
            layer_id = _payload_value(payload, "layer_id")
            if (
                isinstance(layer_id, str)
                and self._selected_layer_id is not None
                and layer_id != self._selected_layer_id
            ):
                return
            if (
                isinstance(layer_id, str)
                and layer_id == self._current_filter_id
                and self._pending_self_param_events > 0
            ):
                self._pending_self_param_events -= 1
                return
        self._refresh()

    def _refresh(self) -> None:
        """Update the panel to reflect the currently selected layer.

        Dispatches between the regular-effect path and the ai_effect
        path; never both at once. Switching layer types (effect <->
        ai_effect <-> none) tears down the previous form before
        building the next so recycled widgets cannot leak Qt parents.
        """
        # ---- AI Effect branch (Step 2) ----
        ai_id, ai_state = self._get_selected_ai_effect()
        if self._current_ai_effect_id is not None and ai_id != self._current_ai_effect_id:
            self._commit_current_ai_effect_values()
        if ai_id is not None and ai_state is not None:
            # Tear down any stale regular-effect UI when flipping type.
            if self._current_filter_id is not None:
                self._current_filter_id = None
                self._clear_param_ui()
            self._refresh_ai_effect(ai_id, ai_state)
            return

        # ---- Regular effect branch (pre-existing) ----
        # Tear down any stale AI Effect UI when flipping back to a
        # regular effect / blank selection so the preset-name GroupBox
        # does not linger over an unrelated layer.
        if self._current_ai_effect_id is not None:
            self._current_ai_effect_id = None
            self._clear_ai_effect_ui()

        filter_id, filter_type, params = self._get_selected_filter()
        if filter_id is None or filter_type is None:
            if self._current_filter_id is not None:
                self._current_filter_id = None
                self._clear_param_ui()
            return

        # Rebuild UI if the selected filter changed.
        if filter_id != self._current_filter_id:
            self._current_filter_id = filter_id
            self._last_submitted_params = None
            self._pending_self_param_events = 0
            filter_cls = FilterRegistry.get(filter_type)
            if filter_cls is None:
                self._clear_param_ui()
                return
            schemas = filter_cls().param_schema()
            self._build_param_ui(filter_type, schemas)

        # Populate values
        if params:
            self._set_param_values(params)

    # ------------------------------------------------------------------
    # AI Effect facade — owned by AIEffectPanelSection
    # ------------------------------------------------------------------

    def request_generate(self, layer_id: str) -> None:
        self._ai_section.request_generate(layer_id)

    def set_preview_image(self, layer_id: str, png_bytes: bytes) -> None:
        self._ai_section.set_preview_image(layer_id, png_bytes)

    def apply_exclude_blend_toggle(
        self, effect_layer_id: str, slot_id: str, new_value: bool
    ) -> None:
        self._ai_section.apply_exclude_blend_toggle(effect_layer_id, slot_id, new_value)

    def apply_slot_bind(self, effect_layer_id: str, slot_id: str, source_layer_id: str) -> None:
        self._ai_section.apply_slot_bind(effect_layer_id, slot_id, source_layer_id)

    def _get_selected_filter(
        self,
    ) -> tuple[str | None, str | None, dict[str, Any] | None]:
        """Return (filter_layer_id, filter_type, params) for the active layer panel selection."""
        _none = (None, None, None)
        if self._host is None:
            return _none
        canvas = getattr(self._host, "canvas", None)
        if canvas is None:
            return _none

        selected_id = self._selected_layer_id
        if selected_id is None:
            return _none

        # Find the layer in canvas tree
        finder = getattr(canvas, "_find_layer_by_id_tree", None)
        if not callable(finder):
            return _none
        layer = finder(selected_id)
        if layer is None or getattr(layer, "layer_type", "") != "effect":
            return _none

        filter_type = getattr(layer, "filter_type", getattr(layer, "effect_type", None))
        filter_params = getattr(layer, "filter_params", getattr(layer, "effect_params", {}))
        return selected_id, filter_type, dict(filter_params) if filter_params else {}

    def _set_param_values(self, params: dict[str, Any]) -> None:
        from .filter_graph_widgets import CurvesGraphWidget  # noqa: PLC0415
        from .gradient_stops_widget import GradientStopsWidget  # noqa: PLC0415

        self._updating = True
        try:
            for key, widget in self._param_widgets.items():
                value = params.get(key)
                if value is None:
                    continue
                if isinstance(widget, _SliderSpinRow):
                    widget.setValue(value)
                elif isinstance(widget, QDoubleSpinBox):
                    widget.setValue(float(value))
                elif isinstance(widget, QSpinBox):
                    widget.setValue(int(value))
                elif isinstance(widget, QComboBox):
                    index = widget.findData(str(value))
                    if index >= 0:
                        widget.setCurrentIndex(index)
                elif isinstance(widget, (GradientStopsWidget, CurvesGraphWidget)):
                    widget.setValue(value)
        finally:
            self._updating = False
        # External state landed (undo, layer reselect, plugin update,
        # etc.) — drop our same-value memo so the next user edit always
        # submits, even if its value happens to equal what we last sent.
        self._last_submitted_params = None
        self._pending_self_param_events = 0

    # ------------------------------------------------------------------
    # Parameter changes
    # ------------------------------------------------------------------

    def _on_param_changed(self, key: str) -> None:
        """Front door for widget edits — throttles bursts to 60Hz.

        Leading-edge throttle: the first change after a quiescent period
        submits immediately (so users see live feedback) and starts a
        16ms cooldown. Subsequent changes within the cooldown queue up
        as ``_param_submit_pending_key`` and fire once at expiry. This
        caps the per-second submit rate to ~60 even if the widget emits
        valueChanged at 240Hz, which keeps ``_recompose`` from
        backlogging on large canvases. Pen tool live-preview is on a
        different code path and is not affected.
        """
        if self._updating:
            return
        if self._current_filter_id is None or self._host is None:
            return
        if self._param_submit_in_cooldown:
            self._param_submit_pending_key = key
            return
        submitted = self._submit_param_change_now(key)
        if submitted:
            self._param_submit_in_cooldown = True
            self._param_submit_timer.start(self._param_submit_cooldown_ms)

    def _on_param_submit_cooldown_expired(self) -> None:
        """Cooldown over — flush any pending edit accumulated mid-burst."""
        self._param_submit_in_cooldown = False
        pending = self._param_submit_pending_key
        if pending is None:
            return
        self._param_submit_pending_key = None
        submitted = self._submit_param_change_now(pending)
        if submitted:
            # Re-arm the cooldown so the burst stays coalesced even when
            # ``valueChanged`` keeps firing past the first cooldown.
            self._param_submit_in_cooldown = True
            self._param_submit_timer.start(self._param_submit_cooldown_ms)

    def _submit_param_change_now(self, key: str) -> bool:
        """Send the latest param snapshot to the canvas. Returns True on
        actual submit, False when same-value guard suppressed it."""
        if self._current_filter_id is None or self._host is None:
            return False
        canvas = getattr(self._host, "canvas", None)
        if canvas is None:
            return False

        # Collect all current param values
        params = self._collect_params()

        # Same-value guard — every drag tick collects the same dict
        # whenever no widget actually moved. Skipping the submit avoids
        # a redundant ``_recompose`` and the resulting widget setValue
        # round-trip. Cleared by ``_set_param_values`` when external
        # state lands so the next user edit re-submits even if it
        # coincides with the last submitted value.
        if self._last_submitted_params == params:
            return False

        from loomstroke.core.commands import ModifyFilterParamsCommand  # noqa: PLC0415

        self._last_submitted_params = dict(params)
        self._pending_self_param_events += 1
        submitted = False
        submit = getattr(canvas, "submit", None)
        if callable(submit):
            cmd = ModifyFilterParamsCommand(
                filter_layer_id=self._current_filter_id,
                params=params,
                merge_key=f"effect_param_{self._current_filter_id}",
            )
            submitted = submit(cmd) is not False
        else:
            setter = getattr(canvas, "set_filter_params", None) or getattr(
                canvas, "set_effect_params", None
            )
            if callable(setter):
                submitted = setter(self._current_filter_id, params) is not False
        if not submitted:
            self._last_submitted_params = None
            self._pending_self_param_events = max(0, self._pending_self_param_events - 1)
            return False
        logger.debug(
            "Updated filter %s params: %s=%s", self._current_filter_id, key, params.get(key)
        )
        return True

    def _collect_params(self) -> dict[str, Any]:
        from .filter_graph_widgets import CurvesGraphWidget  # noqa: PLC0415
        from .gradient_stops_widget import GradientStopsWidget  # noqa: PLC0415

        result: dict[str, Any] = {}
        for key, widget in self._param_widgets.items():
            if isinstance(widget, QComboBox):
                result[key] = widget.currentData()
            elif isinstance(
                widget,
                (
                    _SliderSpinRow,
                    QDoubleSpinBox,
                    QSpinBox,
                    GradientStopsWidget,
                    CurvesGraphWidget,
                ),
            ):
                result[key] = widget.value()
        return result

    # ------------------------------------------------------------------
    # i18n
    # ------------------------------------------------------------------

    def changeEvent(self, event: QEvent) -> None:  # type: ignore[override]  # PySide6 override stub limitation
        if not is_valid(self):
            return
        with contextlib.suppress(RuntimeError):
            super().changeEvent(event)
        event_type = event.type()
        if event_type == QEvent.Type.LanguageChange:
            self._retranslate_ui()
        elif event_type in (
            QEvent.Type.ApplicationPaletteChange,
            QEvent.Type.PaletteChange,
        ):
            self._request_modern_stylesheet_refresh()

    def _request_modern_stylesheet_refresh(self) -> None:
        if self._theme_stylesheet_pending:
            return
        self._theme_stylesheet_pending = True
        QTimer.singleShot(0, self._apply_pending_modern_stylesheet)

    def _apply_pending_modern_stylesheet(self) -> None:
        self._theme_stylesheet_pending = False
        if is_valid(self):
            self._apply_modern_stylesheet()

    def _retranslate_ui(self) -> None:
        if not is_valid(getattr(self, "_placeholder", None)):
            return
        self._placeholder.setText(_ts("No filter selected"))
        if self._current_ai_effect_id is not None:
            self._commit_current_ai_effect_values()
            layer_id, state = self._get_selected_ai_effect()
            if layer_id is not None and state is not None:
                self._build_ai_effect_ui(state)
                self._sync_ai_effect_values(state)
                return
        # Rebuild param UI to refresh labels if an effect is active
        if self._current_filter_id is not None:
            filter_id, filter_type, params = self._get_selected_filter()
            if filter_id is not None and filter_type is not None:
                filter_cls = FilterRegistry.get(filter_type)
                if filter_cls is not None:
                    schemas = filter_cls().param_schema()
                    self._build_param_ui(filter_type, schemas)
                    if params:
                        self._set_param_values(params)


# Import and test compatibility for the former effect-panel facade.
EffectPanel = FilterPanel
EffectParamSchema = FilterParamSchema
EffectRegistry = FilterRegistry
EFFECT_PANEL_SPINBOX_EXTRA_DIGITS = FILTER_PANEL_SPINBOX_EXTRA_DIGITS
FilterPanel._build_effect_graph = FilterPanel._build_filter_graph
FilterPanel._wire_effect_graph_signals = FilterPanel._wire_filter_graph_signals
FilterPanel._on_effect_graph_value_changed = FilterPanel._on_filter_graph_value_changed
FilterPanel._get_selected_effect = FilterPanel._get_selected_filter
