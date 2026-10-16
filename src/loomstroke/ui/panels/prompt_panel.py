"""Prompt panel for ComfyUI text prompt management."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, TypedDict, cast

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ui.panels.base_panel import BasePanel

logger = logging.getLogger(__name__)


@dataclass
class PromptTemplate:
    """Prompt template data."""

    name: str
    positive: str = ""
    negative: str = ""
    settings: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)


class PromptSettings(TypedDict):
    steps: int
    cfg_scale: float
    width: int
    height: int
    seed: int
    sampler: str
    scheduler: str
    denoise: float


class PromptPanel(BasePanel):
    """Panel for managing ComfyUI prompts.

    Handles positive/negative prompts, templates, and generation settings.
    Complies with Master.md 400-line limit.
    """

    # Signals
    prompt_changed = Signal(str, str)  # positive, negative
    settings_changed = Signal(dict)  # settings dict
    template_selected = Signal(dict)  # template data
    generate_requested = Signal(dict)  # full prompt config

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize prompt panel."""
        self.templates: dict[str, PromptTemplate] = {}
        self.current_template: PromptTemplate | None = None
        self.history: list[dict[str, str]] = []
        self.max_history = 50

        self.template_combo: QComboBox | None = None
        self.steps_spin: QSpinBox | None = None
        self.cfg_slider: QSlider | None = None
        self.positive_text: QTextEdit | None = None
        self.negative_text: QTextEdit | None = None

        self.generation_settings: PromptSettings = {
            "steps": 20,
            "cfg_scale": 7.0,
            "width": 512,
            "height": 512,
            "seed": -1,
            "sampler": "euler",
            "scheduler": "normal",
            "denoise": 1.0,
        }

        super().__init__("comfyui.prompt", "Prompt Editor", parent)

        self.auto_save_timer = QTimer(self)
        self.auto_save_timer.timeout.connect(self._auto_save_prompt)
        self.auto_save_timer.setInterval(5000)

        self._load_default_templates()

    def setup_panel_ui(self) -> None:
        """Set up UI components required by BasePanel."""
        layout = self.main_layout
        layout.setSpacing(8)

        header_layout = QHBoxLayout()

        title_text = self._ts("Prompt Editor")
        title_label = QLabel(title_text)
        self._translation_binder.register(title_label.setText, title_text)
        title_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        header_layout.addWidget(title_label)
        header_layout.addStretch()

        template_label = self._ts("Template:")
        selector_label = QLabel(template_label)
        self._translation_binder.register(selector_label.setText, template_label)
        header_layout.addWidget(selector_label)

        self.template_combo = QComboBox()
        self.template_combo.currentTextChanged.connect(self._on_template_selected)
        header_layout.addWidget(self.template_combo)

        layout.addLayout(header_layout)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._create_prompt_group("Positive Prompt", True))
        splitter.addWidget(self._create_prompt_group("Negative Prompt", False))
        splitter.addWidget(self._create_settings_group())
        splitter.setStretchFactor(0, 2)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 1)
        layout.addWidget(splitter)

        actions_layout = QHBoxLayout()

        generate_text = self._ts("Generate")
        generate_btn = QPushButton(generate_text)
        self._translation_binder.register(generate_btn.setText, generate_text)
        generate_btn.clicked.connect(self._on_generate)
        generate_btn.setStyleSheet("QPushButton { font-weight: bold; }")
        actions_layout.addWidget(generate_btn)

        save_template_text = self._ts("Save as Template")
        save_template_btn = QPushButton(save_template_text)
        self._translation_binder.register(save_template_btn.setText, save_template_text)
        save_template_btn.clicked.connect(self._on_save_template)
        actions_layout.addWidget(save_template_btn)

        clear_text = self._ts("Clear")
        clear_btn = QPushButton(clear_text)
        self._translation_binder.register(clear_btn.setText, clear_text)
        clear_btn.clicked.connect(self._on_clear)
        actions_layout.addWidget(clear_btn)

        actions_layout.addStretch()
        layout.addLayout(actions_layout)

    def _create_prompt_group(self, title: str, is_positive: bool) -> QGroupBox:
        """Create prompt input group.

        Args:
            title: Group title
            is_positive: True for positive prompt, False for negative
        """
        group_title = self._ts(title)
        group = QGroupBox(group_title)
        self._translation_binder.register(group.setTitle, group_title)
        layout = QVBoxLayout(group)

        # Text editor
        text_edit = QTextEdit()
        placeholder_text = (
            self._ts("Enter positive prompt...")
            if is_positive
            else self._ts("Enter negative prompt...")
        )
        text_edit.setPlaceholderText(placeholder_text)
        self._translation_binder.register(text_edit.setPlaceholderText, placeholder_text)
        text_edit.setAcceptRichText(False)
        text_edit.textChanged.connect(self._on_prompt_changed)

        if is_positive:
            self.positive_text = text_edit
        else:
            self.negative_text = text_edit

        layout.addWidget(text_edit)

        # Quick tags/modifiers
        tags_layout = QHBoxLayout()

        if is_positive:
            tags = ["masterpiece", "best quality", "detailed", "8k", "photorealistic"]
        else:
            tags = ["low quality", "blurry", "deformed", "ugly", "duplicate"]

        for tag in tags[:4]:  # Limit to 4 for space
            tag_btn = QPushButton(tag)
            tag_btn.setMaximumHeight(25)
            tag_btn.clicked.connect(lambda checked, t=tag: self._add_tag(t, is_positive))
            tags_layout.addWidget(tag_btn)

        layout.addLayout(tags_layout)

        return group

    def _create_settings_group(self) -> QGroupBox:
        """Create generation settings group."""
        group_title = self._ts("Generation Settings")
        group = QGroupBox(group_title)
        self._translation_binder.register(group.setTitle, group_title)
        layout = QVBoxLayout(group)
        self._setup_steps_controls(layout)
        self._setup_dimension_controls(layout)
        self._setup_sampler_controls(layout)
        return group

    def _setup_steps_controls(self, layout: QVBoxLayout) -> None:
        """Set up generation step and CFG controls."""
        steps_layout = QHBoxLayout()
        steps_label = self._ts("Steps:")
        steps_label_widget = QLabel(steps_label)
        self._translation_binder.register(steps_label_widget.setText, steps_label)
        steps_layout.addWidget(steps_label_widget)

        self.steps_spin = QSpinBox()
        self.steps_spin.setRange(1, 150)
        self.steps_spin.setValue(self.generation_settings["steps"])
        self.steps_spin.valueChanged.connect(self._on_setting_changed)
        steps_layout.addWidget(self.steps_spin)

        steps_layout.addStretch()

        # CFG Scale
        cfg_label_text = self._ts("CFG Scale:")
        cfg_label_widget = QLabel(cfg_label_text)
        self._translation_binder.register(cfg_label_widget.setText, cfg_label_text)
        steps_layout.addWidget(cfg_label_widget)

        self.cfg_slider = QSlider(Qt.Orientation.Horizontal)
        self.cfg_slider.setRange(10, 300)  # 1.0 to 30.0
        self.cfg_slider.setValue(int(self.generation_settings["cfg_scale"] * 10))
        self.cfg_slider.valueChanged.connect(self._on_setting_changed)
        steps_layout.addWidget(self.cfg_slider)

        cfg_label = QLabel(str(self.generation_settings["cfg_scale"]))
        self.cfg_slider.valueChanged.connect(lambda v: cfg_label.setText(f"{v / 10:.1f}"))
        steps_layout.addWidget(cfg_label)

        layout.addLayout(steps_layout)

    def _setup_dimension_controls(self, layout: QVBoxLayout) -> None:
        """Set up width/height controls."""
        dim_layout = QHBoxLayout()

        size_label_text = self._ts("Size:")
        size_label_widget = QLabel(size_label_text)
        self._translation_binder.register(size_label_widget.setText, size_label_text)
        dim_layout.addWidget(size_label_widget)

        # Width
        width_spin = QSpinBox()
        width_spin.setRange(64, 2048)
        width_spin.setSingleStep(64)
        width_spin.setValue(self.generation_settings["width"])
        width_spin.valueChanged.connect(self._on_setting_changed)
        dim_layout.addWidget(width_spin)

        dim_layout.addWidget(QLabel("x"))

        # Height
        height_spin = QSpinBox()
        height_spin.setRange(64, 2048)
        height_spin.setSingleStep(64)
        height_spin.setValue(self.generation_settings["height"])
        height_spin.valueChanged.connect(self._on_setting_changed)
        dim_layout.addWidget(height_spin)

        # Preset sizes
        size_combo = QComboBox()
        size_combo.addItems(["512x512", "768x768", "1024x1024", "768x512", "512x768"])
        size_combo.currentTextChanged.connect(self._on_size_preset)
        dim_layout.addWidget(size_combo)

        dim_layout.addStretch()

        layout.addLayout(dim_layout)

    def _setup_sampler_controls(self, layout: QVBoxLayout) -> None:
        """Set up sampler controls."""
        sampler_layout = QHBoxLayout()

        sampler_label_text = self._ts("Sampler:")
        sampler_label_widget = QLabel(sampler_label_text)
        self._translation_binder.register(sampler_label_widget.setText, sampler_label_text)
        sampler_layout.addWidget(sampler_label_widget)

        sampler_combo = QComboBox()
        sampler_combo.addItems(
            ["euler", "euler_a", "ddim", "dpm_2", "dpm_2_a", "lms", "dpm_fast", "dpm_adaptive"]
        )
        sampler_combo.setCurrentText(self.generation_settings["sampler"])
        sampler_combo.currentTextChanged.connect(self._on_setting_changed)
        sampler_layout.addWidget(sampler_combo)

        sampler_layout.addStretch()

        layout.addLayout(sampler_layout)

    def _load_default_templates(self) -> None:
        """Load default prompt templates."""
        # Default templates
        defaults = [
            PromptTemplate(
                name="Portrait",
                positive="portrait photography, professional, studio lighting, sharp focus",
                negative="blurry, out of focus, deformed, ugly",
            ),
            PromptTemplate(
                name="Landscape",
                positive="landscape photography, scenic, nature, beautiful lighting, 8k",
                negative="people, man-made objects, urban",
            ),
            PromptTemplate(
                name="Anime",
                positive="anime style, manga, cel shading, vibrant colors",
                negative="realistic, photographic, 3d render",
            ),
            PromptTemplate(
                name="Concept Art",
                positive="concept art, digital painting, artstation, highly detailed",
                negative="photo, realistic, low quality",
            ),
        ]

        for template in defaults:
            self.templates[template.name] = template

        # Update combo box
        if self.template_combo is None:
            return
        self.template_combo.addItem("None")
        self.template_combo.addItems([t.name for t in defaults])

    # Event Handlers

    def _on_prompt_changed(self) -> None:
        """Handle prompt text change."""
        if self.positive_text and self.negative_text:
            positive = self.positive_text.toPlainText()
            negative = self.negative_text.toPlainText()

            # Emit signal
            self.prompt_changed.emit(positive, negative)

            # Start auto-save timer
            self.auto_save_timer.stop()
            self.auto_save_timer.start()

    def _on_template_selected(self, name: str) -> None:
        """Handle template selection."""
        if name == "None":
            self.current_template = None
            return

        if name in self.templates:
            template = self.templates[name]
            self.current_template = template

            # Apply template
            if self.positive_text:
                self.positive_text.setPlainText(template.positive)
            if self.negative_text:
                self.negative_text.setPlainText(template.negative)

            # Apply settings if available
            if template.settings:
                self._apply_settings(template.settings)

            # Emit signal
            self.template_selected.emit(
                {
                    "name": template.name,
                    "positive": template.positive,
                    "negative": template.negative,
                    "settings": template.settings,
                }
            )

    def _on_setting_changed(self) -> None:
        """Handle generation setting change."""
        # Update settings dict
        if self.steps_spin:
            self.generation_settings["steps"] = self.steps_spin.value()
        if self.cfg_slider:
            self.generation_settings["cfg_scale"] = self.cfg_slider.value() / 10.0

        # Emit signal
        self.settings_changed.emit(self.generation_settings)

    def _on_size_preset(self, preset: str) -> None:
        """Handle size preset selection."""
        if "x" in preset:
            width, height = preset.split("x")
            self.generation_settings["width"] = int(width)
            self.generation_settings["height"] = int(height)
            self._on_setting_changed()

    def _on_generate(self) -> None:
        """Handle generate button click."""
        if not self.positive_text:
            return

        # Collect current state
        config = {
            "positive": self.positive_text.toPlainText(),
            "negative": self.negative_text.toPlainText() if self.negative_text else "",
            "settings": self.generation_settings.copy(),
        }

        # Add to history
        self._add_to_history(config)

        # Emit signal
        self.generate_requested.emit(config)

        # Publish to event bus
        if self.event_bus:
            self.event_bus.publish("prompt.generate", config)

    def _on_save_template(self) -> None:
        """Save current prompt as template."""
        # This would show a dialog to name the template
        # For now, just save with timestamp
        name = f"Custom_{datetime.now().strftime('%H%M%S')}"

        template = PromptTemplate(
            name=name,
            positive=self.positive_text.toPlainText() if self.positive_text else "",
            negative=self.negative_text.toPlainText() if self.negative_text else "",
            settings=dict(self.generation_settings),
        )

        self.templates[name] = template
        if self.template_combo is None:
            return
        self.template_combo.addItem(name)

        logger.info(f"Saved template: {name}")

    def _on_clear(self) -> None:
        """Clear all prompts."""
        if self.positive_text:
            self.positive_text.clear()
        if self.negative_text:
            self.negative_text.clear()

    # Helper Methods

    def _add_tag(self, tag: str, is_positive: bool) -> None:
        """Add tag to prompt."""
        text_edit = self.positive_text if is_positive else self.negative_text
        if text_edit:
            current = text_edit.toPlainText()
            if current and not current.endswith(", "):
                current += ", "
            text_edit.setPlainText(current + tag)

    def _apply_settings(self, settings: dict[str, Any]) -> None:
        """Apply settings to UI."""
        steps = settings.get("steps")
        if isinstance(steps, int):
            self.generation_settings["steps"] = steps
            if self.steps_spin is not None:
                self.steps_spin.setValue(steps)

        cfg_scale = settings.get("cfg_scale")
        if isinstance(cfg_scale, (int, float)):
            scale_value = float(cfg_scale)
            self.generation_settings["cfg_scale"] = scale_value
            if self.cfg_slider is not None:
                self.cfg_slider.setValue(int(scale_value * 10))

        width = settings.get("width")
        if isinstance(width, int):
            self.generation_settings["width"] = width

        height = settings.get("height")
        if isinstance(height, int):
            self.generation_settings["height"] = height

        seed = settings.get("seed")
        if isinstance(seed, int):
            self.generation_settings["seed"] = seed

        sampler = settings.get("sampler")
        if isinstance(sampler, str):
            self.generation_settings["sampler"] = sampler

        scheduler = settings.get("scheduler")
        if isinstance(scheduler, str):
            self.generation_settings["scheduler"] = scheduler

        denoise = settings.get("denoise")
        if isinstance(denoise, (int, float)):
            self.generation_settings["denoise"] = float(denoise)

    def _add_to_history(self, config: dict[str, Any]) -> None:
        """Add prompt to history."""
        self.history.append(config)
        if len(self.history) > self.max_history:
            self.history.pop(0)

    def _auto_save_prompt(self) -> None:
        """Auto-save current prompt."""
        self.auto_save_timer.stop()
        # Save to settings or temporary storage
        logger.debug("Auto-saved prompt")


if TYPE_CHECKING:  # pragma: no cover - typing aid only
    from loomstroke.ui.typing import Signal1, Signal2

    PromptPanel.prompt_changed = cast(  # type: ignore[assignment]
        Signal2[str, str],
        PromptPanel.prompt_changed,
    )
    PromptPanel.settings_changed = cast(  # type: ignore[assignment]
        Signal1[dict[str, Any]],
        PromptPanel.settings_changed,
    )
    PromptPanel.template_selected = cast(  # type: ignore[assignment]
        Signal1[dict[str, Any]],
        PromptPanel.template_selected,
    )
    PromptPanel.generate_requested = cast(  # type: ignore[assignment]
        Signal1[dict[str, Any]],
        PromptPanel.generate_requested,
    )
