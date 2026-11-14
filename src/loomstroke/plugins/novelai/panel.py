"""Manual NovelAI image generation panel."""

from __future__ import annotations

import logging
import secrets
from typing import Any

from PySide6.QtCore import QEvent, QMetaObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QShowEvent
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.events import Events
from loomstroke.platform.settings_manager import get_settings_manager
from loomstroke.plugins.comfyui.ui.prompt_composer import PromptComposerWidget
from loomstroke.plugins.common.outbound_policy import is_external_services_allowed
from loomstroke.plugins.novelai.ai_settings_widget import AISettingsWidget
from loomstroke.plugins.novelai.collapsible_section import CollapsibleSection
from loomstroke.plugins.novelai.image_settings_widget import ImageSettingsWidget
from loomstroke.plugins.novelai.vibe_references_widget import VibeReferencesWidget
from loomstroke.plugins.prompt_editor.resizable_prompt_area import ResizableTextEditArea
from loomstroke.ui._dock_safe import is_valid
from loomstroke.ui.translations import translated_literal_domain

_REFERENCE_AND_VARIETY_MODELS = frozenset({"nai-diffusion-4-5-full", "nai-diffusion-4-5-curated"})
_MODES = (
    ("Image Generation", "generate", "Generate"),
    ("Image2Image", "img2img", "i2i"),
    ("Inpaint (current selection)", "inpaint", "Inpaint"),
    ("Director Tools", "augment", "Tools"),
    ("Upscale", "upscale", "Enlarge"),
)
logger = logging.getLogger(__name__)


class NovelAIPanel(QWidget):
    """Collect a single human-initiated NovelAI request."""

    requested = Signal(object)
    tagsRequested = Signal(str, str)

    def tr(self, text: str) -> str:  # type: ignore[override]
        return translated_literal_domain("NovelAIPanel", text, use_fallback=True)

    def __init__(self, host: Any, parent: QWidget | None = None) -> None:  # noqa: PLR0915
        super().__init__(parent)
        self._host = host
        self._busy = False
        self._settings = getattr(host, "settings", None) or get_settings_manager()
        self._last_seed: int | None = None
        self._layer_event_tokens: list[tuple[str, object]] = []
        self._layer_unsubscribe_name: str | None = None
        self._layer_event_handler = self._on_layer_event
        self._layers_refresh_pending = False
        self.setObjectName("NovelAIPanel")
        self.destroyed.connect(self._on_destroyed)
        self._build_ui(host)
        self._subscribe_layer_events()

    def _add_form_row(self, form: QFormLayout, source: str, field: QWidget) -> None:
        label = QLabel(self.tr(source), self)
        form.addRow(label, field)
        self._form_labels.append((label, source))
        self._row_labels[field] = label

    def _set_row_visible(self, field: QWidget, visible: bool) -> None:
        self._row_labels[field].setVisible(visible)
        field.setVisible(visible)

    def _build_ui(self, host: Any) -> None:  # noqa: PLR0915
        outer = QVBoxLayout(self)
        self._form_labels: list[tuple[QLabel, str]] = []
        self._row_labels: dict[QWidget, QLabel] = {}

        self.mode = QComboBox(self)
        for label, value, _short in _MODES:
            self.mode.addItem(self.tr(label), value)
        self.mode.currentIndexChanged.connect(self._update_mode)
        self.mode.hide()  # Keep the existing stable mode API for saved requests.
        mode_row = QHBoxLayout()
        mode_row.setSpacing(2)
        self.mode_buttons: dict[str, QToolButton] = {}
        for index, (_label, value, short) in enumerate(_MODES):
            button = QToolButton(self)
            button.setCheckable(True)
            button.setAutoExclusive(True)
            button.setText(self.tr(short))
            button.setToolTip(self.tr(_MODES[index][0]))
            button.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            button.clicked.connect(lambda _checked, row=index: self.mode.setCurrentIndex(row))
            self.mode_buttons[value] = button
            mode_row.addWidget(button, 2 if value == "inpaint" else 1)
        outer.addLayout(mode_row)

        self.settings_scroll = QScrollArea(self)
        self.settings_scroll.setWidgetResizable(True)
        body = QWidget(self.settings_scroll)
        layout = QVBoxLayout(body)
        form = QFormLayout()

        self.model = QComboBox(self)
        self.model.setEditable(True)
        for label, value in (
            ("NovelAI Diffusion V5 Full", "nai-diffusion-5-full"),
            ("NovelAI Diffusion V5 Curated", "nai-diffusion-5-curated"),
            ("NovelAI Diffusion V4.5 Full", "nai-diffusion-4-5-full"),
            ("NovelAI Diffusion V4.5 Curated", "nai-diffusion-4-5-curated"),
        ):
            self.model.addItem(label, value)
        self.model.currentIndexChanged.connect(self._update_model_options)
        self.model.lineEdit().textChanged.connect(self._update_model_options)
        self._add_form_row(form, "Model", self.model)

        self.source = QComboBox(self)
        self.source.setObjectName("novelaiSourceLayer")
        self.target = QComboBox(self)
        self.target.setObjectName("novelaiTargetLayer")
        self.target.currentIndexChanged.connect(self._update_batch_target)
        target_row = QWidget(self)
        target_layout = QHBoxLayout(target_row)
        target_layout.setContentsMargins(0, 0, 0, 0)
        target_layout.addWidget(self.target, 1)
        self.refresh_button = QToolButton(self)
        self.refresh_button.setText("↻")
        self.refresh_button.setFixedWidth(28)
        self.refresh_button.setToolTip(self.tr("Refresh layers"))
        self.refresh_button.clicked.connect(self.refresh_layers)
        target_layout.addWidget(self.refresh_button)
        self._add_form_row(form, "Target layer", target_row)
        layout.addLayout(form)

        self.input_group = QGroupBox(self.tr("Input Image"), self)
        input_form = QFormLayout(self.input_group)
        self._add_form_row(input_form, "Input layer", self.source)
        self.strength = QDoubleSpinBox(self)
        self.strength.setRange(0, 1)
        self.strength.setSingleStep(0.05)
        self.strength.setValue(0.7)
        self._add_form_row(input_form, "Change strength (i2i)", self.strength)
        self.noise = QDoubleSpinBox(self)
        self.noise.setRange(0, 1)
        self.noise.setSingleStep(0.05)
        self._add_form_row(input_form, "Added noise", self.noise)
        layout.addWidget(self.input_group)

        self.tool_group = QGroupBox(self.tr("Director Tools"), self)
        tool_form = QFormLayout(self.tool_group)
        self.tool = QComboBox(self)
        for label, value in (
            ("Remove Background", "bg-removal"),
            ("Line Art", "lineart"),
            ("Sketch", "sketch"),
            ("Colorize", "colorize"),
            ("Emotion", "emotion"),
        ):
            self.tool.addItem(self.tr(label), value)
        self._add_form_row(tool_form, "Director Tools", self.tool)
        self.tag_query = QTextEdit(self)
        self.tag_query.setPlaceholderText(self.tr("Tag fragment for suggestions"))
        tool_form.addRow(
            ResizableTextEditArea(self.tag_query, key="ui/novelai/tag_height", default_height=42)
        )
        self.tag_button = QPushButton(self.tr("Suggest tags"), self)
        self.tag_button.clicked.connect(self._request_tags)
        tool_form.addRow(self.tag_button)
        layout.addWidget(self.tool_group)

        self.image_settings = ImageSettingsWidget(self.tr, self)
        self.image_settings.setTitle("")
        self.image_settings.setFlat(True)
        self.resolution = self.image_settings.resolution
        self.width = self.image_settings.width
        self.height = self.image_settings.height
        self.swap_resolution = self.image_settings.swap_resolution
        self.image_section = CollapsibleSection(
            self.tr("Image Settings"),
            "ui/novelai/image_settings_open",
            expanded=True,
            parent=self,
            settings=self._settings,
        )
        self.image_section.content_layout.addWidget(self.image_settings)
        layout.addWidget(self.image_section)

        self.reference_section = CollapsibleSection(
            self.tr("References"),
            "ui/novelai/references_open",
            expanded=False,
            parent=self,
            settings=self._settings,
        )
        reference_form = QFormLayout()
        self.reference = QComboBox(self)
        self.reference.setObjectName("novelaiReferenceLayer")
        self.reference.currentIndexChanged.connect(self._update_reference_summary)
        self._add_form_row(reference_form, "Reference layer", self.reference)
        self.reference_mode = QComboBox(self)
        for label, value in (
            ("None", "none"),
            ("Vibe Transfer", "vibe"),
            ("Character Reference", "character"),
            ("Style Reference", "style"),
            ("Character and Style", "character&style"),
        ):
            self.reference_mode.addItem(self.tr(label), value)
        self.reference_mode.currentIndexChanged.connect(self._update_reference_controls)
        self._add_form_row(reference_form, "Reference mode", self.reference_mode)
        self.reference_strength = QDoubleSpinBox(self)
        self.reference_strength.setRange(0, 1)
        self.reference_strength.setSingleStep(0.05)
        self.reference_strength.setValue(0.5)
        self._add_form_row(reference_form, "Reference strength", self.reference_strength)
        for combo in (
            self.model,
            self.source,
            self.reference,
            self.reference_mode,
            self.target,
            self.tool,
        ):
            combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.reference_section.content_layout.addLayout(reference_form)
        self.vibes = VibeReferencesWidget(self.tr, self)
        self.vibes.changed.connect(self._update_reference_summary)
        self.reference_section.content_layout.addWidget(self.vibes)
        layout.addWidget(self.reference_section)

        self.ai_settings = AISettingsWidget(self.tr, self)
        self.ai_settings.random_seed_button.clicked.connect(self._randomize_seed)
        self.ai_settings.reuse_seed_button.clicked.connect(self._reuse_last_seed)
        self.steps = self.ai_settings.steps
        self.scale = self.ai_settings.scale
        layout.addWidget(self.ai_settings)
        self.ai_settings.collapse_button.toggled.connect(
            lambda expanded: self._save_section_state("ui/novelai/ai_settings_open", expanded)
        )
        self.ai_settings.advanced_button.toggled.connect(
            lambda expanded: self._save_section_state("ui/novelai/ai_advanced_open", expanded)
        )
        self.ai_settings.collapse_button.setChecked(
            self._saved_section_state("ui/novelai/ai_settings_open", True)
        )
        self.ai_settings.advanced_button.setChecked(
            self._saved_section_state("ui/novelai/ai_advanced_open", False)
        )
        self._update_model_options()

        self.negative_label = QLabel(self.tr("Undesired Content"), self)
        layout.addWidget(self.negative_label)
        self.negative_prompt = QTextEdit(self)
        self.negative_prompt.setPlaceholderText(self.tr("Undesired Content"))
        self.negative_area = ResizableTextEditArea(
            self.negative_prompt, key="ui/novelai/negative_prompt_height", default_height=60
        )
        layout.addWidget(self.negative_area)
        self.advanced_label = QLabel(
            self.tr("Advanced API parameters (JSON; overrides controls)"), self
        )
        self.advanced_label.setWordWrap(True)
        layout.addWidget(self.advanced_label)
        self.advanced_parameters = QTextEdit(self)
        self.advanced_parameters.setPlainText("{}")
        self.advanced_area = ResizableTextEditArea(
            self.advanced_parameters, key="ui/novelai/advanced_height", default_height=60
        )
        layout.addWidget(self.advanced_area)
        self.variation_source = QComboBox(self)
        self.variation_source.setObjectName("novelaiVariationSource")
        self.variation_source.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        layout.addWidget(self.variation_source)
        self.variation_button = QPushButton(self.tr("Create variation from Gallery image"), self)
        self.variation_button.clicked.connect(self._emit_variation_request)
        layout.addWidget(self.variation_button)
        layout.addStretch()
        self.settings_scroll.setWidget(body)
        outer.addWidget(self.settings_scroll, 1)

        footer = QWidget(self)
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(0, 0, 0, 0)
        footer_layout.setSpacing(3)
        self.live_label = QLabel(self.tr("Live prompt"), self)
        footer_layout.addWidget(self.live_label)
        self.composer = PromptComposerWidget(host_api=host, parent=self, emphasis_style="novelai")
        self.composer.modeChanged.connect(self._update_empty_composer_height)
        self.composer._flow_area.height_limit = lambda: max(80, self.size().height() // 4)
        footer_layout.addWidget(self.composer)
        self.prompt = QTextEdit(self)
        self.prompt.setPlaceholderText(self.tr("Enter prompt text..."))
        self.prompt_area = ResizableTextEditArea(
            self.prompt, key="ui/novelai/prompt_height", default_height=78
        )
        self.prompt_area.height_limit = lambda: max(80, int(self.size().height() * 0.35))
        self.prompt_area.set_area_height(self.prompt_area.height())
        footer_layout.addWidget(self.prompt_area)

        self.generate_button = QPushButton(self.tr("Generate"), self)
        self.generate_button.clicked.connect(lambda: self._emit_request())
        footer_layout.addWidget(self.generate_button)
        self.status = QLabel("", self)
        self.status.setWordWrap(True)
        footer_layout.addWidget(self.status)
        outer.addWidget(footer)
        self.refresh_layers()
        self.refresh_variations()
        self._update_mode()
        self._update_empty_composer_height(self.composer.is_composer_active())

    def refresh_layers(self) -> None:
        old_source = self.source.currentData()
        old_target = self.target.currentData()
        self.source.clear()
        self.source.addItem(self.tr("Nothing"), None)
        old_reference = self.reference.currentData()
        self.reference.clear()
        self.reference.addItem(self.tr("Nothing"), None)
        self.target.clear()
        self.target.addItem(self.tr("<New Layer>"), None)
        canvas = getattr(self._host, "canvas", None)
        if canvas is not None:
            for layer in canvas.get_layers():
                if getattr(layer, "id", None):
                    label = str(getattr(layer, "name", None) or layer.id)
                    self.source.addItem(label, layer.id)
                    self.reference.addItem(label, layer.id)
                    self.target.addItem(label, layer.id)
        self.source.setCurrentIndex(max(0, self.source.findData(old_source)))
        self.reference.setCurrentIndex(max(0, self.reference.findData(old_reference)))
        self.target.setCurrentIndex(max(0, self.target.findData(old_target)))
        self.vibes.set_layers(
            [
                (self.source.itemText(index), str(self.source.itemData(index)))
                for index in range(1, self.source.count())
            ]
        )
        self._update_batch_target()

    def refresh_variations(self) -> None:
        selected = self.variation_source.currentData()
        self.variation_source.clear()
        store = getattr(self._host, "get_service", lambda _name: None)("generation_history")
        if store is not None:
            for record in store.list_records(service_id="novelai", status="completed"):
                if record.image_path and record.prompt and record.model_name:
                    label = f"{record.prompt[:45]} ({record.job_id[-8:]})"
                    self.variation_source.addItem(label, record.job_id)
        self.variation_source.setCurrentIndex(max(0, self.variation_source.findData(selected)))
        self.variation_button.setEnabled(not self._busy and self.variation_source.count() > 0)

    def _update_batch_target(self) -> None:
        if not hasattr(self, "image_settings"):
            return
        enabled = self.target.currentData() is None and self.mode.currentData() in {
            "generate",
            "img2img",
            "inpaint",
        }
        if not enabled:
            self.image_settings.set_count(1)
        for button in self.image_settings.count_controls.values():
            button.setEnabled(enabled)

    def _update_mode(self) -> None:
        mode = self.mode.currentData()
        generates = mode in {"generate", "img2img", "inpaint"}
        self.mode_buttons[mode].setChecked(True)
        self._set_row_visible(self.model, mode != "augment")
        self.input_group.setVisible(mode != "generate")
        self.source.setEnabled(mode != "generate")
        self._set_row_visible(self.strength, mode in {"img2img", "inpaint"})
        self._set_row_visible(self.noise, mode in {"img2img", "inpaint"})
        self.strength.setEnabled(mode in {"img2img", "inpaint"})
        self.noise.setEnabled(mode in {"img2img", "inpaint"})
        self.tool_group.setVisible(mode == "augment")
        self.tool.setEnabled(mode == "augment")
        self.model.setEnabled(mode != "augment")
        self.image_section.setVisible(generates)
        self.image_settings.setEnabled(generates)
        self.ai_settings.setEnabled(generates)
        self.ai_settings.setVisible(generates)
        self.negative_area.setVisible(generates)
        self.negative_label.setEnabled(generates)
        self.negative_label.setVisible(generates)
        self.negative_prompt.setEnabled(generates)
        self.advanced_area.setVisible(generates)
        self.advanced_label.setVisible(generates)
        self.advanced_label.setEnabled(generates)
        self.advanced_parameters.setEnabled(generates)
        self._update_reference_controls()
        self.prompt.setEnabled(mode != "upscale")
        self.prompt_area.setVisible(mode != "upscale")
        self.composer.setEnabled(mode != "upscale")
        self.composer.setVisible(mode != "upscale")
        self.live_label.setVisible(mode != "upscale")
        self._update_batch_target()

    def _selected_model(self) -> str:
        index = self.model.currentIndex()
        text = self.model.currentText().strip()
        if index >= 0 and text == self.model.itemText(index):
            return str(self.model.itemData(index))
        return text

    def _update_reference_controls(self) -> None:
        if not hasattr(self, "reference_strength"):
            return
        model = self._selected_model()
        generates = self.mode.currentData() in {"generate", "img2img", "inpaint"}
        available = generates and model in _REFERENCE_AND_VARIETY_MODELS
        self.reference_section.setVisible(available)
        self.reference_mode.setEnabled(available)
        enabled = generates and self._reference_mode_available()
        self.reference.setEnabled(enabled)
        self.reference_strength.setEnabled(enabled)
        self.vibes.setEnabled(enabled and self.reference_mode.currentData() == "vibe")
        self._set_row_visible(self.reference, enabled)
        self._set_row_visible(self.reference_strength, enabled)
        self.vibes.setVisible(enabled and self.reference_mode.currentData() == "vibe")
        self._update_reference_summary()

    def _reference_mode_available(self) -> bool:
        model = self._selected_model()
        mode = self.reference_mode.currentData()
        return mode != "none" and model in _REFERENCE_AND_VARIETY_MODELS

    def _update_model_options(self) -> None:
        if not hasattr(self, "ai_settings"):
            return
        model = self._selected_model()
        self.ai_settings.set_variety_available(model in _REFERENCE_AND_VARIETY_MODELS)
        self._update_reference_controls()

    def _saved_section_state(self, key: str, default: bool) -> bool:
        try:
            saved = self._settings.get(key, default)
        except Exception:
            logger.warning("Could not read NovelAI section state", exc_info=True)
            return default
        return saved if type(saved) is bool else default

    def _save_section_state(self, key: str, expanded: bool) -> None:
        try:
            self._settings.set(key, expanded)
        except Exception:
            logger.warning("Could not save NovelAI section state", exc_info=True)

    def _update_reference_summary(self) -> None:
        count = 0
        if self._reference_mode_available():
            count = int(self.reference.currentData() is not None)
            if self.reference_mode.currentData() == "vibe":
                count += len(self.vibes.values())
        self.reference_section.set_title(
            f"{self.tr('References')} ({self.tr('{count} items').format(count=count)})"
        )

    def _update_empty_composer_height(self, active: bool) -> None:
        area = self.composer._flow_area
        if not active:
            area.setFixedHeight(42)
            return
        try:
            saved = area.settings.get(area.key, 170)
        except Exception:
            saved = 170
        area.set_area_height(saved if type(saved) is int else 170)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if not hasattr(self, "prompt_area"):
            return
        try:
            saved = self.prompt_area.settings.get(self.prompt_area.key, 78)
        except Exception:
            saved = 78
        self.prompt_area.set_area_height(saved if type(saved) is int else 78)
        if self.composer.is_composer_active():
            self._update_empty_composer_height(True)

    def _randomize_seed(self) -> None:
        self.ai_settings.seed.setText(str(secrets.randbelow(4_294_967_296)))

    def _reuse_last_seed(self) -> None:
        if self._last_seed is not None:
            self.ai_settings.seed.setText(str(self._last_seed))

    def _subscribe_layer_events(self) -> None:
        events = getattr(self._host, "events", None)
        if events is None:
            return
        subscribe = getattr(events, "on", None)
        self._layer_unsubscribe_name = "off"
        if not callable(subscribe):
            subscribe = getattr(events, "subscribe", None)
            self._layer_unsubscribe_name = "unsubscribe"
        if not callable(subscribe):
            return
        for event_name in (Events.LAYER_ADDED, Events.LAYER_REMOVED, Events.LAYER_CHANGED):
            try:
                token = subscribe(event_name, self._layer_event_handler)
            except Exception:
                logger.warning("NovelAI layer subscription failed", exc_info=True)
            else:
                self._layer_event_tokens.append((event_name, token))

    def _on_layer_event(self, _payload: Any) -> None:
        if self._layers_refresh_pending:
            return
        self._layers_refresh_pending = True
        if QThread.currentThread() == self.thread():
            QTimer.singleShot(0, self._perform_layers_refresh)
        else:
            QMetaObject.invokeMethod(
                self, "_perform_layers_refresh", Qt.ConnectionType.QueuedConnection
            )

    @Slot()
    def _perform_layers_refresh(self) -> None:
        if not is_valid(self):
            return
        self._layers_refresh_pending = False
        self.refresh_layers()

    def _on_destroyed(self, _obj=None) -> None:
        events = getattr(self._host, "events", None)
        off = getattr(events, self._layer_unsubscribe_name or "off", None)
        if callable(off):
            for event_name, token in self._layer_event_tokens:
                try:
                    off(event_name, token)
                except Exception:
                    logger.warning("NovelAI layer unsubscribe failed", exc_info=True)
        self._layer_event_tokens.clear()

    def _emit_request(self, variation_job_id: str | None = None) -> None:
        if not is_external_services_allowed(self._host):
            self.show_status(self.tr("External AI services are disabled"))
            return
        self.refresh_layers()
        seed = None
        if self.mode.currentData() in {"generate", "img2img", "inpaint"}:
            try:
                seed = self.ai_settings.seed_value()
            except ValueError as exc:
                self.show_status(str(exc))
                return
        prompt = (
            self.composer.resolve_for_generation()
            if self.composer.is_composer_active()
            else self.prompt.toPlainText()
        ).strip()
        request = {
            "kind": self.mode.currentData(),
            "model": self._selected_model(),
            "source_layer_id": self.source.currentData(),
            "reference_layer_id": self.reference.currentData(),
            "reference_mode": (
                self.reference_mode.currentData()
                if self.reference_mode.isEnabled() and self._reference_mode_available()
                else "none"
            ),
            "reference_strength": self.reference_strength.value(),
            "vibes": self.vibes.values() if self.vibes.isEnabled() else [],
            "target_layer_id": self.target.currentData(),
            "prompt": prompt,
            "negative_prompt": self.negative_prompt.toPlainText(),
            "width": self.width.value(),
            "height": self.height.value(),
            "n_samples": self.image_settings.count(),
            "steps": self.steps.value(),
            "scale": self.scale.value(),
            "seed": seed,
            "sampler": self.ai_settings.sampler.currentData(),
            "cfg_rescale": self.ai_settings.cfg_rescale.value(),
            "noise_schedule": self.ai_settings.noise_schedule.currentData(),
            "variety": (
                self.ai_settings.variety.isEnabled() and self.ai_settings.variety.isChecked()
            ),
            "strength": self.strength.value(),
            "noise": self.noise.value(),
            "tool": self.tool.currentData(),
            "advanced_parameters": self.advanced_parameters.toPlainText(),
        }
        if variation_job_id is not None:
            request.update(
                kind="img2img",
                variation_job_id=variation_job_id,
                source_layer_id=None,
                target_layer_id=None,
                seed=None,
                reference_mode="none",
                vibes=[],
                advanced_parameters="{}",
            )
        elif seed is not None:
            self._last_seed = seed
            self.ai_settings.reuse_seed_button.setEnabled(True)
        self.requested.emit(request)

    def _emit_variation_request(self) -> None:
        job_id = self.variation_source.currentData()
        if job_id is not None:
            self._emit_request(str(job_id))

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self.refresh_layers()
        self.refresh_variations()

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "_form_labels"):
            self._retranslate()

    def _retranslate(self) -> None:
        for label, source in self._form_labels:
            label.setText(self.tr(source))
        for label, value, short in _MODES:
            self.mode_buttons[value].setText(self.tr(short))
            self.mode_buttons[value].setToolTip(self.tr(label))
        for combo, sources in (
            (
                self.mode,
                (
                    "Image Generation",
                    "Image2Image",
                    "Inpaint (current selection)",
                    "Director Tools",
                    "Upscale",
                ),
            ),
            (
                self.reference_mode,
                (
                    "None",
                    "Vibe Transfer",
                    "Character Reference",
                    "Style Reference",
                    "Character and Style",
                ),
            ),
            (self.tool, ("Remove Background", "Line Art", "Sketch", "Colorize", "Emotion")),
        ):
            for index, source in enumerate(sources):
                combo.setItemText(index, self.tr(source))
        self.refresh_button.setToolTip(self.tr("Refresh layers"))
        self.input_group.setTitle(self.tr("Input Image"))
        self.tool_group.setTitle(self.tr("Director Tools"))
        self.image_section.set_title(self.tr("Image Settings"))
        self._update_reference_summary()
        self.image_settings.retranslate()
        self.image_settings.setTitle("")
        self.ai_settings.retranslate()
        self.live_label.setText(self.tr("Live prompt"))
        self.generate_button.setText(self.tr("Generate"))
        self.variation_button.setText(self.tr("Create variation from Gallery image"))
        self.vibes.retranslate()
        self.tag_button.setText(self.tr("Suggest tags"))
        self.prompt.setPlaceholderText(self.tr("Enter prompt text..."))
        self.negative_label.setText(self.tr("Undesired Content"))
        self.negative_prompt.setPlaceholderText(self.tr("Undesired Content"))
        self.advanced_label.setText(self.tr("Advanced API parameters (JSON; overrides controls)"))
        self.tag_query.setPlaceholderText(self.tr("Tag fragment for suggestions"))
        self.refresh_layers()
        self.refresh_variations()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self.generate_button.setEnabled(not busy)
        self.variation_button.setEnabled(not busy and self.variation_source.count() > 0)

    def show_status(self, message: str) -> None:
        self.status.setText(message)

    def _request_tags(self) -> None:
        model = self._selected_model()
        self.tagsRequested.emit(self.tag_query.toPlainText(), str(model))
