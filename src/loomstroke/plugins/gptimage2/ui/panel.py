"""Qt panel for configuring GPT Image 2 jobs."""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QEvent, QMetaObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.event_bus_extensions import get_extended_event_bus
from loomstroke.core.events import DocumentChangeEvent, Events
from loomstroke.plugins.comfyui.ui.prompt_composer import PromptComposerWidget
from loomstroke.plugins.common.input_layer_selector import InputLayerSelector
from loomstroke.plugins.common.outbound_policy import is_external_services_allowed
from loomstroke.plugins.sdk.base import JobSpec
from loomstroke.ui._dock_safe import is_valid
from loomstroke.ui.translations import TranslationBinder, translate_with_fallback

from ..input_images import MAX_INPUT_LAYERS
from ..types import (
    ALL_BACKGROUNDS,
    ALL_MODELS,
    ALL_MODERATION,
    ALL_OUTPUT_FORMATS,
    ALL_QUALITIES,
    ALL_SIZES,
    DEFAULT_MODEL,
    MODEL_DISPLAY_NAMES,
    GPTImage2Options,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _StatusEntry:
    message: str | None = None
    source_text: str | None = None
    domain: str | None = None
    kwargs: dict[str, Any] = field(default_factory=dict)


class GPTImage2Panel(QWidget):
    """Configuration panel for GPT Image 2 image generation."""

    submitRequested = Signal(JobSpec)

    def __init__(self, host, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host = host
        self._target_layer_choices: list[tuple[str, str]] = []
        self._status_entries: list[_StatusEntry] = []
        self._translation_binder = TranslationBinder(self.tr)
        self._event_bus = get_extended_event_bus()
        self._translation_handlers: list[Any] = []
        self._translation_tokens: list[Any] = []
        self._new_layer_label = self._ts("<New Layer>")
        self._prompt_required_text = self._ts("Prompt is required")
        self._job_submitted_text = self._ts("Submitted job to queue")
        self._auto_label = self._ts("Auto")
        self._low_label = self._ts("Low")
        self._medium_label = self._ts("Medium")
        self._high_label = self._ts("High")
        self._opaque_label = self._ts("Opaque")
        self._format_png_label = self._ts("PNG")
        self._format_jpeg_label = self._ts("JPEG")
        self._format_webp_label = self._ts("WebP")
        self._moderation_low_label = self._ts("Low")
        self._external_services_disabled_text = self._ts(
            "External AI services are disabled. Enable 'Allow external AI services' "
            "in Preferences to generate."
        )
        self._translation_binder.register(self._set_new_layer_label, self._new_layer_label)
        self._translation_binder.register(
            self._set_prompt_required_text,
            self._prompt_required_text,
        )
        self._translation_binder.register(self._set_job_submitted_text, self._job_submitted_text)
        self._translation_binder.register(self._set_auto_label, self._auto_label)
        self._translation_binder.register(self._set_low_label, self._low_label)
        self._translation_binder.register(self._set_medium_label, self._medium_label)
        self._translation_binder.register(self._set_high_label, self._high_label)
        self._translation_binder.register(self._set_opaque_label, self._opaque_label)
        self._translation_binder.register(self._set_format_png_label, self._format_png_label)
        self._translation_binder.register(self._set_format_jpeg_label, self._format_jpeg_label)
        self._translation_binder.register(self._set_format_webp_label, self._format_webp_label)
        self._translation_binder.register(
            self._set_moderation_low_label,
            self._moderation_low_label,
        )
        self._translation_binder.register(
            self._set_external_services_disabled_text,
            self._external_services_disabled_text,
        )
        self._layer_event_tokens: list[tuple[str, object]] = []
        self._layer_unsubscribe_name: str | None = None
        self._layer_event_handler = self._on_layer_event
        self._doc_event_handler = self._on_doc_event
        self._layers_refresh_pending = False
        self.destroyed.connect(self._on_destroyed)

        self._build_ui()
        self._reload_layers()
        self._subscribe_layer_events()
        self._subscribe_language_events()

    def _build_ui(self) -> None:  # noqa: PLR0915 - Qt widget assembly is intentionally linear
        self.setObjectName("GPTImage2Panel")
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        prompt_label_text = self._ts("Prompt:")
        prompt_label = QLabel(prompt_label_text, self)
        prompt_label.setWordWrap(True)
        self._translation_binder.register(prompt_label.setText, prompt_label_text)
        layout.addWidget(prompt_label)

        self._prompt_composer = PromptComposerWidget(host_api=self._host, parent=self)
        self._prompt_composer.composedTextChanged.connect(self._on_composed_prompt_changed)
        self._prompt_composer.modeChanged.connect(self._on_prompt_mode_changed)
        self._prompt_composer.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        layout.addWidget(self._prompt_composer, 1)

        self._prompt_text = QTextEdit(self)
        self._prompt_text.setAcceptRichText(False)
        prompt_placeholder = self._ts("Enter prompt text...")
        self._prompt_text.setPlaceholderText(prompt_placeholder)
        self._translation_binder.register(self._prompt_text.setPlaceholderText, prompt_placeholder)
        self._prompt_text.setFixedHeight(80)
        layout.addWidget(self._prompt_text)

        self._input_layers = InputLayerSelector(MAX_INPUT_LAYERS, self)
        layout.addWidget(self._input_layers)

        opts = QGridLayout()
        opts.setContentsMargins(0, 0, 0, 0)
        opts.setHorizontalSpacing(8)
        opts.setVerticalSpacing(4)
        opts.setColumnStretch(0, 1)
        opts.setColumnStretch(1, 1)

        self._model_combo = QComboBox(self)
        self._configure_compact_combo(self._model_combo, min_chars=10)
        self._model_label = QLabel(self._ts("Model"), self)
        self._translation_binder.register(self._model_label.setText, self._ts("Model"))
        opts.addWidget(self._model_label, 0, 0)
        opts.addWidget(self._model_combo, 1, 0)

        self._quality_combo = QComboBox(self)
        self._configure_compact_combo(self._quality_combo, min_chars=7)
        self._quality_label = QLabel(self._ts("Quality"), self)
        self._translation_binder.register(self._quality_label.setText, self._ts("Quality"))
        opts.addWidget(self._quality_label, 0, 1)
        opts.addWidget(self._quality_combo, 1, 1)

        self._size_combo = QComboBox(self)
        self._configure_compact_combo(self._size_combo, min_chars=10)
        self._size_label = QLabel(self._ts("Size"), self)
        self._translation_binder.register(self._size_label.setText, self._ts("Size"))
        opts.addWidget(self._size_label, 2, 0)
        opts.addWidget(self._size_combo, 3, 0)

        self._format_combo = QComboBox(self)
        self._configure_compact_combo(self._format_combo, min_chars=6)
        self._format_combo.currentIndexChanged.connect(self._update_compression_enabled)
        self._format_label = QLabel(self._ts("Format"), self)
        self._translation_binder.register(self._format_label.setText, self._ts("Format"))
        opts.addWidget(self._format_label, 2, 1)
        opts.addWidget(self._format_combo, 3, 1)

        self._background_combo = QComboBox(self)
        self._configure_compact_combo(self._background_combo, min_chars=7)
        self._background_label = QLabel(self._ts("Background"), self)
        self._translation_binder.register(
            self._background_label.setText,
            self._ts("Background"),
        )
        opts.addWidget(self._background_label, 4, 0)
        opts.addWidget(self._background_combo, 5, 0)

        self._moderation_combo = QComboBox(self)
        self._configure_compact_combo(self._moderation_combo, min_chars=7)
        self._moderation_label = QLabel(self._ts("Moderation"), self)
        self._translation_binder.register(
            self._moderation_label.setText,
            self._ts("Moderation"),
        )
        opts.addWidget(self._moderation_label, 4, 1)
        opts.addWidget(self._moderation_combo, 5, 1)

        self._compression_spin = QSpinBox(self)
        self._compression_spin.setRange(0, 100)
        self._compression_spin.setValue(90)
        self._compression_label = QLabel(self._ts("Compression"), self)
        self._translation_binder.register(
            self._compression_label.setText,
            self._ts("Compression"),
        )
        opts.addWidget(self._compression_label, 6, 0)
        opts.addWidget(self._compression_spin, 7, 0)

        self._target_combo = QComboBox(self)
        self._configure_compact_combo(self._target_combo, min_chars=10)
        self._target_combo.addItem(self._new_layer_label, None)
        self._target_label = QLabel(self._ts("Target layer"), self)
        self._translation_binder.register(self._target_label.setText, self._ts("Target layer"))
        opts.addWidget(self._target_label, 6, 1)
        opts.addWidget(self._target_combo, 7, 1)

        layout.addLayout(opts)

        generate_text = self._ts("Generate")
        self._generate_button = QPushButton(generate_text, self)
        self._translation_binder.register(self._generate_button.setText, generate_text)
        self._generate_button.clicked.connect(self._emit_job)
        self._generate_button.setStyleSheet("""
            QPushButton {
                font-weight: bold;
                padding: 4px 10px;
                background-color: #4CAF50;
                color: white;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666666;
            }
        """)
        self._generate_button.setFixedHeight(28)
        self._generate_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        layout.addWidget(self._generate_button, alignment=Qt.AlignRight)

        self._status = QTextEdit(self)
        self._status.setReadOnly(True)
        status_placeholder = self._ts("Status messages will appear here")
        self._status.setPlaceholderText(status_placeholder)
        self._translation_binder.register(self._status.setPlaceholderText, status_placeholder)
        self._status.setFixedHeight(80)
        layout.addWidget(self._status)

        self._populate_model_combo()
        self._populate_quality_combo()
        self._populate_size_combo()
        self._populate_format_combo()
        self._populate_background_combo()
        self._populate_moderation_combo()
        self._update_compression_enabled()

    def append_status(self, message: str) -> None:
        self._status_entries.append(_StatusEntry(message=message))
        self._status.append(message)

    def append_status_translated(
        self,
        source_text: str,
        *,
        domain: str | None = None,
        **kwargs: Any,
    ) -> None:
        entry = _StatusEntry(
            source_text=source_text,
            domain=domain or "GPTImage2Panel",
            kwargs=dict(kwargs),
        )
        self._status_entries.append(entry)
        self._status.append(self._render_status_entry(entry))

    def _render_status_entry(self, entry: _StatusEntry) -> str:
        if entry.source_text:
            return translate_with_fallback(
                entry.domain or "GPTImage2Panel",
                entry.source_text,
            ).format(**entry.kwargs)
        return entry.message or ""

    def _rerender_status_entries(self) -> None:
        self._status.clear()
        for entry in self._status_entries:
            self._status.append(self._render_status_entry(entry))

    def _on_composed_prompt_changed(self, text: str) -> None:
        self._prompt_text.blockSignals(True)
        self._prompt_text.setPlainText(text)
        self._prompt_text.blockSignals(False)

    def _on_prompt_mode_changed(self, composer_active: bool) -> None:
        self._prompt_text.setVisible(not composer_active)

    def _reload_layers(self) -> None:
        previous_target = (
            self._target_combo.currentData() if self._target_combo.count() > 0 else None
        )
        canvas = getattr(self._host, "canvas", None)
        layers = []
        if canvas and hasattr(canvas, "get_layers"):
            try:
                layers = canvas.get_layers()
            except Exception:
                layers = []

        self._input_layers.set_choices(
            [(layer.id, str(layer.name or layer.id)) for layer in layers]
        )
        self._target_layer_choices = []
        for layer in layers:
            layer_id = layer.id
            is_vector = getattr(layer, "layer_type", None) == "vector"
            if is_vector or self._is_vector_layer_id(layer_id):
                continue
            display = f"{layer.name or layer.id}"
            self._target_layer_choices.append((layer_id, display))

        self._target_combo.blockSignals(True)
        self._target_combo.clear()
        self._target_combo.addItem(self._new_layer_label, None)
        for layer_id, display in self._target_layer_choices:
            self._target_combo.addItem(display, layer_id)
        restore_index = (
            self._target_combo.findData(previous_target)
            if previous_target is not None
            else self._target_combo.findData(None)
        )
        self._target_combo.setCurrentIndex(max(restore_index, 0))
        self._target_combo.blockSignals(False)

    def _is_vector_layer_id(self, layer_id: str) -> bool:
        if not layer_id:
            return False
        canvas = getattr(self._host, "canvas", None)
        layers = getattr(canvas, "_layers", None) if canvas else None
        if not isinstance(layers, list):
            return False
        for layer in layers:
            if getattr(layer, "id", None) == layer_id:
                return getattr(layer, "layer_type", "raster") == "vector"
        return False

    def _subscribe_layer_events(self) -> None:
        events = getattr(self._host, "events", None)
        if not events:
            return

        subscribe = getattr(events, "on", None)
        unsubscribe_name = "off"
        if not callable(subscribe):
            subscribe = getattr(events, "subscribe", None)
            unsubscribe_name = "unsubscribe"
        if not callable(subscribe):
            logger.debug("GPTImage2Panel: host event bus lacks subscription API")
            return

        self._unsubscribe_layer_events()
        for event_name in (
            Events.LAYER_ADDED,
            Events.LAYER_REMOVED,
            Events.LAYER_CHANGED,
            Events.LAYER_SELECTED,
        ):
            try:
                token = subscribe(event_name, self._layer_event_handler)
            except Exception:
                logger.debug(
                    "GPTImage2Panel: failed to subscribe to %s",
                    event_name,
                    exc_info=True,
                )
            else:
                self._layer_event_tokens.append((event_name, token))
        try:
            token = subscribe(Events.DOC_CHANGED, self._doc_event_handler)
        except Exception:
            logger.debug(
                "GPTImage2Panel: failed to subscribe to %s",
                Events.DOC_CHANGED,
                exc_info=True,
            )
        else:
            self._layer_event_tokens.append((Events.DOC_CHANGED, token))
        if self._layer_event_tokens:
            self._layer_unsubscribe_name = unsubscribe_name

    def _unsubscribe_layer_events(self) -> None:
        if not self._layer_event_tokens:
            return

        events = getattr(self._host, "events", None)
        if not events:
            self._layer_event_tokens.clear()
            self._layer_unsubscribe_name = None
            return

        off = None
        if self._layer_unsubscribe_name:
            off = getattr(events, self._layer_unsubscribe_name, None)
        if not callable(off):
            off = getattr(events, "off", None)
        if not callable(off):
            off = getattr(events, "unsubscribe", None)
        if not callable(off):
            self._layer_event_tokens.clear()
            self._layer_unsubscribe_name = None
            return

        for event_name, token in self._layer_event_tokens:
            try:
                off(event_name, token)
            except Exception:
                logger.debug(
                    "GPTImage2Panel: failed to unsubscribe from %s",
                    event_name,
                    exc_info=True,
                )
        self._layer_event_tokens.clear()
        self._layer_unsubscribe_name = None

    def _on_layer_event(self, _payload) -> None:
        self._schedule_layers_refresh()

    def _on_doc_event(self, payload: Any) -> None:
        if not self._should_refresh_layers_from_doc(payload):
            return
        self._schedule_layers_refresh()

    def _should_refresh_layers_from_doc(self, payload: Any) -> bool:
        change_type: str | None = None
        if isinstance(payload, DocumentChangeEvent):
            change_type = payload.change_type
        elif isinstance(payload, dict):
            raw = payload.get("change_type") or payload.get("type")
            if isinstance(raw, str):
                change_type = raw
        if not change_type:
            return False
        return change_type in {"replace_layers", "new_document"}

    def _schedule_layers_refresh(self) -> None:
        if self._layers_refresh_pending:
            return
        self._layers_refresh_pending = True

        if QThread.currentThread() == self.thread():
            QTimer.singleShot(0, self._perform_layers_refresh)
            return

        invoked = QMetaObject.invokeMethod(
            self,
            "_perform_layers_refresh",
            Qt.ConnectionType.QueuedConnection,
        )
        if not invoked:
            QTimer.singleShot(0, self._perform_layers_refresh)

    @Slot()
    def _perform_layers_refresh(self) -> None:
        if not is_valid(self):
            return
        self._layers_refresh_pending = False
        self._reload_layers()

    def _on_destroyed(self, _obj=None) -> None:
        self._unsubscribe_layer_events()
        self._unsubscribe_language_events()

    def _emit_job(self) -> None:
        if not self._is_external_services_allowed():
            self.append_status_translated(
                "External AI services are disabled. Enable 'Allow external AI services' "
                "in Preferences to generate."
            )
            return

        prompt = (
            self._prompt_composer.resolve_for_generation()
            if self._prompt_composer.is_composer_active()
            else self._prompt_text.toPlainText()
        ).strip()
        if not prompt:
            self.append_status_translated("Prompt is required")
            return

        output_format = self._format_combo.currentData() or "png"
        compression = self._compression_spin.value() if output_format in {"jpeg", "webp"} else None
        options = GPTImage2Options(
            target_layer_id=self._target_combo.currentData(),
            model=self._model_combo.currentData() or DEFAULT_MODEL,
            size=self._size_combo.currentData() or "1024x1024",
            quality=self._quality_combo.currentData() or "auto",
            output_format=output_format,
            output_compression=compression,
            background=self._background_combo.currentData() or "auto",
            moderation=self._moderation_combo.currentData() or "auto",
        )
        spec = JobSpec(
            op="generate",
            params={
                "prompt": prompt,
                "input_layer_ids": self._input_layers.selected(),
                "options": options.as_mapping(),
            },
        )
        self.submitRequested.emit(spec)
        self.append_status_translated("Submitted job to queue")

    @staticmethod
    def _configure_compact_combo(combo: QComboBox, *, min_chars: int) -> None:
        combo.setMinimumWidth(0)
        combo.setMinimumContentsLength(min_chars)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)

    def _populate_model_combo(self) -> None:
        current = self._model_combo.currentData() if self._model_combo.count() > 0 else None
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        for model_id in ALL_MODELS:
            self._model_combo.addItem(MODEL_DISPLAY_NAMES.get(model_id, model_id), model_id)
        restore = current if isinstance(current, str) else DEFAULT_MODEL
        self._model_combo.setCurrentIndex(max(self._model_combo.findData(restore), 0))
        self._model_combo.blockSignals(False)

    def _populate_quality_combo(self) -> None:
        current = self._quality_combo.currentData() if self._quality_combo.count() > 0 else None
        labels = {
            "auto": self._auto_label,
            "low": self._low_label,
            "medium": self._medium_label,
            "high": self._high_label,
        }
        self._quality_combo.blockSignals(True)
        self._quality_combo.clear()
        for value in ALL_QUALITIES:
            self._quality_combo.addItem(labels[value], value)
        restore = current if isinstance(current, str) else "auto"
        self._quality_combo.setCurrentIndex(max(self._quality_combo.findData(restore), 0))
        self._quality_combo.blockSignals(False)

    def _populate_size_combo(self) -> None:
        current = self._size_combo.currentData() if self._size_combo.count() > 0 else None
        self._size_combo.blockSignals(True)
        self._size_combo.clear()
        for value in ALL_SIZES:
            label = self._auto_label if value == "auto" else value
            self._size_combo.addItem(label, value)
        restore = current if isinstance(current, str) else "1024x1024"
        self._size_combo.setCurrentIndex(max(self._size_combo.findData(restore), 0))
        self._size_combo.blockSignals(False)

    def _populate_format_combo(self) -> None:
        current = self._format_combo.currentData() if self._format_combo.count() > 0 else None
        labels = {
            "png": self._format_png_label,
            "jpeg": self._format_jpeg_label,
            "webp": self._format_webp_label,
        }
        self._format_combo.blockSignals(True)
        self._format_combo.clear()
        for value in ALL_OUTPUT_FORMATS:
            self._format_combo.addItem(labels[value], value)
        restore = current if isinstance(current, str) else "png"
        self._format_combo.setCurrentIndex(max(self._format_combo.findData(restore), 0))
        self._format_combo.blockSignals(False)

    def _populate_background_combo(self) -> None:
        current = (
            self._background_combo.currentData() if self._background_combo.count() > 0 else None
        )
        labels = {
            "auto": self._auto_label,
            "opaque": self._opaque_label,
        }
        self._background_combo.blockSignals(True)
        self._background_combo.clear()
        for value in ALL_BACKGROUNDS:
            self._background_combo.addItem(labels[value], value)
        restore = current if isinstance(current, str) else "auto"
        self._background_combo.setCurrentIndex(max(self._background_combo.findData(restore), 0))
        self._background_combo.blockSignals(False)

    def _populate_moderation_combo(self) -> None:
        current = (
            self._moderation_combo.currentData() if self._moderation_combo.count() > 0 else None
        )
        labels = {
            "auto": self._auto_label,
            "low": self._moderation_low_label,
        }
        self._moderation_combo.blockSignals(True)
        self._moderation_combo.clear()
        for value in ALL_MODERATION:
            self._moderation_combo.addItem(labels[value], value)
        restore = current if isinstance(current, str) else "auto"
        self._moderation_combo.setCurrentIndex(max(self._moderation_combo.findData(restore), 0))
        self._moderation_combo.blockSignals(False)

    def _refresh_target_combo_label(self) -> None:
        index = self._target_combo.findData(None)
        if index >= 0:
            self._target_combo.setItemText(index, self._new_layer_label)

    def _update_compression_enabled(self) -> None:
        enabled = self._format_combo.currentData() in {"jpeg", "webp"}
        self._compression_spin.setEnabled(enabled)
        self._compression_label.setEnabled(enabled)

    def _subscribe_language_events(self) -> None:
        if self._translation_tokens:
            return
        handler = self._on_language_changed
        self._translation_handlers.append(handler)
        subscribe = getattr(self._event_bus, "on", None) or getattr(
            self._event_bus,
            "subscribe",
            None,
        )
        if callable(subscribe):
            token = subscribe(Events.LANGUAGE_CHANGED, handler)
            self._translation_tokens.append(token)

    def _unsubscribe_language_events(self) -> None:
        if not self._translation_tokens:
            return
        unsubscribe = getattr(self._event_bus, "off", None) or getattr(
            self._event_bus,
            "unsubscribe",
            None,
        )
        if not callable(unsubscribe):
            self._translation_tokens.clear()
            self._translation_handlers.clear()
            return
        for token in self._translation_tokens:
            event_name = getattr(token, "event", None) or Events.LANGUAGE_CHANGED
            try:
                unsubscribe(event_name, token)
            except Exception:
                logger.debug(
                    "GPTImage2Panel: failed to unsubscribe from %s",
                    event_name,
                    exc_info=True,
                )
        self._translation_tokens.clear()
        self._translation_handlers.clear()

    def _on_language_changed(self, _payload: Any) -> None:
        if not is_valid(self):
            return
        with contextlib.suppress(RuntimeError):
            self.refresh_translations()

    def refresh_translations(self) -> None:
        if not is_valid(self):
            return
        self._translation_binder.set_translator(self.tr)
        self._translation_binder.retranslate()
        self._input_layers.refresh_translations()
        self._populate_model_combo()
        self._populate_quality_combo()
        self._populate_size_combo()
        self._populate_format_combo()
        self._populate_background_combo()
        self._populate_moderation_combo()
        self._refresh_target_combo_label()
        self._update_compression_enabled()
        self._rerender_status_entries()

    def _set_new_layer_label(self, text: str) -> None:
        self._new_layer_label = text

    def _set_prompt_required_text(self, text: str) -> None:
        self._prompt_required_text = text

    def _set_job_submitted_text(self, text: str) -> None:
        self._job_submitted_text = text

    def _set_auto_label(self, text: str) -> None:
        self._auto_label = text

    def _set_low_label(self, text: str) -> None:
        self._low_label = text

    def _set_medium_label(self, text: str) -> None:
        self._medium_label = text

    def _set_high_label(self, text: str) -> None:
        self._high_label = text

    def _set_opaque_label(self, text: str) -> None:
        self._opaque_label = text

    def _set_format_png_label(self, text: str) -> None:
        self._format_png_label = text

    def _set_format_jpeg_label(self, text: str) -> None:
        self._format_jpeg_label = text

    def _set_format_webp_label(self, text: str) -> None:
        self._format_webp_label = text

    def _set_moderation_low_label(self, text: str) -> None:
        self._moderation_low_label = text

    def _set_external_services_disabled_text(self, text: str) -> None:
        self._external_services_disabled_text = text

    def _is_external_services_allowed(self) -> bool:
        settings = getattr(self._host, "settings", self._host)
        return is_external_services_allowed(settings)

    def _ts(self, text: str) -> str:
        from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

        return translated_literal_domain("GPTImage2Panel", text, use_fallback=True)

    def changeEvent(self, event: QEvent) -> None:
        if not is_valid(self):
            return
        with contextlib.suppress(RuntimeError):
            super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange and self.isVisible():
            self.refresh_translations()
