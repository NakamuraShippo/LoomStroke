"""Qt panel for configuring NanoBanana jobs."""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from functools import partial
from typing import Any

from PySide6.QtCore import QEvent, QMetaObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.event_bus_extensions import get_extended_event_bus
from loomstroke.core.events import DocumentChangeEvent, Events
from loomstroke.plugins.comfyui.ui.prompt_composer import PromptComposerWidget
from loomstroke.plugins.common.outbound_policy import is_external_services_allowed
from loomstroke.plugins.sdk.base import JobSpec
from loomstroke.ui._dock_safe import is_valid
from loomstroke.ui.translations import TranslationBinder, translate_with_fallback

from ..types import (
    ALL_MODELS,
    ASPECT_TO_RESOLUTION,
    DEFAULT_MODEL,
    MAX_INPUT_LAYERS,
    MODEL_DISPLAY_NAMES,
    NanoBananaOptions,
)

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _StatusEntry:
    message: str | None = None
    source_text: str | None = None
    domain: str | None = None
    kwargs: dict[str, Any] = field(default_factory=dict)


class NanoBananaPanel(QWidget):
    """Simple configuration panel for NanoBanana image generation."""

    submitRequested = Signal(JobSpec)

    def __init__(self, host, *, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._host = host

        self._layer_choices: list[tuple[str, str]] = []
        self._target_layer_choices: list[tuple[str, str]] = []
        self._layer_labels: list[QLabel] = []
        self._layer_selectors: list[QComboBox] = []
        self._status_entries: list[_StatusEntry] = []
        self._translation_binder = TranslationBinder(self.tr)
        self._event_bus = get_extended_event_bus()
        self._translation_handlers: list[Any] = []
        self._translation_tokens: list[Any] = []
        self._input_layers_label: QLabel | None = None
        self._none_option_label = self._ts("Nothing")
        self._new_layer_label = self._ts("<New Layer>")
        self._input_layers_template = self._ts("Input Layers (select up to {count}):")
        self._prompt_required_text = self._ts("Prompt is required")
        self._job_submitted_text = self._ts("Submitted job to queue")
        self._modality_text_image = self._ts("Text+Image")
        self._modality_image = self._ts("Image")
        self._aspect_item_template = self._ts("{aspect} ({width}x{height})")
        self._image_size_auto = self._ts("Auto")
        self._thinking_minimal = self._ts("Minimal")
        self._thinking_high = self._ts("High")
        self._include_thoughts_text = self._ts("Thoughts")
        self._google_search_text = self._ts("Search")
        self._external_services_disabled_text = self._ts(
            "External AI services are disabled. Enable 'Allow external AI services' "
            "in Preferences to generate."
        )
        self._translation_binder.register(self._set_none_option_label, self._none_option_label)
        self._translation_binder.register(self._set_new_layer_label, self._new_layer_label)
        self._translation_binder.register(
            self._set_input_layers_template, self._input_layers_template
        )
        self._translation_binder.register(
            self._set_prompt_required_text, self._prompt_required_text
        )
        self._translation_binder.register(self._set_job_submitted_text, self._job_submitted_text)
        self._translation_binder.register(self._set_modality_text_image, self._modality_text_image)
        self._translation_binder.register(self._set_modality_image, self._modality_image)
        self._translation_binder.register(
            self._set_aspect_item_template, self._aspect_item_template
        )
        self._translation_binder.register(self._set_image_size_auto, self._image_size_auto)
        self._translation_binder.register(self._set_thinking_minimal, self._thinking_minimal)
        self._translation_binder.register(self._set_thinking_high, self._thinking_high)
        self._translation_binder.register(
            self._set_include_thoughts_text, self._include_thoughts_text
        )
        self._translation_binder.register(self._set_google_search_text, self._google_search_text)
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

    # ------------------------------------------------------------------
    def _build_ui(self) -> None:  # noqa: PLR0915
        self.setObjectName("NanoBananaPanel")
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Prompt composition
        prompt_label_text = self._ts("Prompt:")
        prompt_label = QLabel(prompt_label_text, self)
        prompt_label.setWordWrap(True)
        self._translation_binder.register(prompt_label.setText, prompt_label_text)
        layout.addWidget(prompt_label)

        self._prompt_composer = PromptComposerWidget(host_api=self._host, parent=self)
        self._prompt_composer.composedTextChanged.connect(self._on_composed_prompt_changed)
        self._prompt_composer.modeChanged.connect(self._on_prompt_mode_changed)
        self._prompt_composer.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self._prompt_composer, 1)

        self._prompt_text = QTextEdit(self)
        self._prompt_text.setAcceptRichText(False)
        prompt_placeholder = self._ts("Enter prompt text...")
        self._prompt_text.setPlaceholderText(prompt_placeholder)
        self._translation_binder.register(self._prompt_text.setPlaceholderText, prompt_placeholder)
        self._prompt_text.setFixedHeight(80)
        layout.addWidget(self._prompt_text)

        # Input layers
        self._input_layers_label = QLabel("", self)
        self._input_layers_label.setWordWrap(True)
        layout.addWidget(self._input_layers_label)
        self._refresh_input_layers_label()
        self._layer_grid_container = QWidget(self)
        self._layer_grid = QGridLayout(self._layer_grid_container)
        self._layer_grid.setContentsMargins(0, 0, 0, 0)
        self._layer_grid.setHorizontalSpacing(6)
        self._layer_grid.setVerticalSpacing(6)
        self._layer_grid.setColumnStretch(1, 1)

        for index in range(MAX_INPUT_LAYERS):
            label = QLabel(str(index + 1), self._layer_grid_container)
            combo = QComboBox(self._layer_grid_container)
            self._configure_compact_combo(combo, min_chars=6)
            combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            combo.addItem(self._none_option_label, None)
            combo.currentIndexChanged.connect(partial(self._on_layer_selector_changed, index))
            row = index
            self._layer_grid.addWidget(label, row, 0)
            self._layer_grid.addWidget(combo, row, 1)
            label.setVisible(index == 0)
            combo.setVisible(index == 0)
            self._layer_labels.append(label)
            self._layer_selectors.append(combo)

        layout.addWidget(self._layer_grid_container)

        # Options grid (2-column layout)
        opts = QGridLayout()
        opts.setContentsMargins(0, 0, 0, 0)
        opts.setHorizontalSpacing(8)
        opts.setVerticalSpacing(4)
        opts.setColumnStretch(0, 1)
        opts.setColumnStretch(1, 1)

        # Row 0: Model | Thinking level
        self._model_combo = QComboBox(self)
        self._configure_compact_combo(self._model_combo, min_chars=6)
        self._populate_model_combo()
        self._model_label = QLabel(self._ts("Model"), self)
        self._translation_binder.register(self._model_label.setText, self._ts("Model"))
        opts.addWidget(self._model_label, 0, 0)
        opts.addWidget(self._model_combo, 1, 0)

        self._thinking_combo = QComboBox(self)
        self._configure_compact_combo(self._thinking_combo, min_chars=6)
        self._populate_thinking_combo()
        self._thinking_label = QLabel(self._ts("Thinking"), self)
        self._translation_binder.register(self._thinking_label.setText, self._ts("Thinking"))
        opts.addWidget(self._thinking_label, 0, 1)
        opts.addWidget(self._thinking_combo, 1, 1)

        # Row 1: Response modality | Image size
        self._modality_combo = QComboBox(self)
        self._configure_compact_combo(self._modality_combo, min_chars=6)
        self._populate_modality_combo()
        self._modality_label = QLabel(self._ts("Modality"), self)
        self._translation_binder.register(self._modality_label.setText, self._ts("Modality"))
        opts.addWidget(self._modality_label, 2, 0)
        opts.addWidget(self._modality_combo, 3, 0)

        self._image_size_combo = QComboBox(self)
        self._configure_compact_combo(self._image_size_combo, min_chars=6)
        self._populate_image_size_combo()
        self._image_size_label = QLabel(self._ts("Size"), self)
        self._translation_binder.register(self._image_size_label.setText, self._ts("Size"))
        opts.addWidget(self._image_size_label, 2, 1)
        opts.addWidget(self._image_size_combo, 3, 1)

        # Row 2: Aspect ratio (full width)
        self._aspect_combo = QComboBox(self)
        self._configure_compact_combo(self._aspect_combo, min_chars=10)
        self._populate_aspect_combo()
        self._aspect_label = QLabel(self._ts("Aspect ratio"), self)
        self._translation_binder.register(self._aspect_label.setText, self._ts("Aspect ratio"))
        opts.addWidget(self._aspect_label, 4, 0, 1, 2)
        opts.addWidget(self._aspect_combo, 5, 0, 1, 2)

        # Row 3: Target layer (full width)
        self._target_combo = QComboBox(self)
        self._configure_compact_combo(self._target_combo, min_chars=10)
        self._target_combo.addItem(self._new_layer_label, None)
        self._target_label = QLabel(self._ts("Target layer"), self)
        self._translation_binder.register(self._target_label.setText, self._ts("Target layer"))
        opts.addWidget(self._target_label, 6, 0, 1, 2)
        opts.addWidget(self._target_combo, 7, 0, 1, 2)

        layout.addLayout(opts)

        # Checkboxes (horizontal row)
        check_row = QHBoxLayout()
        check_row.setContentsMargins(0, 0, 0, 0)
        check_row.setSpacing(12)

        self._include_thoughts_check = QCheckBox(self._include_thoughts_text, self)
        self._translation_binder.register(
            self._include_thoughts_check.setText, self._include_thoughts_text
        )
        check_row.addWidget(self._include_thoughts_check)

        self._google_search_check = QCheckBox(self._google_search_text, self)
        self._translation_binder.register(
            self._google_search_check.setText, self._google_search_text
        )
        check_row.addWidget(self._google_search_check)
        check_row.addStretch(1)

        layout.addLayout(check_row)

        # Generate button
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

        # Status log
        self._status = QTextEdit(self)
        self._status.setReadOnly(True)
        status_placeholder = self._ts("Status messages will appear here")
        self._status.setPlaceholderText(status_placeholder)
        self._translation_binder.register(self._status.setPlaceholderText, status_placeholder)
        self._status.setFixedHeight(80)
        layout.addWidget(self._status)

    # ------------------------------------------------------------------
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
            domain=domain or "NanoBananaPanel",
            kwargs=dict(kwargs),
        )
        self._status_entries.append(entry)
        self._status.append(self._render_status_entry(entry))

    def _render_status_entry(self, entry: _StatusEntry) -> str:
        if entry.source_text:
            return translate_with_fallback(
                entry.domain or "NanoBananaPanel",
                entry.source_text,
            ).format(**entry.kwargs)
        return entry.message or ""

    def _rerender_status_entries(self) -> None:
        self._status.clear()
        for entry in self._status_entries:
            self._status.append(self._render_status_entry(entry))

    # ------------------------------------------------------------------
    def _on_composed_prompt_changed(self, text: str) -> None:
        """Update the hidden text widget when the composer emits new text."""
        self._prompt_text.blockSignals(True)
        self._prompt_text.setPlainText(text)
        self._prompt_text.blockSignals(False)

    def _on_prompt_mode_changed(self, composer_active: bool) -> None:
        """Toggle between composer mode and flat-text mode."""
        self._prompt_text.setVisible(not composer_active)

    # ------------------------------------------------------------------

    def _reload_layers(self) -> None:
        previous_selection = self._gather_layer_selection()
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

        self._layer_choices = []
        self._target_layer_choices = []
        for layer in layers:
            display = f"{layer.name or layer.id}"
            layer_id = layer.id
            self._layer_choices.append((layer_id, display))
            is_vector = getattr(layer, "layer_type", None) == "vector"
            if not is_vector and not self._is_vector_layer_id(layer_id):
                self._target_layer_choices.append((layer_id, display))

        self._target_combo.blockSignals(True)
        self._target_combo.clear()
        self._target_combo.addItem(self._new_layer_label, None)
        for layer_id, display in self._target_layer_choices:
            self._target_combo.addItem(display, layer_id)

        if previous_target is not None:
            restore_index = self._target_combo.findData(previous_target)
        else:
            restore_index = self._target_combo.findData(None)
        restore_index = max(restore_index, 0)
        self._target_combo.setCurrentIndex(restore_index)
        self._target_combo.blockSignals(False)

        self._refresh_layer_selector_models()
        self._set_layer_selector_values(previous_selection)

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

    def _refresh_layer_selector_models(self) -> None:
        for combo in self._layer_selectors:
            combo.blockSignals(True)
            combo.clear()
            combo.addItem(self._none_option_label, None)
            for layer_id, display in self._layer_choices:
                combo.addItem(display, layer_id)
            combo.blockSignals(False)

    def _set_layer_selector_values(self, layer_ids: list[str]) -> None:
        valid_ids = {layer_id for layer_id, _ in self._layer_choices}
        ordered: list[str] = []
        for layer_id in layer_ids:
            if layer_id in valid_ids and layer_id not in ordered:
                ordered.append(layer_id)
            if len(ordered) >= MAX_INPUT_LAYERS:
                break
        for index, combo in enumerate(self._layer_selectors):
            combo.blockSignals(True)
            target_id = ordered[index] if index < len(ordered) else None
            target_idx = combo.findData(target_id) if target_id is not None else 0
            target_idx = max(target_idx, 0)
            combo.setCurrentIndex(target_idx)
            combo.blockSignals(False)
        self._update_layer_selector_visibility()

    def _gather_layer_selection(self) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for combo in self._layer_selectors:
            layer_id = combo.currentData()
            if layer_id and layer_id not in seen:
                ordered.append(layer_id)
                seen.add(layer_id)
        return ordered

    def _on_layer_selector_changed(self, _slot_index: int, _index: int) -> None:
        # Compacts selections whenever a slot changes and avoids duplicates.
        self._normalize_layer_selection()

    def _normalize_layer_selection(self) -> None:
        current = self._gather_layer_selection()
        self._set_layer_selector_values(current)

    def _update_layer_selector_visibility(self) -> None:
        selections = self._gather_layer_selection()
        visible_slots = min(len(selections) + 1, MAX_INPUT_LAYERS) if selections else 1
        for index, (label, combo) in enumerate(
            zip(self._layer_labels, self._layer_selectors, strict=False)
        ):
            is_visible = index < visible_slots
            label.setVisible(is_visible)
            combo.setVisible(is_visible)

    # ------------------------------------------------------------------

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
            logger.debug("NanoBananaPanel: host event bus lacks subscription API")
            return

        self._unsubscribe_layer_events()
        for event_name in (
            Events.LAYER_ADDED,
            Events.LAYER_REMOVED,
            Events.LAYER_CHANGED,
            Events.LAYER_SELECTED,
        ):
            try:
                handler = self._layer_event_handler
                token = subscribe(event_name, handler)
            except Exception:
                logger.debug(
                    "NanoBananaPanel: failed to subscribe to %s",
                    event_name,
                    exc_info=True,
                )
            else:
                self._layer_event_tokens.append((event_name, token))
        try:
            token = subscribe(Events.DOC_CHANGED, self._doc_event_handler)
        except Exception:
            logger.debug(
                "NanoBananaPanel: failed to subscribe to %s",
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
                    "NanoBananaPanel: failed to unsubscribe from %s",
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

        layer_ids = self._gather_layer_selection()
        if len(layer_ids) > MAX_INPUT_LAYERS:
            layer_ids = layer_ids[:MAX_INPUT_LAYERS]

        model = self._model_combo.currentData() or DEFAULT_MODEL
        modality = self._modality_combo.currentData()
        if not isinstance(modality, str):
            modality = "Text+Image"
        aspect = self._aspect_combo.currentData() or "1:1"
        image_size = self._image_size_combo.currentData()  # None = auto
        thinking_level = self._thinking_combo.currentData() or "minimal"
        include_thoughts = self._include_thoughts_check.isChecked()
        google_search = self._google_search_check.isChecked()
        target_layer_id = self._target_combo.currentData()

        options = NanoBananaOptions(
            modality=modality if modality in ("Image", "Text+Image") else "Text+Image",
            aspect_ratio=aspect,
            target_layer_id=target_layer_id,
            model=model,
            image_size=image_size,
            thinking_level=thinking_level,
            include_thoughts=include_thoughts,
            google_search=google_search,
        )
        params = {
            "prompt": prompt,
            "input_layer_ids": layer_ids,
            "options": {
                "modality": options.modality,
                "aspect_ratio": options.aspect_ratio,
                "target_layer_id": options.target_layer_id,
                "model": options.model,
                "image_size": options.image_size,
                "thinking_level": options.thinking_level,
                "include_thoughts": options.include_thoughts,
                "google_search": options.google_search,
            },
        }
        spec = JobSpec(op="generate", params=params)
        self.submitRequested.emit(spec)
        self.append_status_translated("Submitted job to queue")

    @staticmethod
    def _configure_compact_combo(combo: QComboBox, *, min_chars: int) -> None:
        combo.setMinimumWidth(0)
        combo.setMinimumContentsLength(min_chars)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)

    def _populate_modality_combo(self) -> None:
        if not hasattr(self, "_modality_combo"):
            return
        current = self._modality_combo.currentData() if self._modality_combo.count() > 0 else None
        self._modality_combo.blockSignals(True)
        self._modality_combo.clear()
        self._modality_combo.addItem(self._modality_text_image, "Text+Image")
        self._modality_combo.addItem(self._modality_image, "Image")
        restore = current if isinstance(current, str) else "Text+Image"
        restore_index = self._modality_combo.findData(restore)
        restore_index = max(restore_index, 0)
        self._modality_combo.setCurrentIndex(restore_index)
        self._modality_combo.blockSignals(False)

    def _populate_model_combo(self) -> None:
        if not hasattr(self, "_model_combo"):
            return
        current = self._model_combo.currentData() if self._model_combo.count() > 0 else None
        self._model_combo.blockSignals(True)
        self._model_combo.clear()
        for model_id in ALL_MODELS:
            display = MODEL_DISPLAY_NAMES.get(model_id, model_id)
            self._model_combo.addItem(display, model_id)
        restore = current if isinstance(current, str) else DEFAULT_MODEL
        restore_index = self._model_combo.findData(restore)
        restore_index = max(restore_index, 0)
        self._model_combo.setCurrentIndex(restore_index)
        self._model_combo.blockSignals(False)

    def _populate_aspect_combo(self) -> None:
        if not hasattr(self, "_aspect_combo"):
            return
        current = self._aspect_combo.currentData() if self._aspect_combo.count() > 0 else None
        self._aspect_combo.blockSignals(True)
        self._aspect_combo.clear()
        for aspect, res in ASPECT_TO_RESOLUTION.items():
            label = self._aspect_item_template.format(aspect=aspect, width=res[0], height=res[1])
            self._aspect_combo.addItem(label, aspect)
        restore = current if isinstance(current, str) else "1:1"
        restore_index = self._aspect_combo.findData(restore)
        restore_index = max(restore_index, 0)
        self._aspect_combo.setCurrentIndex(restore_index)
        self._aspect_combo.blockSignals(False)

    def _populate_image_size_combo(self) -> None:
        if not hasattr(self, "_image_size_combo"):
            return
        current = (
            self._image_size_combo.currentData() if self._image_size_combo.count() > 0 else None
        )
        self._image_size_combo.blockSignals(True)
        self._image_size_combo.clear()
        self._image_size_combo.addItem(self._image_size_auto, None)
        for size in ("512", "1K", "2K", "4K"):
            self._image_size_combo.addItem(size, size)
        restore_index = self._image_size_combo.findData(current) if current else 0
        restore_index = max(restore_index, 0)
        self._image_size_combo.setCurrentIndex(restore_index)
        self._image_size_combo.blockSignals(False)

    def _populate_thinking_combo(self) -> None:
        if not hasattr(self, "_thinking_combo"):
            return
        current = self._thinking_combo.currentData() if self._thinking_combo.count() > 0 else None
        self._thinking_combo.blockSignals(True)
        self._thinking_combo.clear()
        self._thinking_combo.addItem(self._thinking_minimal, "minimal")
        self._thinking_combo.addItem(self._thinking_high, "High")
        restore = current if isinstance(current, str) else "minimal"
        restore_index = self._thinking_combo.findData(restore)
        restore_index = max(restore_index, 0)
        self._thinking_combo.setCurrentIndex(restore_index)
        self._thinking_combo.blockSignals(False)

    def _refresh_input_layers_label(self) -> None:
        if self._input_layers_label is None:
            return
        self._input_layers_label.setText(self._input_layers_template.format(count=MAX_INPUT_LAYERS))

    def _refresh_layer_selector_labels(self) -> None:
        for combo in self._layer_selectors:
            index = combo.findData(None)
            if index >= 0:
                combo.setItemText(index, self._none_option_label)

    def _refresh_target_combo_label(self) -> None:
        if not hasattr(self, "_target_combo"):
            return
        index = self._target_combo.findData(None)
        if index >= 0:
            self._target_combo.setItemText(index, self._new_layer_label)

    def _subscribe_language_events(self) -> None:
        if self._translation_tokens:
            return
        handler = self._on_language_changed
        self._translation_handlers.append(handler)
        subscribe = getattr(self._event_bus, "on", None) or getattr(
            self._event_bus, "subscribe", None
        )
        if callable(subscribe):
            token = subscribe(Events.LANGUAGE_CHANGED, handler)
            self._translation_tokens.append(token)

    def _unsubscribe_language_events(self) -> None:
        if not self._translation_tokens:
            return
        unsubscribe = getattr(self._event_bus, "off", None) or getattr(
            self._event_bus, "unsubscribe", None
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
                    "NanoBananaPanel: failed to unsubscribe from %s",
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
        self._refresh_input_layers_label()
        self._populate_model_combo()
        self._populate_modality_combo()
        self._populate_aspect_combo()
        self._populate_image_size_combo()
        self._populate_thinking_combo()
        self._refresh_layer_selector_labels()
        self._refresh_target_combo_label()
        self._rerender_status_entries()

    def _set_none_option_label(self, text: str) -> None:
        self._none_option_label = text

    def _set_new_layer_label(self, text: str) -> None:
        self._new_layer_label = text

    def _set_input_layers_template(self, text: str) -> None:
        self._input_layers_template = text

    def _set_prompt_required_text(self, text: str) -> None:
        self._prompt_required_text = text

    def _set_job_submitted_text(self, text: str) -> None:
        self._job_submitted_text = text

    def _set_modality_text_image(self, text: str) -> None:
        self._modality_text_image = text

    def _set_modality_image(self, text: str) -> None:
        self._modality_image = text

    def _set_aspect_item_template(self, text: str) -> None:
        self._aspect_item_template = text

    def _set_image_size_auto(self, text: str) -> None:
        self._image_size_auto = text

    def _set_thinking_minimal(self, text: str) -> None:
        self._thinking_minimal = text

    def _set_thinking_high(self, text: str) -> None:
        self._thinking_high = text

    def _set_include_thoughts_text(self, text: str) -> None:
        self._include_thoughts_text = text

    def _set_google_search_text(self, text: str) -> None:
        self._google_search_text = text

    def _set_external_services_disabled_text(self, text: str) -> None:
        self._external_services_disabled_text = text

    def _is_external_services_allowed(self) -> bool:
        settings = getattr(self._host, "settings", self._host)
        return is_external_services_allowed(settings)

    def _ts(self, text: str) -> str:
        from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

        return translated_literal_domain("NanoBananaPanel", text, use_fallback=True)

    def changeEvent(self, event: QEvent) -> None:
        if not is_valid(self):
            return
        with contextlib.suppress(RuntimeError):
            super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange and self.isVisible():
            self.refresh_translations()
