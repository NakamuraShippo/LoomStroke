"""Selector helpers for ComfyUI node parameter widgets."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QLineEdit, QTextEdit, QVBoxLayout, QWidget

from loomstroke.plugins.common.snippet_models import Snippet as TextSnippet
from loomstroke.plugins.common.snippet_repository import SnippetRepository
from loomstroke.ui.translations import translate_with_fallback

from .prompt_composer import PromptComposerWidget

logger = logging.getLogger(__name__)

SAMPLER_CHOICES: tuple[str, ...] = (
    "euler",
    "euler_ancestral",
    "heun",
    "heunpp2",
    "dpm_2",
    "dpm_2_ancestral",
    "lms",
    "dpm_fast",
    "dpm_adaptive",
    "dpmpp_2s_ancestral",
    "dpmpp_sde",
    "dpmpp_sde_gpu",
    "dpmpp_2m",
    "dpmpp_2m_sde",
    "dpmpp_2m_sde_gpu",
    "dpmpp_3m_sde",
    "dpmpp_3m_sde_gpu",
    "ddim",
    "uni_pc",
    "uni_pc_bh2",
)
SCHEDULER_CHOICES: tuple[str, ...] = (
    "normal",
    "karras",
    "exponential",
    "sgm_uniform",
    "simple",
    "ddim_uniform",
)
CONTROL_AFTER_GENERATE_CHOICES: tuple[str, ...] = (
    "fixed",
    "increment",
    "decrement",
    "randomize",
)
UPSCALE_METHOD_CHOICES: tuple[str, ...] = (
    "nearest-exact",
    "bilinear",
    "area",
    "bicubic",
    "lanczos",
    "bislerp",
)
ENABLE_DISABLE_CHOICES: tuple[str, ...] = ("enable", "disable")


def _node_tabs_tr(text: str) -> str:
    return translate_with_fallback("NodeTabsWidget", text)


class PromptSnippetComboBox(QComboBox):
    """Combo box that refreshes prompt snippets before opening."""

    def __init__(self, refresh_callback, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._refresh_callback = refresh_callback

    def showPopup(self) -> None:
        if callable(self._refresh_callback):
            try:
                self._refresh_callback()
            except Exception:
                logger.debug("Prompt snippet refresh failed", exc_info=True)
        super().showPopup()


class NodePromptSelectorMixin:
    """Own prompt snippet and LivePrompt composer selector behavior."""

    host_api: Any
    _prompt_selector_combos: dict[str, QComboBox]
    _prompt_text_widgets: dict[str, QWidget]
    _updating_prompt_selection: bool

    def _refresh_prompt_selector_options(self) -> None:
        for param_name, combo in list(self._prompt_selector_combos.items()):
            self._populate_prompt_combo(param_name, combo)

    def _fetch_prompt_snippets(self) -> list[TextSnippet]:
        if not self.host_api:
            return []
        settings = getattr(self.host_api, "settings", None)
        try:
            repository = SnippetRepository(settings, db_path=None)
            snippets = repository.list_snippets(kinds=["prompt"])
        except Exception:
            logger.debug("Failed to load prompt snippets for ComfyUI selector", exc_info=True)
            return []
        snippets.sort(key=lambda snippet: (snippet.title or snippet.id).lower())
        return snippets

    @staticmethod
    def _extract_prompt_combo_payload(data: Any) -> tuple[Any, Any] | None:
        if isinstance(data, (list, tuple)) and len(data) >= 2:
            return data[0], data[1]
        if isinstance(data, dict):
            return data.get("id"), data.get("content")
        return None

    def _populate_prompt_combo(self, param_name: str, combo: QComboBox) -> None:
        del param_name
        snippets = self._fetch_prompt_snippets()
        current_data = self._extract_prompt_combo_payload(combo.currentData())
        current_id = current_data[0] if current_data else None
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(_node_tabs_tr("Select prompt snippet..."), None)
        for snippet in snippets:
            display_name = snippet.title or snippet.id
            combo.addItem(display_name, (snippet.id, snippet.content))
        if current_id:
            for index in range(combo.count()):
                data = combo.itemData(index)
                if isinstance(data, tuple) and data[0] == current_id:
                    combo.setCurrentIndex(index)
                    break
            else:
                combo.setCurrentIndex(0)
        else:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _reset_prompt_combo(self, param_name: str) -> None:
        combo = self._prompt_selector_combos.get(param_name)
        if combo and combo.currentIndex() != 0:
            combo.blockSignals(True)
            combo.setCurrentIndex(0)
            combo.blockSignals(False)

    def _wrap_with_prompt_selector(
        self, param_name: str, text_widget: QWidget, *, multiline: bool
    ) -> QWidget:
        del multiline

        def refresh() -> None:
            self._populate_prompt_combo(param_name, combo)

        combo = PromptSnippetComboBox(refresh_callback=refresh)
        combo.setEditable(False)
        self._prompt_selector_combos[param_name] = combo
        self._prompt_text_widgets[param_name] = text_widget
        self._populate_prompt_combo(param_name, combo)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        layout.addWidget(combo)
        layout.addWidget(text_widget)
        container._prompt_text_widget = text_widget

        def on_combo_selected(index: int) -> None:
            payload = self._extract_prompt_combo_payload(combo.itemData(index))
            if not payload:
                return
            _, snippet_content = payload
            if snippet_content is None:
                return
            self._updating_prompt_selection = True
            try:
                if isinstance(text_widget, QTextEdit):
                    prev = text_widget.blockSignals(True)
                    text_widget.setPlainText(snippet_content)
                    text_widget.blockSignals(prev)
                    value = text_widget.toPlainText()
                elif isinstance(text_widget, QLineEdit):
                    prev = text_widget.blockSignals(True)
                    text_widget.setText(snippet_content)
                    text_widget.blockSignals(prev)
                    value = text_widget.text()
                else:
                    value = snippet_content
                self.on_param_changed(param_name, value)
            finally:
                self._updating_prompt_selection = False

        combo.currentIndexChanged.connect(on_combo_selected)
        return container

    def _wrap_with_prompt_composer(self, param_name: str, text_widget: QWidget) -> QWidget:
        """Wrap a multiline text widget with the chip-based prompt composer."""

        composer = PromptComposerWidget(host_api=self.host_api)
        composer.setProperty("prompt_node_id", self.node_id)
        composer.setProperty("prompt_param_name", param_name)
        self._prompt_text_widgets[param_name] = text_widget

        def on_composed(text: str) -> None:
            self._updating_prompt_selection = True
            try:
                if isinstance(text_widget, QTextEdit):
                    text_widget.blockSignals(True)
                    text_widget.setPlainText(text)
                    text_widget.blockSignals(False)
                else:
                    text_widget.blockSignals(True)
                    text_widget.setText(text)
                    text_widget.blockSignals(False)
                self.on_param_changed(param_name, text)
            finally:
                self._updating_prompt_selection = False

        def on_mode_changed(composer_active: bool) -> None:
            text_widget.setVisible(not composer_active)

        composer.composedTextChanged.connect(on_composed)
        composer.modeChanged.connect(on_mode_changed)

        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        layout.addWidget(composer)
        layout.addWidget(text_widget)
        container._prompt_text_widget = text_widget
        return container


class NodeChoiceSelectorMixin:
    """Own sampler/scheduler and schema choice combo creation."""

    def _create_basic_string_choice_widget(self, param_name: str, value: str) -> QComboBox | None:
        choices = self._basic_string_choices_for_value(value)
        if choices is None:
            return None
        return self._create_choice_combo(param_name, choices, current_value=value)

    @staticmethod
    def _basic_string_choices_for_value(value: str) -> Sequence[str] | None:
        if value in SAMPLER_CHOICES:
            return SAMPLER_CHOICES
        if value in SCHEDULER_CHOICES:
            return SCHEDULER_CHOICES
        if value in CONTROL_AFTER_GENERATE_CHOICES:
            return CONTROL_AFTER_GENERATE_CHOICES
        if value in UPSCALE_METHOD_CHOICES:
            return UPSCALE_METHOD_CHOICES
        if value in ENABLE_DISABLE_CHOICES:
            return ENABLE_DISABLE_CHOICES
        return None

    def _create_choice_combo(
        self,
        param_name: str,
        choices: Sequence[Any],
        *,
        current_value: Any = None,
        tooltip: str | None = None,
        tooltip_context: str | None = None,
        allow_partial_match: bool = False,
    ) -> QComboBox:
        widget = QComboBox()
        if tooltip:
            widget.setToolTip(tooltip)
        else:
            self._apply_default_choice_tooltip(widget, param_name, tooltip_context)

        widget.addItems([str(choice) for choice in choices])
        self._select_choice_value(
            widget,
            choices,
            current_value,
            allow_partial_match=allow_partial_match,
        )
        widget.currentTextChanged.connect(
            lambda text, name=param_name: self.on_param_changed(name, text)
        )
        return widget

    def _apply_default_choice_tooltip(
        self,
        widget: QWidget,
        param_name: str,
        tooltip_context: str | None,
    ) -> None:
        if tooltip_context == "direct":
            if param_name in ["sampler_name", "sampler"]:
                self._set_runtime_tooltip(widget, "Sampling algorithm")
            elif param_name in ["scheduler"]:
                self._set_runtime_tooltip(widget, "Noise scheduler")
            elif param_name in ["ckpt_name", "checkpoint_name", "model_name"]:
                self._set_runtime_tooltip(widget, "Model checkpoint")
        elif tooltip_context == "nested":
            if param_name in ["ckpt_name", "checkpoint_name", "model_name"]:
                self._set_runtime_tooltip(widget, "Available models/checkpoints")
            elif param_name in ["sampler_name", "sampler"]:
                self._set_runtime_tooltip(widget, "Sampling algorithm")
            elif param_name in ["scheduler"]:
                self._set_runtime_tooltip(widget, "Noise scheduler")
            elif param_name in ["lora_name", "embedding_name"]:
                self._set_runtime_tooltip(widget, "Additional model components")
        elif tooltip_context == "typed":
            if param_name == "control_after_generate":
                self._set_runtime_tooltip(widget, "Control seed behavior after generation")
            elif param_name == "upscale_method":
                self._set_runtime_tooltip(widget, "Upscaling algorithm")

    @staticmethod
    def _select_choice_value(
        widget: QComboBox,
        choices: Sequence[Any],
        current_value: Any,
        *,
        allow_partial_match: bool,
    ) -> None:
        if current_value is not None:
            current_text = str(current_value)
            index = widget.findText(current_text)
            if index >= 0:
                widget.setCurrentIndex(index)
                return
            if allow_partial_match:
                for choice_index, choice in enumerate(choices):
                    choice_text = str(choice)
                    if choice_text in current_text or current_text in choice_text:
                        widget.setCurrentIndex(choice_index)
                        return
        elif widget.count() > 0:
            widget.setCurrentIndex(0)
