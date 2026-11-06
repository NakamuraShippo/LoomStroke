from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.host_api import HostAPI
from loomstroke.plugins.common.constants import (
    SK_LIST,
    SK_PROMPT,
    SK_SYSTEM_PROMPT,
    SK_TEXT,
    SK_USER_PROMPT,
)
from loomstroke.plugins.common.llm_gateway_client import LLMGatewayClient
from loomstroke.plugins.common.snippet_models import (
    PromptPayload,
    Snippet,
    format_prompt_for_log,
)
from loomstroke.plugins.llm_gateway.config import (
    DEFAULT_LOCAL_BASE_URL,
    LOCAL_BASE_URL_KEY,
    LOCAL_PROVIDER,
    validate_local_base_url,
)
from loomstroke.plugins.llm_gateway.model_params import accepts_temperature
from loomstroke.ui.translations import (
    TranslationBinder,
    translate_runtime_text,
    translated_literal_domain,
)

logger = logging.getLogger(__name__)

_PROVIDER_LABELS = {"anthropic": "Anthropic", "gemini": "Gemini", "openai": "OpenAI"}
_NO_TEMPERATURE_TIP = "This model does not use temperature."
# Item data of the trailing "Other model…" entry; never saved or sent.
_OTHER_MODEL = "\x00other-model"
_OTHER_MODEL_TEXT = "Other model…"
# Reasoning models count their thinking toward this limit, so the old 512
# default leaves replies empty; a stored 512 is raised once.
_DEFAULT_MAX_TOKENS = 4096
_LEGACY_MAX_TOKENS = 512
_MAX_TOKENS_MIGRATED_KEY = "prompt_editor/llm/max_tokens_migrated"
_SETTINGS_EXPANDED_KEY = "prompt_editor/llm/settings_expanded"
_OUTPUT_COPIED = "Output copied to clipboard."
# Each provider's own model list, kept so offline launches still show it.
_MODEL_CACHE_KEY = "prompt_editor/llm/model_cache/{provider}"
_MODEL_CACHE_MAX_AGE = 24 * 60 * 60
_REFRESH_MODELS_TIP = "Refresh the model list"
_MODELS_LOADED = "Loaded {count} models."
_MODELS_LOAD_FAILED = "Couldn't update the model list: {error}"
_LOCAL_PROVIDER_TEXT = "Local (OpenAI compatible)"
_NO_MODELS_TEXT = "(No models yet)"
_SELECT_MODEL_FIRST = "Select a model first."
_LOCAL_URL_INVALID = "Enter the URL of a server on this PC, such as http://localhost:11434/v1."
_LOCAL_CONTEXT_NOTE = (
    "Ollama's default context is short and silently cuts the start of long"
    " prompts; raise it with OLLAMA_CONTEXT_LENGTH or the model's num_ctx."
)
_SUBMIT_STYLE = (
    "QToolButton#llmSubmitButton { background: #8b5cf6; color: #ffffff; border: 0;"
    " border-radius: 6px; padding: 3px 12px; font-weight: 700; }"
    "QToolButton#llmSubmitButton:disabled { background: rgba(139, 92, 246, 0.35);"
    " color: rgba(255, 255, 255, 0.6); }"
)


def _llm_ts(text: str) -> str:
    return translated_literal_domain("PromptEditorPanel", text, use_fallback=True)


def _configure_compact_combo(combo: QComboBox, *, min_chars: int = 6) -> None:
    combo.setMinimumWidth(0)
    combo.setMinimumContentsLength(min_chars)
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)


class LLMPaneController:
    """Owns the Prompt Editor LLM tab state and gateway UI bridge."""

    def __init__(
        self,
        parent: QWidget,
        host: HostAPI,
        *,
        llm_client: LLMGatewayClient | None,
        translation_binder: TranslationBinder,
        prompt_snippets_provider: Callable[[], list[Snippet]],
        catalog_get: Callable[[str | None], Snippet | None],
        selected_prompt_provider: Callable[[], Snippet | None],
        library_kind_label: Callable[[str], str],
        add_output_to_library: Callable[[str, str], None],
        create_owned_task: Callable[..., asyncio.Task | None],
        cancel_owned_task: Callable[[asyncio.Task], None],
    ) -> None:
        self._parent = parent
        self._host = host
        self._client = llm_client
        self._translation_binder = translation_binder
        self._prompt_snippets_provider = prompt_snippets_provider
        self._catalog_get = catalog_get
        self._selected_prompt_provider = selected_prompt_provider
        self._library_kind_label = library_kind_label
        self._add_output_to_library = add_output_to_library
        self._create_owned_task = create_owned_task
        self._cancel_owned_task = cancel_owned_task
        self._system_prompt_placeholder = self._ts("Select system prompt...")
        self._user_prompt_placeholder = self._ts("Select user prompt...")

        self.provider_combo: QComboBox | None = None
        self.model_combo: QComboBox | None = None
        self.stream_toggle: QCheckBox | None = None
        self.json_toggle: QCheckBox | None = None
        self.temperature: QDoubleSpinBox | None = None
        self.max_tokens: QSpinBox | None = None
        self.schema_combo: QComboBox | None = None
        self.schema_paths: dict[str, Path] = {}
        self.system_prompt_combo: QComboBox | None = None
        self.user_prompt_combo: QComboBox | None = None
        self.system_prompt: QPlainTextEdit | None = None
        self.user_prompt: QPlainTextEdit | None = None
        self.output: QPlainTextEdit | None = None
        self.submit_button: QToolButton | None = None
        self.cancel_button: QToolButton | None = None
        self.clear_button: QToolButton | None = None
        self.copy_button: QToolButton | None = None
        self.refresh_models_button: QToolButton | None = None
        self._models_task: asyncio.Task | None = None
        self._tab_shown = False
        self.local_url_edit: QLineEdit | None = None
        self.local_note: QLabel | None = None
        self.settings_toggle: QToolButton | None = None
        self.settings_summary: QLabel | None = None
        self.settings_body: QWidget | None = None
        self._settings_form: QFormLayout | None = None
        self.status: QLabel | None = None
        self.job_id: str | None = None
        self.task: asyncio.Task | None = None
        self.status_source: str | None = None
        self.status_kwargs: dict[str, Any] = {}
        self.status_level = "info"
        self._restoring = False
        self._last_model = ""

    def build_tab(self) -> QWidget:  # noqa: PLR0915
        tab = QWidget(self._parent)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Collapsible like the Compose tab's Output; collapsed shows "provider · model".
        settings_row = QHBoxLayout()
        settings_row.setContentsMargins(0, 0, 0, 0)
        settings_row.setSpacing(6)
        self.settings_toggle = QToolButton(tab)
        self.settings_toggle.setCheckable(True)
        self.settings_toggle.setChecked(True)
        self.settings_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.settings_toggle.setArrowType(Qt.ArrowType.DownArrow)
        self.settings_toggle.setAutoRaise(True)
        self._translation_binder.register(self.settings_toggle.setText, self._ts("LLM Settings"))
        # Left-aligned: the hidden summary leaves the row without a stretch.
        settings_row.addWidget(self.settings_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.settings_summary = QLabel(tab)
        self.settings_summary.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.settings_summary.hide()
        settings_row.addWidget(self.settings_summary, 1)
        layout.addLayout(settings_row)
        self.settings_body = QWidget(tab)
        settings_layout = QVBoxLayout(self.settings_body)
        settings_layout.setContentsMargins(8, 0, 0, 0)
        settings_layout.setSpacing(6)

        stream_text = self._ts("Stream updates")
        self.stream_toggle = QCheckBox(stream_text, tab)
        self._translation_binder.register(self.stream_toggle.setText, stream_text)
        json_text = self._ts("Expect JSON response")
        self.json_toggle = QCheckBox(json_text, tab)
        self._translation_binder.register(self.json_toggle.setText, json_text)

        self.provider_combo = QComboBox(tab)
        _configure_compact_combo(self.provider_combo, min_chars=8)
        self.model_combo = QComboBox(tab)
        _configure_compact_combo(self.model_combo, min_chars=8)
        self.temperature = QDoubleSpinBox(tab)
        self.temperature.setRange(0.0, 2.0)
        self.temperature.setSingleStep(0.05)
        self.temperature.setValue(0.7)
        self.max_tokens = QSpinBox(tab)
        self.max_tokens.setRange(0, 32000)
        self.max_tokens.setValue(_DEFAULT_MAX_TOKENS)
        self.schema_combo = QComboBox(tab)
        _configure_compact_combo(self.schema_combo, min_chars=8)
        self.schema_combo.setEnabled(False)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        self._add_form_row(form, self._ts("Provider"), self.provider_combo)
        # Shown only for the local provider; validated before it is saved.
        self.local_url_edit = QLineEdit(tab)
        self.local_url_edit.setPlaceholderText(DEFAULT_LOCAL_BASE_URL)
        self.local_url_edit.setText(
            str(self._settings_get(LOCAL_BASE_URL_KEY, "") or DEFAULT_LOCAL_BASE_URL)
        )
        self.local_url_edit.editingFinished.connect(self._on_local_url_edited)
        self._add_form_row(form, self._ts("Server URL"), self.local_url_edit)
        model_field = QWidget(tab)
        model_row = QHBoxLayout(model_field)
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.setSpacing(4)
        model_row.addWidget(self.model_combo, 1)
        self.refresh_models_button = self._create_compact_icon_button(
            model_field,
            tooltip=self._ts(_REFRESH_MODELS_TIP),
            theme_icon="view-refresh",
            fallback_icon=QStyle.SP_BrowserReload,
            handler=lambda: self.refresh_models(force=True),
            icon_size=14,
            button_size=22,
        )
        model_row.addWidget(self.refresh_models_button)
        self._add_form_row(form, self._ts("Model"), model_field)
        self._add_form_row(form, self._ts("Temperature"), self.temperature)
        self._add_form_row(form, self._ts("Max tokens"), self.max_tokens)
        self._add_form_row(form, self._ts("Schema"), self.schema_combo)
        self._settings_form = form
        settings_layout.addLayout(form)
        self.local_note = QLabel(tab)
        self.local_note.setWordWrap(True)
        self.local_note.setStyleSheet("color: #9a9aa3;")
        self._translation_binder.register(self.local_note.setText, self._ts(_LOCAL_CONTEXT_NOTE))
        settings_layout.addWidget(self.local_note)
        # Local-only rows start hidden; _update_model_state shows them for Local.
        form.setRowVisible(self.local_url_edit, False)
        self.local_note.hide()
        settings_layout.addWidget(self.stream_toggle)
        settings_layout.addWidget(self.json_toggle)
        layout.addWidget(self.settings_body)

        system_row = QHBoxLayout()
        system_row.setContentsMargins(0, 0, 0, 0)
        system_label_text = self._ts("System prompt")
        system_label = QLabel(system_label_text, tab)
        self._translation_binder.register(system_label.setText, system_label_text)
        system_row.addWidget(system_label)
        self.system_prompt_combo = QComboBox(tab)
        _configure_compact_combo(self.system_prompt_combo, min_chars=8)
        self.system_prompt_combo.addItem(
            self._parent.tr(self._system_prompt_placeholder),
            None,
        )
        system_row.addWidget(self.system_prompt_combo, 1)
        layout.addLayout(system_row)

        self.system_prompt = QPlainTextEdit(tab)
        system_placeholder = self._ts("Optional system instructions...")
        self._translation_binder.register(
            self.system_prompt.setPlaceholderText,
            system_placeholder,
        )
        self.system_prompt.setMinimumHeight(40)
        layout.addWidget(self.system_prompt, 1)

        user_row = QHBoxLayout()
        user_row.setContentsMargins(0, 0, 0, 0)
        user_label_text = self._ts("User prompt")
        user_label = QLabel(user_label_text, tab)
        self._translation_binder.register(user_label.setText, user_label_text)
        user_row.addWidget(user_label)
        self.user_prompt_combo = QComboBox(tab)
        _configure_compact_combo(self.user_prompt_combo, min_chars=8)
        self.user_prompt_combo.addItem(
            self._parent.tr(self._user_prompt_placeholder),
            None,
        )
        user_row.addWidget(self.user_prompt_combo, 1)
        layout.addLayout(user_row)

        self.user_prompt = QPlainTextEdit(tab)
        user_placeholder = self._ts("Enter user prompt...")
        self._translation_binder.register(
            self.user_prompt.setPlaceholderText,
            user_placeholder,
        )
        self.user_prompt.setMinimumHeight(40)
        layout.addWidget(self.user_prompt, 2)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        self.submit_button = self._create_compact_icon_button(
            tab,
            tooltip=self._ts("Submit"),
            theme_icon="mail-send",
            fallback_icon=QStyle.SP_DialogApplyButton,
            handler=self.submit,
        )
        # The main action: labeled and filled like the Compose tab's Save.
        self.submit_button.setObjectName("llmSubmitButton")
        self.submit_button.setStyleSheet(_SUBMIT_STYLE)
        self.submit_button.setMinimumSize(0, 0)
        self.submit_button.setMaximumSize(16777215, 16777215)
        self.submit_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.submit_button.setAutoRaise(False)
        self.submit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self._translation_binder.register(self.submit_button.setText, self._ts("Submit"))
        action_row.addWidget(self.submit_button)
        self.cancel_button = self._create_compact_icon_button(
            tab,
            tooltip=self._ts("Cancel"),
            theme_icon="process-stop",
            fallback_icon=QStyle.SP_DialogCancelButton,
            handler=self.cancel,
        )
        action_row.addWidget(self.cancel_button)
        self.clear_button = self._create_compact_icon_button(
            tab,
            tooltip=self._ts("Clear"),
            theme_icon="edit-clear",
            fallback_icon=QStyle.SP_DialogResetButton,
            handler=self.clear,
        )
        action_row.addWidget(self.clear_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        output_row = QHBoxLayout()
        output_row.setContentsMargins(0, 0, 0, 0)
        output_text = self._ts("Output")
        output_label = QLabel(output_text, tab)
        self._translation_binder.register(output_label.setText, output_text)
        output_row.addWidget(output_label, 1)
        self.copy_button = QToolButton(tab)
        self._translation_binder.register(self.copy_button.setText, self._ts("Copy"))
        self.copy_button.clicked.connect(self.copy_output)
        output_row.addWidget(self.copy_button)
        layout.addLayout(output_row)
        self.output = QPlainTextEdit(tab)
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(40)
        self.output.setContextMenuPolicy(Qt.CustomContextMenu)
        self.output.customContextMenuRequested.connect(self.on_output_context_menu)
        layout.addWidget(self.output, 3)

        self.status = QLabel("", tab)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self._connect_signals()
        self.initialize()
        return tab

    def refresh_translations(self) -> None:
        self.refresh_prompt_lists()
        if self.model_combo is not None:
            other = self.model_combo.findData(_OTHER_MODEL)
            if other >= 0:
                self.model_combo.setItemText(other, self._ts(_OTHER_MODEL_TEXT))
        if self.provider_combo is not None:
            local = self.provider_combo.findData(LOCAL_PROVIDER)
            if local >= 0:
                self.provider_combo.setItemText(local, self._provider_label(LOCAL_PROVIDER))
        self._update_model_state()
        if self.status_source:
            self.set_status_translated(
                self.status_source,
                level=self.status_level,
                **self.status_kwargs,
            )

    def cleanup(self) -> None:
        self.task = None
        self.job_id = None

    def initialize(self) -> None:
        if not self._client:
            if self.submit_button:
                self.submit_button.setEnabled(False)
            self.set_status_translated("LLM gateway unavailable.")
            return
        self.load_schemas()
        self.load_providers()
        self.restore_state()

    def on_json_toggled(self, checked: bool) -> None:
        if self.schema_combo:
            self.schema_combo.setEnabled(checked)
        self.persist_state()

    def on_provider_changed(self, _index: int) -> None:
        self.load_models()
        self.persist_state()

    def load_providers(self) -> None:
        if not self._client or self.provider_combo is None:
            return
        providers: list[str] = []
        try:
            providers = self._client.providers()
        except Exception as exc:
            self.set_status_translated("Failed to load providers: {error}", error=exc)
        if not providers:
            providers = ["default"]
        current = self.provider_combo.currentData()
        self.provider_combo.blockSignals(True)
        self.provider_combo.clear()
        for provider in providers:
            self.provider_combo.addItem(self._provider_label(provider), provider)
        self.provider_combo.blockSignals(False)
        if current:
            index = self.provider_combo.findData(current)
            if index >= 0:
                self.provider_combo.setCurrentIndex(index)
        self.load_models()

    def load_models(self, *, keep_current: bool = False) -> None:
        if not self._client or self.model_combo is None or self.provider_combo is None:
            return
        provider = self.provider_combo.currentData()
        models: list[str] = []
        if provider:
            try:
                models = self._client.models_for(provider)
            except Exception as exc:
                self.set_status_translated("Failed to load models: {error}", error=exc)
        # The local provider has no default model; its server lists them.
        if not models and provider != LOCAL_PROVIDER:
            models = [provider or "default"]
        listed = self._cached_models(str(provider or ""))
        # Recommended first; once the provider's own list is known, only
        # recommendations it still serves, then everything else it lists.
        recommended = [m for m in models if m in listed] if listed else models
        others = sorted(set(listed) - set(recommended), reverse=True)
        current = self.model_combo.currentData()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for model in recommended:
            self.model_combo.addItem(model, model)
        if recommended and others:
            self.model_combo.insertSeparator(self.model_combo.count())
        for model in others:
            self.model_combo.addItem(model, model)
        if not recommended and not others:
            # A selectable "Other model…" alone could not be re-picked, and
            # nothing valid would be sent; show why the list is empty instead.
            self.model_combo.addItem(self._ts(_NO_MODELS_TEXT), "")
        # IDs outside the curated list come in through a dialog; a plain
        # dropdown opens on any click, an editable one only on its arrow.
        self.model_combo.addItem(self._ts(_OTHER_MODEL_TEXT), _OTHER_MODEL)
        if keep_current and current and current != _OTHER_MODEL:
            self.model_combo.setCurrentIndex(self._model_index(str(current)))
        elif current:
            index = self.model_combo.findData(current)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
        self.model_combo.blockSignals(False)
        self._update_model_state()
        self.refresh_models()

    def _cached_models(self, provider: str) -> list[str]:
        cached = self._settings_get(_MODEL_CACHE_KEY.format(provider=provider), {})
        if isinstance(cached, dict) and isinstance(cached.get("models"), list):
            return [str(model) for model in cached["models"] if model]
        return []

    def on_tab_shown(self) -> None:
        self._tab_shown = True
        self.refresh_models()

    def refresh_models(self, *, force: bool = False) -> None:
        """Fetch the provider's own list when forced or when the cached copy is stale.

        Automatic fetches wait until the LLM tab has been opened.
        """
        if not self._client or self.provider_combo is None:
            return
        if not (force or self._tab_shown):
            return
        provider = str(self.provider_combo.currentData() or "")
        if not provider or (self._models_task is not None and not self._models_task.done()):
            return
        cached = self._settings_get(_MODEL_CACHE_KEY.format(provider=provider), {})
        fetched_at = cached.get("fetched_at", 0) if isinstance(cached, dict) else 0
        fresh = (
            isinstance(fetched_at, (int, float)) and time.time() - fetched_at < _MODEL_CACHE_MAX_AGE
        )
        if fresh and not force:
            return
        try:
            self._models_task = self._create_owned_task(
                self._fetch_models(provider), consume_exceptions=True
            )
        except RuntimeError:
            # No event loop (e.g. headless use): keep the current list.
            logger.debug("No event loop for the %s model list request", provider)

    async def _fetch_models(self, provider: str) -> None:
        if not self._client:
            return
        try:
            models = await self._client.fetch_models(provider)
        except Exception as exc:
            logger.debug("Model list request for %s failed", provider, exc_info=True)
            self.set_status_translated(_MODELS_LOAD_FAILED, level="error", error=exc)
            return
        if models is None:
            return
        self._settings_set(
            _MODEL_CACHE_KEY.format(provider=provider),
            {"fetched_at": time.time(), "models": list(dict.fromkeys(models))},
        )
        if self.provider_combo is not None and self.provider_combo.currentData() == provider:
            self.load_models(keep_current=True)
        self.set_status_translated(_MODELS_LOADED, count=len(models))

    def _update_model_state(self) -> None:
        """Disable temperature for models that drop it and refresh the collapsed summary."""
        if self.temperature is None or self.provider_combo is None or self.model_combo is None:
            return
        provider = str(self.provider_combo.currentData() or "")
        model = self.selected_model()
        if self.model_combo.currentData() != _OTHER_MODEL:
            self._last_model = model
        usable = accepts_temperature(provider, model)
        self.temperature.setEnabled(usable)
        self.temperature.setToolTip("" if usable else self._ts(_NO_TEMPERATURE_TIP))
        if self.settings_summary is not None:
            label = self.provider_combo.currentText()
            self.settings_summary.setText(f"{label} · {model}" if model else label)
        is_local = provider == LOCAL_PROVIDER
        if self._settings_form is not None and self.local_url_edit is not None:
            self._settings_form.setRowVisible(self.local_url_edit, is_local)
        if self.local_note is not None:
            self.local_note.setVisible(is_local)

    def selected_model(self) -> str:
        """The chosen model ID; '' while "Other model…" or the empty-list note is current."""
        if self.model_combo is None:
            return ""
        data = self.model_combo.currentData()
        return data.strip() if isinstance(data, str) and data != _OTHER_MODEL else ""

    def _provider_label(self, provider: str) -> str:
        if provider == LOCAL_PROVIDER:
            return self._ts(_LOCAL_PROVIDER_TEXT)
        return _PROVIDER_LABELS.get(provider, provider.capitalize())

    def _on_local_url_edited(self) -> None:
        if self.local_url_edit is None:
            return
        stored = str(self._settings_get(LOCAL_BASE_URL_KEY, "") or DEFAULT_LOCAL_BASE_URL)
        text = self.local_url_edit.text().strip() or DEFAULT_LOCAL_BASE_URL
        try:
            url = validate_local_base_url(text)
        except ValueError:
            self.local_url_edit.setText(stored)
            self.set_status_translated(_LOCAL_URL_INVALID, level="error")
            return
        self.local_url_edit.setText(url)
        if url == stored:
            return
        self._settings_set(LOCAL_BASE_URL_KEY, url)
        # Another server serves other models.
        if self.provider_combo is not None and self.provider_combo.currentData() == LOCAL_PROVIDER:
            self.refresh_models(force=True)

    def _model_index(self, model: str) -> int:
        """Index of *model*, inserting it just above "Other model…" when missing."""
        assert self.model_combo is not None
        index = self.model_combo.findData(model)
        if index < 0:
            other = self.model_combo.findData(_OTHER_MODEL)
            index = other if other >= 0 else self.model_combo.count()
            self.model_combo.insertItem(index, model, model)
        return index

    def _on_model_changed(self) -> None:
        combo = self.model_combo
        if combo is not None and combo.currentData() == _OTHER_MODEL:
            model, accepted = QInputDialog.getText(
                self._parent, self._ts("Model ID"), self._ts("Enter a model ID:")
            )
            model = model.strip()
            block = combo.blockSignals(True)
            if accepted and model:
                combo.setCurrentIndex(self._model_index(model))
            else:
                combo.setCurrentIndex(max(combo.findData(self._last_model), 0))
            combo.blockSignals(block)
        self._update_model_state()
        self.persist_state()

    def set_settings_expanded(self, expanded: bool) -> None:
        if self.settings_toggle is None or self.settings_body is None:
            return
        if self.settings_toggle.isChecked() != expanded:
            self.settings_toggle.setChecked(expanded)
            return
        self.settings_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow
        )
        self.settings_body.setVisible(expanded)
        if self.settings_summary is not None:
            self.settings_summary.setVisible(not expanded)
        if not self._restoring:
            self._settings_set(_SETTINGS_EXPANDED_KEY, int(expanded))

    def copy_output(self) -> None:
        if self.output is None:
            return
        QApplication.clipboard().setText(self.output.toPlainText())
        self.set_status_translated(_OUTPUT_COPIED)

    def load_schemas(self) -> None:
        if self.schema_combo is None:
            return
        base = Path(__file__).resolve()
        schema_dir = base.parents[4] / "configs" / "text_editor" / "schema"
        self.schema_paths.clear()
        self.schema_combo.clear()
        if schema_dir.exists():
            for path in sorted(schema_dir.glob("*.json")):
                self.schema_paths[path.stem] = path
                self.schema_combo.addItem(path.stem, path)
        if self._settings_form is not None:
            # No bundled schemas ship today; an always-empty row only confuses.
            self._settings_form.setRowVisible(self.schema_combo, bool(self.schema_paths))

    def restore_state(self) -> None:
        # Combo signals fired mid-restore would persist fields not yet restored.
        self._restoring = True
        try:
            self._restore_fields()
        finally:
            self._restoring = False

    def _restore_fields(self) -> None:  # noqa: PLR0912
        if not self.provider_combo or not self.model_combo:
            return
        provider = self._settings_get("prompt_editor/llm/provider", None)
        if provider:
            index = self.provider_combo.findData(provider)
            if index >= 0:
                self.provider_combo.setCurrentIndex(index)
        model = self._settings_get("prompt_editor/llm/model", None)
        if model and model != _OTHER_MODEL:
            # A custom or retired model stays selectable.
            self.model_combo.setCurrentIndex(self._model_index(str(model)))
        if self.stream_toggle:
            stream = self._coerce_bool(self._settings_get("prompt_editor/llm/stream", True), True)
            self.stream_toggle.setChecked(stream)
        if self.json_toggle:
            json_mode = self._coerce_bool(
                self._settings_get("prompt_editor/llm/json_mode", False),
                False,
            )
            self.json_toggle.setChecked(json_mode)
            if self.schema_combo:
                self.schema_combo.setEnabled(json_mode)
        if self.schema_combo:
            schema_value = self._settings_get("prompt_editor/llm/schema", "")
            if schema_value:
                schema_path = Path(schema_value)
                index = self.schema_combo.findData(schema_path)
                if index < 0:
                    index = self.schema_combo.findText(schema_path.stem)
                if index >= 0:
                    self.schema_combo.setCurrentIndex(index)
        if self.temperature:
            temp = self._settings_get("prompt_editor/llm/temperature", None)
            if temp is not None:
                with contextlib.suppress(Exception):
                    self.temperature.setValue(float(temp))
        if self.max_tokens:
            tokens = self._settings_get("prompt_editor/llm/max_tokens", None)
            migrated = self._coerce_bool(self._settings_get(_MAX_TOKENS_MIGRATED_KEY, False))
            if tokens is not None:
                with contextlib.suppress(Exception):
                    value = int(tokens)
                    if value == _LEGACY_MAX_TOKENS and not migrated:
                        value = _DEFAULT_MAX_TOKENS
                        # setValue() emits nothing when the field already shows it.
                        self._settings_set("prompt_editor/llm/max_tokens", value)
                    self.max_tokens.setValue(value)
            if not migrated:
                self._settings_set(_MAX_TOKENS_MIGRATED_KEY, 1)
        self.set_settings_expanded(
            self._coerce_bool(self._settings_get(_SETTINGS_EXPANDED_KEY, True), True)
        )
        self._update_model_state()

    def persist_state(self) -> None:
        if self._restoring or not self.provider_combo or not self.model_combo:
            return
        provider = self.provider_combo.currentData() or self.provider_combo.currentText()
        model = self.selected_model()
        schema_data = ""
        if self.schema_combo:
            index = self.schema_combo.currentIndex()
            if index >= 0:
                schema_path = self.schema_combo.itemData(index)
                if schema_path:
                    schema_data = str(schema_path)
        self._settings_set("prompt_editor/llm/provider", str(provider))
        if model:
            # The empty-list note is not a model; keep the last real choice.
            self._settings_set("prompt_editor/llm/model", model)
        self._settings_set("prompt_editor/llm/schema", schema_data)
        if self.stream_toggle:
            self._settings_set("prompt_editor/llm/stream", int(self.stream_toggle.isChecked()))
        if self.json_toggle:
            self._settings_set(
                "prompt_editor/llm/json_mode",
                int(self.json_toggle.isChecked()),
            )
        if self.temperature:
            self._settings_set("prompt_editor/llm/temperature", float(self.temperature.value()))
        if self.max_tokens:
            self._settings_set("prompt_editor/llm/max_tokens", int(self.max_tokens.value()))
        if self.system_prompt_combo:
            self._settings_set(
                "prompt_editor/llm/system_prompt_id",
                self.system_prompt_combo.currentData(),
            )
        if self.user_prompt_combo:
            self._settings_set(
                "prompt_editor/llm/user_prompt_id",
                self.user_prompt_combo.currentData(),
            )

    def refresh_prompt_lists(self) -> None:
        if not self.system_prompt_combo or not self.user_prompt_combo:
            return
        system_selected = self.system_prompt_combo.currentData()
        user_selected = self.user_prompt_combo.currentData()
        if not system_selected:
            system_selected = self._settings_get("prompt_editor/llm/system_prompt_id", None)
        if not user_selected:
            user_selected = self._settings_get("prompt_editor/llm/user_prompt_id", None)
        prompt_snippets = self._prompt_snippets_provider()
        system_snippets = [
            snippet for snippet in prompt_snippets if snippet.kind == SK_SYSTEM_PROMPT
        ]
        user_snippets = [snippet for snippet in prompt_snippets if snippet.kind == SK_USER_PROMPT]
        self._populate_prompt_combo(
            self.system_prompt_combo,
            system_snippets,
            self._system_prompt_placeholder,
            system_selected,
        )
        self._populate_prompt_combo(
            self.user_prompt_combo,
            user_snippets,
            self._user_prompt_placeholder,
            user_selected,
        )
        self._apply_prompt_selection(self.system_prompt_combo, self.system_prompt)
        self._apply_prompt_selection(self.user_prompt_combo, self.user_prompt)

    def populate_prompt_combo(
        self,
        combo: QComboBox,
        snippets: list[Snippet],
        placeholder: str,
        selected_id: str | None,
    ) -> None:
        self._populate_prompt_combo(combo, snippets, placeholder, selected_id)

    def apply_prompt_selection(
        self,
        combo: QComboBox | None,
        target: QPlainTextEdit | None,
    ) -> None:
        self._apply_prompt_selection(combo, target)

    def on_system_prompt_selected(self, _index: int) -> None:
        self._apply_prompt_selection(self.system_prompt_combo, self.system_prompt)
        self.persist_state()

    def on_user_prompt_selected(self, _index: int) -> None:
        self._apply_prompt_selection(self.user_prompt_combo, self.user_prompt)
        self.persist_state()

    def submit(self) -> None:
        if not self._client:
            self.set_status_translated("LLM gateway unavailable.")
            return
        if not self.user_prompt:
            return
        prompt_text = self.user_prompt.toPlainText().strip()
        if not prompt_text:
            self.set_status_translated("User prompt is empty.")
            return
        system_text = self.system_prompt.toPlainText().strip() if self.system_prompt else ""
        provider = (self.provider_combo.currentData() if self.provider_combo else None) or (
            self.provider_combo.currentText() if self.provider_combo else ""
        )
        model = self.selected_model()
        if not provider:
            provider = "default"
        if not model and provider == LOCAL_PROVIDER:
            # The provider name is never a local model ID.
            self.set_status_translated(_SELECT_MODEL_FIRST, level="error")
            return
        if not model:
            model = provider
        schema_path = None
        if self.schema_combo:
            schema_index = self.schema_combo.currentIndex()
            if schema_index >= 0:
                schema_path = self.schema_combo.itemData(schema_index)
                if schema_path:
                    schema_path = Path(schema_path)
        max_tokens_value = self.max_tokens.value() if self.max_tokens else 512
        selected_prompt = self._selected_prompt_provider()
        payload = PromptPayload(
            provider=str(provider),
            model=str(model),
            prompt=prompt_text,
            system_prompt=system_text or None,
            expect_json=self.json_toggle.isChecked() if self.json_toggle else False,
            stream=self.stream_toggle.isChecked() if self.stream_toggle else True,
            temperature=self.temperature.value() if self.temperature else 0.7,
            max_tokens=max_tokens_value or None,
            json_schema_path=schema_path,
            metadata={
                "prompt_id": selected_prompt.id if selected_prompt else None,
                "source": "prompt_editor",
            },
        )
        warnings = payload.validate()
        for warning in warnings:
            self.set_status(warning, level="warning")
        if self.output:
            self.output.clear()
        self.task = self._create_owned_task(
            self.submit_async(payload),
            cancel_previous=self.task,
        )

    async def submit_async(self, payload: PromptPayload) -> None:
        if not self._client:
            return
        try:
            job_id = await self._client.submit(payload)
        except Exception as exc:
            self.set_status_translated("Submission failed: {error}", level="error", error=exc)
            return
        self.job_id = job_id
        self.set_status_translated(
            "Submitted job {job_id}: {prompt}",
            job_id=job_id,
            prompt=format_prompt_for_log(payload),
        )
        if payload.stream:
            await self.consume_stream(job_id)
        else:
            await self.await_result(job_id)
        self.job_id = None
        if self.task is asyncio.current_task():
            self.task = None

    async def consume_stream(self, job_id: str) -> None:
        if not self._client or self.output is None:
            return
        collected: list[str] = []
        try:
            async for event in self._client.stream(job_id):
                message = getattr(event, "message", None)
                if message:
                    collected.append(str(message))
                    self.output.setPlainText("".join(collected))
            final_output = "".join(collected)
            if final_output:
                self.output.setPlainText(final_output)
                self.set_status_translated("Received streaming output.")
        except Exception as exc:
            self.set_status_translated("Streaming failed: {error}", level="error", error=exc)

    async def await_result(self, job_id: str) -> None:
        if not self._client or self.output is None:
            return
        try:
            result = await self._client.result(job_id)
        except Exception as exc:
            self.set_status_translated(
                "Result retrieval failed: {error}",
                level="error",
                error=exc,
            )
            return
        data = getattr(result, "data", None)
        output_text = ""
        if isinstance(data, dict):
            output_text = str(data.get("text") or data.get("content") or "")
            if not output_text:
                str_values = [
                    str(value) for value in data.values() if isinstance(value, (str, int, float))
                ]
                output_text = "\n".join(str_values) if str_values else str(data)
        elif isinstance(data, (list, tuple)):
            output_text = "\n".join(str(item) for item in data)
        elif data is not None:
            output_text = str(data)
        self.output.setPlainText(output_text)
        if output_text:
            self.set_status_translated("Received response from LLM.")
        else:
            self.set_status_translated(
                "LLM response did not include text.",
                level="warning",
            )

    def cancel(self) -> None:
        if not self.job_id:
            self.set_status_translated("No active job to cancel.")
            return
        self._create_owned_task(self.cancel_async(), consume_exceptions=True)

    async def cancel_async(self) -> None:
        if not self._client or not self.job_id:
            return
        job_id = self.job_id
        try:
            await self._client.cancel(job_id)
        except Exception as exc:
            self.set_status_translated("Cancel failed: {error}", level="error", error=exc)
            return
        if self.task:
            self._cancel_owned_task(self.task)
            self.task = None
        self.job_id = None
        self.set_status_translated("Cancelled job {job_id}.", job_id=job_id)

    def clear(self) -> None:
        if self.user_prompt:
            self.user_prompt.clear()
        if self.system_prompt:
            self.system_prompt.clear()
        if self.output:
            self.output.clear()
        if self.system_prompt_combo:
            self.system_prompt_combo.setCurrentIndex(0)
        if self.user_prompt_combo:
            self.user_prompt_combo.setCurrentIndex(0)
        self.persist_state()
        self.set_status_translated("Cleared prompts.")

    def set_status(self, message: str, *, level: str = "info") -> None:
        if self.status is None:
            return
        self.status_source = None
        self.status_kwargs = {}
        self.status_level = level
        self.render_status(message, level=level)

    def render_status(self, message: str, *, level: str = "info") -> None:
        if self.status is None:
            return
        palette = {
            "info": "#2563eb",
            "warning": "#b45309",
            "error": "#b91c1c",
        }
        color = palette.get(level, "#374151")
        self.status.setText(f"<span style='color:{color}'>{message}</span>")

    def set_status_translated(
        self,
        source_text: str,
        *,
        level: str = "info",
        **kwargs: Any,
    ) -> None:
        self.status_source = source_text
        self.status_kwargs = dict(kwargs)
        self.status_level = level
        self.render_status(self._ts(source_text).format(**kwargs), level=level)

    def on_output_context_menu(self, pos: Any) -> None:
        if self.output is None:
            return
        menu = self.output.createStandardContextMenu()
        cursor = self.output.textCursor()
        selected = cursor.selectedText().replace("\u2029", "\n")
        menu.addSeparator()
        add_library_menu = menu.addMenu(self._ts("Add Library"))
        add_library_menu.setEnabled(bool(selected.strip()))
        for kind in (SK_TEXT, SK_LIST, SK_PROMPT):
            label = self._library_kind_label(kind)
            action = add_library_menu.addAction(label)
            action.triggered.connect(
                lambda _checked=False, k=kind, t=selected: self._add_output_to_library(k, t)
            )
        menu.exec(self.output.mapToGlobal(pos))
        del menu, add_library_menu

    def _connect_signals(self) -> None:
        if self.settings_toggle:
            self.settings_toggle.toggled.connect(self.set_settings_expanded)
        if self.json_toggle:
            self.json_toggle.toggled.connect(self.on_json_toggled)
        if self.provider_combo:
            self.provider_combo.currentIndexChanged.connect(self.on_provider_changed)
        if self.model_combo:
            self.model_combo.currentIndexChanged.connect(lambda _idx: self._on_model_changed())
        if self.stream_toggle:
            self.stream_toggle.toggled.connect(lambda _state: self.persist_state())
        if self.temperature:
            self.temperature.valueChanged.connect(lambda _value: self.persist_state())
        if self.max_tokens:
            self.max_tokens.valueChanged.connect(lambda _value: self.persist_state())
        if self.schema_combo:
            self.schema_combo.currentIndexChanged.connect(lambda _idx: self.persist_state())
        if self.system_prompt_combo:
            self.system_prompt_combo.currentIndexChanged.connect(self.on_system_prompt_selected)
        if self.user_prompt_combo:
            self.user_prompt_combo.currentIndexChanged.connect(self.on_user_prompt_selected)

    def _populate_prompt_combo(
        self,
        combo: QComboBox,
        snippets: list[Snippet],
        placeholder: str,
        selected_id: str | None,
    ) -> None:
        block = combo.blockSignals(True)
        combo.clear()
        combo.addItem(translate_runtime_text(placeholder, self._parent.tr), None)
        for snippet in sorted(snippets, key=lambda entry: (entry.title or entry.id).lower()):
            combo.addItem(snippet.title or snippet.id, snippet.id)
        if selected_id:
            index = combo.findData(selected_id)
            if index >= 0:
                combo.setCurrentIndex(index)
        combo.blockSignals(block)

    def _apply_prompt_selection(
        self,
        combo: QComboBox | None,
        target: QPlainTextEdit | None,
    ) -> None:
        if combo is None or target is None:
            return
        snippet_id = combo.currentData()
        if not snippet_id:
            return
        snippet = self._catalog_get(str(snippet_id))
        if not snippet:
            return
        block = target.blockSignals(True)
        target.setPlainText(snippet.body or "")
        target.blockSignals(block)

    def _add_form_row(self, layout: QFormLayout, text: str, field: QWidget) -> None:
        label = QLabel()
        self._translation_binder.register(label.setText, text)
        layout.addRow(label, field)

    def _create_compact_icon_button(
        self,
        parent: QWidget,
        *,
        tooltip: str,
        theme_icon: str,
        fallback_icon: QStyle.StandardPixmap,
        handler: Callable[[], None],
        icon_size: int = 18,
        button_size: int = 24,
    ) -> QToolButton:
        button = QToolButton(parent)
        icon = QIcon.fromTheme(theme_icon)
        if icon.isNull():
            icon = self._parent.style().standardIcon(fallback_icon)
        button.setIcon(icon)
        self._translation_binder.register(button.setToolTip, tooltip)
        button.setIconSize(QSize(icon_size, icon_size))
        button.setFixedSize(QSize(button_size, button_size))
        button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        button.setAutoRaise(True)
        button.clicked.connect(handler)
        return button

    def _settings_get(self, key: str, default: Any = None) -> Any:
        settings = getattr(self._host, "settings", None)
        getter = getattr(settings, "get", None)
        if callable(getter):
            try:
                return getter(key, default)
            except Exception:
                return default
        return default

    def _settings_set(self, key: str, value: Any) -> None:
        settings = getattr(self._host, "settings", None)
        setter = getattr(settings, "set", None)
        if callable(setter):
            try:
                setter(key, value)
            except Exception:
                logger.debug("Failed to persist setting %s", key)

    @staticmethod
    def _coerce_bool(value: Any, default: bool = False) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
        if isinstance(value, (int, float)):
            return bool(value)
        return default

    def _ts(self, text: str) -> str:
        return _llm_ts(text)
