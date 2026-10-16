from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
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
from loomstroke.ui.translations import (
    TranslationBinder,
    translate_runtime_text,
    translated_literal_domain,
)

logger = logging.getLogger(__name__)


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
        self.status: QLabel | None = None
        self.job_id: str | None = None
        self.task: asyncio.Task | None = None
        self.status_source: str | None = None
        self.status_kwargs: dict[str, Any] = {}
        self.status_level = "info"

    def build_tab(self) -> QWidget:  # noqa: PLR0915
        tab = QWidget(self._parent)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        settings_title = self._ts("LLM Settings")
        settings_box = QGroupBox(settings_title, tab)
        self._translation_binder.register(settings_box.setTitle, settings_title)
        settings_layout = QVBoxLayout(settings_box)
        settings_layout.setContentsMargins(8, 8, 8, 8)
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
        self.max_tokens.setValue(512)
        self.schema_combo = QComboBox(tab)
        _configure_compact_combo(self.schema_combo, min_chars=8)
        self.schema_combo.setEnabled(False)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        self._add_form_row(form, self._ts("Provider"), self.provider_combo)
        self._add_form_row(form, self._ts("Model"), self.model_combo)
        self._add_form_row(form, self._ts("Temperature"), self.temperature)
        self._add_form_row(form, self._ts("Max tokens"), self.max_tokens)
        self._add_form_row(form, self._ts("Schema"), self.schema_combo)
        settings_layout.addLayout(form)
        settings_layout.addWidget(self.stream_toggle)
        settings_layout.addWidget(self.json_toggle)
        layout.addWidget(settings_box)

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
        system_row.addWidget(self.system_prompt_combo)
        system_row.addStretch(1)
        layout.addLayout(system_row)

        self.system_prompt = QPlainTextEdit(tab)
        system_placeholder = self._ts("Optional system instructions...")
        self._translation_binder.register(
            self.system_prompt.setPlaceholderText,
            system_placeholder,
        )
        self.system_prompt.setMinimumHeight(40)
        layout.addWidget(self.system_prompt)

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
        user_row.addWidget(self.user_prompt_combo)
        user_row.addStretch(1)
        layout.addLayout(user_row)

        self.user_prompt = QPlainTextEdit(tab)
        user_placeholder = self._ts("Enter user prompt...")
        self._translation_binder.register(
            self.user_prompt.setPlaceholderText,
            user_placeholder,
        )
        self.user_prompt.setMinimumHeight(40)
        layout.addWidget(self.user_prompt)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        self.submit_button = self._create_compact_icon_button(
            tab,
            tooltip=self._ts("Submit"),
            theme_icon="mail-send",
            fallback_icon=QStyle.SP_DialogApplyButton,
            handler=self.submit,
        )
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

        output_text = self._ts("Output")
        output_label = QLabel(output_text, tab)
        self._translation_binder.register(output_label.setText, output_text)
        layout.addWidget(output_label)
        self.output = QPlainTextEdit(tab)
        self.output.setReadOnly(True)
        self.output.setMinimumHeight(40)
        self.output.setContextMenuPolicy(Qt.CustomContextMenu)
        self.output.customContextMenuRequested.connect(self.on_output_context_menu)
        layout.addWidget(self.output)

        self.status = QLabel("", tab)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        self._connect_signals()
        self.initialize()
        return tab

    def refresh_translations(self) -> None:
        self.refresh_prompt_lists()
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
            label = provider.capitalize()
            self.provider_combo.addItem(label, provider)
        self.provider_combo.blockSignals(False)
        if current:
            index = self.provider_combo.findData(current)
            if index >= 0:
                self.provider_combo.setCurrentIndex(index)
        self.load_models()

    def load_models(self) -> None:
        if not self._client or self.model_combo is None or self.provider_combo is None:
            return
        provider = self.provider_combo.currentData()
        models: list[str] = []
        if provider:
            try:
                models = self._client.models_for(provider)
            except Exception as exc:
                self.set_status_translated("Failed to load models: {error}", error=exc)
        if not models:
            models = [provider or "default"]
        current = self.model_combo.currentData()
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for model in models:
            self.model_combo.addItem(model, model)
        self.model_combo.blockSignals(False)
        if current:
            index = self.model_combo.findData(current)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)

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

    def restore_state(self) -> None:  # noqa: PLR0912
        if not self.provider_combo or not self.model_combo:
            return
        provider = self._settings_get("prompt_editor/llm/provider", None)
        if provider:
            index = self.provider_combo.findData(provider)
            if index >= 0:
                self.provider_combo.setCurrentIndex(index)
        model = self._settings_get("prompt_editor/llm/model", None)
        if model:
            index = self.model_combo.findData(model)
            if index >= 0:
                self.model_combo.setCurrentIndex(index)
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
            if tokens is not None:
                with contextlib.suppress(Exception):
                    self.max_tokens.setValue(int(tokens))

    def persist_state(self) -> None:
        if not self.provider_combo or not self.model_combo:
            return
        provider = self.provider_combo.currentData() or self.provider_combo.currentText()
        model = self.model_combo.currentData() or self.model_combo.currentText()
        schema_data = ""
        if self.schema_combo:
            index = self.schema_combo.currentIndex()
            if index >= 0:
                schema_path = self.schema_combo.itemData(index)
                if schema_path:
                    schema_data = str(schema_path)
        self._settings_set("prompt_editor/llm/provider", str(provider))
        self._settings_set("prompt_editor/llm/model", str(model))
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
        model = (self.model_combo.currentData() if self.model_combo else None) or (
            self.model_combo.currentText() if self.model_combo else ""
        )
        if not provider:
            provider = "default"
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
        if self.json_toggle:
            self.json_toggle.toggled.connect(self.on_json_toggled)
        if self.provider_combo:
            self.provider_combo.currentIndexChanged.connect(self.on_provider_changed)
        if self.model_combo:
            self.model_combo.currentIndexChanged.connect(lambda _idx: self.persist_state())
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
