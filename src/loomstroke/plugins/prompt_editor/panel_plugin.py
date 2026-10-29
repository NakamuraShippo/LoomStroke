from __future__ import annotations

import asyncio
import contextlib
import logging
import sqlite3  # noqa: F401 — used in except clauses
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from PySide6.QtCore import QEvent, QMimeData, QModelIndex, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (  # noqa: F401
    QCloseEvent,
    QIcon,
    QKeySequence,
    QShortcut,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QStyle,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.core.event_bus import EventToken
from loomstroke.core.event_bus_extensions import ExtendedEventBus, get_extended_event_bus
from loomstroke.core.events import Events
from loomstroke.host_api import HostAPI
from loomstroke.platform.async_task_owner import AsyncTaskOwner
from loomstroke.plugins.common.constants import (  # noqa: F401
    ALL_SNIPPET_KINDS,
    CK_LIST,
    CK_PROMPT,
    CK_STRING,
    CK_TEXT,
    SK_LIST,
    SK_PROMPT,
    SK_SYSTEM_PROMPT,
    SK_TEXT,
    SK_USER_PROMPT,
    EventTypes,
)
from loomstroke.plugins.common.events import PluginEvent
from loomstroke.plugins.common.llm_gateway_client import LLMGatewayClient
from loomstroke.plugins.common.snippet_models import (
    PromptPayload,
    Snippet,
    sanitize_filename,
)
from loomstroke.plugins.common.snippet_repository import SnippetRepository
from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.ui import PanelDescriptor, UIPanelPlugin
from loomstroke.ui._dock_safe import is_valid
from loomstroke.ui.translations import (
    TranslationBinder,
    translate_runtime_text,
    translated_literal_domain,
)

from .comfyui_bridge import ComfyUIBridge
from .compose_widgets import PromptChipFlowWidget, PromptChipWidget  # noqa: F401
from .composer import PromptComposer
from .event_handlers import PromptEditorEventHandler, SnippetEventHandler
from .llm_pane_controller import LLMPaneController
from .storage import PromptSnippetStore

_LIBRARY_ACTION_ICON_SIZE = QSize(14, 14)
_LIBRARY_ACTION_BUTTON_SIZE = QSize(22, 22)
_PANEL_MINIMUM_SIZE_HINT = QSize(100, 50)
_PROMPT_EDITOR_DEFAULT_SIZE = (360, 600)
_LIBRARY_ACTION_SVG_ICONS: dict[str, str] = {
    "load": (
        "<svg viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M3 8h8' stroke='#f8fafc' stroke-width='1.7' stroke-linecap='round'/>"
        "<path d='M8 5l3 3-3 3' fill='none' stroke='#f8fafc' stroke-width='1.7' "
        "stroke-linecap='round' stroke-linejoin='round'/>"
        "<path d='M13 3v10' stroke='#94a3b8' stroke-width='1.3' stroke-linecap='round'/>"
        "</svg>"
    ),
    "new": (
        "<svg viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M3.5 3.5h6l3 3v6h-9z' fill='none' stroke='#f8fafc' "
        "stroke-width='1.3' stroke-linejoin='round'/>"
        "<path d='M9.5 3.5v3h3' fill='none' stroke='#94a3b8' stroke-width='1.3'/>"
        "<path d='M8 8v4M6 10h4' stroke='#a78bfa' stroke-width='1.5' "
        "stroke-linecap='round'/>"
        "</svg>"
    ),
    "duplicate": (
        "<svg viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<rect x='5' y='3' width='7.5' height='9' rx='1' fill='none' stroke='#f8fafc' "
        "stroke-width='1.3'/>"
        "<path d='M3.5 5v8h7' fill='none' stroke='#94a3b8' stroke-width='1.3' "
        "stroke-linecap='round' stroke-linejoin='round'/>"
        "</svg>"
    ),
    "delete": (
        "<svg viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M4 5h8M6 5V3.5h4V5' fill='none' stroke='#f8fafc' "
        "stroke-width='1.3' stroke-linecap='round' stroke-linejoin='round'/>"
        "<path d='M5 6l.5 6.5h5L11 6' fill='none' stroke='#fb7185' stroke-width='1.3' "
        "stroke-linecap='round' stroke-linejoin='round'/>"
        "<path d='M7 8v3M9 8v3' stroke='#fb7185' stroke-width='1.1' "
        "stroke-linecap='round'/>"
        "</svg>"
    ),
    "export": (
        "<svg viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M4 12.5h8' stroke='#94a3b8' stroke-width='1.3' stroke-linecap='round'/>"
        "<path d='M8 3.5v6M5.5 7.5L8 10l2.5-2.5' fill='none' stroke='#f8fafc' "
        "stroke-width='1.5' stroke-linecap='round' stroke-linejoin='round'/>"
        "</svg>"
    ),
    "refresh": (
        "<svg viewBox='0 0 16 16' xmlns='http://www.w3.org/2000/svg'>"
        "<path d='M12.4 6.3A4.6 4.6 0 0 0 4.2 4.8' fill='none' stroke='#f8fafc' "
        "stroke-width='1.3' stroke-linecap='round'/>"
        "<path d='M12.3 3.8v2.7H9.6' fill='none' stroke='#a78bfa' stroke-width='1.3' "
        "stroke-linecap='round' stroke-linejoin='round'/>"
        "<path d='M3.6 9.7a4.6 4.6 0 0 0 8.2 1.5' fill='none' stroke='#f8fafc' "
        "stroke-width='1.3' stroke-linecap='round'/>"
        "<path d='M3.7 12.2V9.5h2.7' fill='none' stroke='#a78bfa' stroke-width='1.3' "
        "stroke-linecap='round' stroke-linejoin='round'/>"
        "</svg>"
    ),
}


def _configure_compact_combo(combo: QComboBox, *, min_chars: int = 6) -> None:
    combo.setMinimumWidth(0)
    combo.setMinimumContentsLength(min_chars)
    combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)


logger = logging.getLogger(__name__)


def _plugin_ts(text: str) -> str:
    return translated_literal_domain("PromptEditorPanelPlugin", text, use_fallback=True)


def _panel_ts(text: str) -> str:
    return translated_literal_domain("PromptEditorPanel", text, use_fallback=True)


def _palette_ts(text: str) -> str:
    return translated_literal_domain("ComponentPaletteWidget", text, use_fallback=True)


COMPONENT_MIME_TYPE = "application/x-loomstroke-component-kind"


@dataclass(slots=True)
class PromptComponent:
    """Represents a single building block within the prompt timeline."""

    kind: str
    snippet_id: str | None = None
    list_index: int | None = None
    text_value: str = ""
    tag_filter: str | None = None
    selection_mode: str = "fixed"

    def to_metadata(self) -> dict[str, Any]:
        data: dict[str, Any] = {"kind": self.kind}
        if self.snippet_id:
            data["snippet_id"] = self.snippet_id
        if self.list_index is not None:
            data["list_item_index"] = self.list_index
        if self.text_value:
            data["text_value"] = self.text_value
        if self.tag_filter:
            data["tag_filter"] = self.tag_filter
        if self.selection_mode in {"random", "sequential", "combinatorial"}:
            data["selection_mode"] = self.selection_mode
        return data

    @classmethod
    def from_metadata(cls, payload: dict[str, Any]) -> PromptComponent:
        list_index = payload.get("list_item_index")
        if list_index is not None:
            try:
                list_index = int(list_index)
            except (TypeError, ValueError):
                list_index = None
        return cls(
            kind=str(payload.get("kind", CK_TEXT)),
            snippet_id=payload.get("snippet_id"),
            list_index=list_index,
            text_value=str(payload.get("text_value", "")),
            tag_filter=payload.get("tag_filter"),
            selection_mode=(
                payload.get("selection_mode")
                if payload.get("selection_mode") in ("random", "sequential", "combinatorial")
                else "fixed"
            ),
        )


def _resolve_list_items(snippet: Snippet | None) -> list[str]:
    """Return usable list entries for list-type snippets."""
    if snippet is None:
        return []
    items = []
    for value in snippet.list_items:
        for part in str(value).split(","):
            part = part.strip()  # noqa: PLW2901
            if part:
                items.append(part)
    if items:
        return items
    body = (snippet.body or "").strip()
    if not body:
        return []
    if "," in body:
        parts = []
        for line in body.splitlines():
            parts.extend([part.strip() for part in line.split(",") if part.strip()])
        if parts:
            return parts
    return [line.strip() for line in body.splitlines() if line.strip()]


class SnippetCatalog:
    """Convenience wrapper that exposes snippet subsets for the editor."""

    def __init__(self, repository: SnippetRepository) -> None:
        self._repository = repository
        self._snippet_map: dict[str, Snippet] = {}
        self._by_kind: dict[str, list[Snippet]] = {}
        self._tag_cache: dict[str, list[str]] = {}
        self.refresh()

    def refresh(self) -> None:
        snippets = self._repository.list_snippets()
        self._snippet_map = {snippet.id: snippet for snippet in snippets}
        by_kind: dict[str, list[Snippet]] = {k: [] for k in ALL_SNIPPET_KINDS}
        for snippet in snippets:
            kind = snippet.kind or SK_TEXT
            if kind in by_kind:
                by_kind[kind].append(snippet)
            else:
                by_kind.setdefault(SK_TEXT, []).append(snippet)
        self._by_kind = by_kind
        self._rebuild_tag_cache()

    def update_snippet(self, snippet: Snippet) -> None:
        """Add or update a single snippet without full reload."""
        old = self._snippet_map.get(snippet.id)
        self._snippet_map[snippet.id] = snippet
        kind = snippet.kind or SK_TEXT
        target_list = self._by_kind.setdefault(kind, [])
        # Remove old entry from its kind list
        if old:
            old_kind = old.kind or SK_TEXT
            old_list = self._by_kind.get(old_kind, [])
            self._by_kind[old_kind] = [s for s in old_list if s.id != snippet.id]
        # Remove any stale entry in new kind list and append
        self._by_kind[kind] = [s for s in target_list if s.id != snippet.id]
        self._by_kind[kind].append(snippet)
        self._rebuild_tag_cache()

    def remove_snippet(self, snippet_id: str) -> None:
        """Remove a single snippet without full reload."""
        old = self._snippet_map.pop(snippet_id, None)
        if old:
            kind = old.kind or SK_TEXT
            old_list = self._by_kind.get(kind, [])
            self._by_kind[kind] = [s for s in old_list if s.id != snippet_id]
            self._rebuild_tag_cache()

    def _rebuild_tag_cache(self) -> None:
        tag_cache: dict[str, list[str]] = {}
        for group, items in self._by_kind.items():
            tags = {tag.strip() for snippet in items for tag in (snippet.tags or []) if tag.strip()}
            tag_cache[group] = sorted(tags)
        self._tag_cache = tag_cache

    def get(self, snippet_id: str | None) -> Snippet | None:
        if not snippet_id:
            return None
        return self._snippet_map.get(snippet_id)

    def tags(self, component_kind: str) -> list[str]:
        key = self._kind_key(component_kind)
        return list(self._tag_cache.get(key, []))

    def iter(self, component_kind: str, *, tag: str | None = None) -> list[Snippet]:
        key = self._kind_key(component_kind)
        base = self._by_kind.get(key, [])
        if not tag:
            return list(base)
        tag_lower = tag.lower()
        return [
            snippet for snippet in base if any(t.lower() == tag_lower for t in (snippet.tags or []))
        ]

    def default_snippet(self, component_kind: str) -> Snippet | None:
        snippets = self.iter(component_kind)
        return snippets[0] if snippets else None

    @staticmethod
    def _kind_key(component_kind: str) -> str:
        if component_kind == CK_LIST:
            return SK_LIST
        if component_kind == CK_PROMPT:
            return SK_PROMPT
        return SK_TEXT


class ComponentPaletteWidget(QListWidget):
    """Palette that exposes available component primitives."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setViewMode(QListWidget.IconMode)
        self.setMovement(QListWidget.Static)
        self.setDragEnabled(True)
        self.setSpacing(8)
        self.setResizeMode(QListWidget.Adjust)
        self.setIconSize(QSize(32, 32))
        self._entries: list[tuple[str, str, object]] = []
        self._items: list[tuple[QListWidgetItem, str]] = []
        self._tooltip_template = self._ts("Add {label} component")
        self._populate()

    def _populate(self) -> None:
        style = self.style()
        entries: list[tuple[str, str, object]] = [
            (CK_STRING, self._ts("String"), style.standardIcon(QStyle.SP_FileIcon)),
            (CK_LIST, self._ts("List"), style.standardIcon(QStyle.SP_FileDialogDetailedView)),
            (CK_TEXT, self._ts("Text"), style.standardIcon(QStyle.SP_DialogApplyButton)),
            (CK_PROMPT, self._ts("Prompt"), style.standardIcon(QStyle.SP_MessageBoxInformation)),
        ]
        self._entries = entries
        self.clear()
        self._items.clear()
        for kind, label_source, icon in entries:
            label = translate_runtime_text(label_source, self.tr)
            item = QListWidgetItem(icon, label)
            item.setData(Qt.UserRole, kind)
            item.setToolTip(
                translate_runtime_text(self._tooltip_template, self.tr).format(label=label)
            )
            self.addItem(item)
            self._items.append((item, label_source))

    def refresh_translations(self) -> None:
        for item, label_source in self._items:
            label = translate_runtime_text(label_source, self.tr)
            item.setText(label)
            item.setToolTip(
                translate_runtime_text(self._tooltip_template, self.tr).format(label=label)
            )

    def _ts(self, text: str) -> str:
        return _palette_ts(text)

    def mimeData(
        self,
        indexes: Iterable[QModelIndex | QListWidgetItem],
    ) -> QMimeData:  # type: ignore[override]  # PySide6 override stub limitation
        mime = QMimeData()
        kinds: list[str] = []
        for entry in indexes:
            data_value: Any | None = None
            if isinstance(entry, QListWidgetItem):
                data_value = entry.data(Qt.UserRole)
            else:
                is_valid = getattr(entry, "isValid", None)
                if callable(is_valid) and is_valid():
                    data_value = entry.data(Qt.UserRole)
            if data_value:
                kinds.append(str(data_value))
        if kinds:
            mime.setData(COMPONENT_MIME_TYPE, ",".join(kinds).encode("utf-8"))
        return mime


class PromptEditorPanel(QWidget, AsyncTaskOwner):
    """Prompt Editor dock widget combining snippets into prompts."""

    prompt_built = Signal(str)

    def __init__(  # noqa: PLR0915
        self,
        store: PromptSnippetStore,
        host: HostAPI,
        *,
        event_bus: ExtendedEventBus | None,
        composer: PromptComposer | None,
        comfyui_bridge: ComfyUIBridge | None = None,
        llm_client: LLMGatewayClient | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        AsyncTaskOwner.__init__(self)
        self.setMinimumWidth(0)
        self._store = store
        self._host = host
        self._event_bus = event_bus
        self._translation_binder = TranslationBinder(self.tr)
        self._translation_event_bus = event_bus or get_extended_event_bus()
        self._translation_handlers: list[Callable[[PluginEvent], None]] = []
        self._translation_tokens: list[EventToken] = []
        self._library_kind_sources = {
            SK_PROMPT: self._ts("Prompt"),
            SK_TEXT: self._ts("String"),
            SK_LIST: self._ts("List"),
            SK_SYSTEM_PROMPT: self._ts("System Prompt (LLM)"),
            SK_USER_PROMPT: self._ts("User Prompt (LLM)"),
        }
        self._library_kind_order = list(ALL_SNIPPET_KINDS)
        self._library_type_placeholder = self._ts("Component type")
        self._library_all_tags_source = self._ts("All tags")
        self._composer = composer
        self._comfyui = comfyui_bridge
        self._snippet_repo = SnippetRepository(host.settings)
        self._catalog = SnippetCatalog(self._snippet_repo)
        self._components: list[PromptComponent] = []
        self._component_widgets: list[PromptChipWidget] = []
        self._chip_flow: PromptChipFlowWidget | None = None
        self._palette: ComponentPaletteWidget | None = None
        self._prompt_snippets: list[Snippet] = []
        self._current_prompt_id: str | None = None
        self._selected_prompt: Snippet | None = None
        self._dirty = False
        self._separator_edit: QLineEdit | None = None
        self._event_tokens: list[EventToken] = []
        self._event_handlers: list[Callable[[PluginEvent], None]] = []
        self._tabs: QTabWidget | None = None
        self._compose_tab: QWidget | None = None
        self._library_tab: QWidget | None = None
        self._llm_tab: QWidget | None = None
        self._library_search: QLineEdit | None = None
        self._library_tag_filter: QComboBox | None = None
        self._library_type_filter: QComboBox | None = None
        self._library_list: QListWidget | None = None
        self._library_title: QLineEdit | None = None
        self._library_tags: QLineEdit | None = None
        self._library_preview: QPlainTextEdit | None = None
        self._library_status: QLabel | None = None
        self._library_kind_combo: QComboBox | None = None
        self._library_duplicate_button: QToolButton | None = None
        self._library_delete_button: QToolButton | None = None
        self._library_export_button: QToolButton | None = None
        self._library_refresh_button: QToolButton | None = None
        self._library_new_button: QToolButton | None = None
        self._library_load_button: QToolButton | None = None
        self._library_autosave: QTimer | None = None
        self._library_dirty = False
        self._library_block_updates = False
        self._llm_pane = LLMPaneController(
            self,
            host,
            llm_client=llm_client,
            translation_binder=self._translation_binder,
            prompt_snippets_provider=lambda: self._prompt_snippets,
            catalog_get=self._catalog.get,
            selected_prompt_provider=lambda: self._selected_prompt,
            library_kind_label=self._library_kind_label,
            add_output_to_library=self._add_output_to_library,
            create_owned_task=self.create_owned_task,
            cancel_owned_task=self.cancel_owned_task,
        )
        self._status_source: str | None = None
        self._status_kwargs: dict[str, Any] = {}
        self._library_status_source: str | None = None
        self._library_status_kwargs: dict[str, Any] = {}

        self._build_ui()
        self.refresh_data()
        self._subscribe_text_editor_events()
        self._subscribe_language_events()
        self._update_preview()
        self.set_status_message_translated("Drag components to build a prompt.")

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        self._tabs = QTabWidget(self)
        layout.addWidget(self._tabs)

        self._compose_tab = self._build_compose_tab()
        self._library_tab = self._build_library_tab()
        self._llm_tab = self._llm_pane.build_tab()

        compose_title = self._ts("Compose")
        library_title = self._ts("Library")
        llm_title = self._ts("LLM")
        self._tabs.addTab(self._compose_tab, compose_title)
        self._tabs.addTab(self._library_tab, library_title)
        self._tabs.addTab(self._llm_tab, llm_title)
        self._register_tab_title(self._compose_tab, compose_title)
        self._register_tab_title(self._library_tab, library_title)
        self._register_tab_title(self._llm_tab, llm_title)

        self._save_shortcut = QShortcut(QKeySequence("Ctrl+Shift+S"), self)
        self._save_shortcut.activated.connect(self._save_prompt)

    def minimumSizeHint(self) -> QSize:
        return _PANEL_MINIMUM_SIZE_HINT

    def _register_tab_title(self, widget: QWidget, title: str) -> None:
        if self._tabs is None:
            return
        index = self._tabs.indexOf(widget)
        if index < 0:
            return
        self._translation_binder.register(
            lambda text, idx=index: self._tabs.setTabText(idx, text),
            title,
        )

    def _subscribe_language_events(self) -> None:
        bus = self._translation_event_bus
        if bus is None:
            return
        handler = self._on_language_changed
        self._translation_handlers.append(handler)
        token = getattr(bus, "on", None) or getattr(bus, "subscribe", None)
        if callable(token):
            self._translation_tokens.append(token(Events.LANGUAGE_CHANGED, handler))

    def _on_language_changed(self, _payload: PluginEvent) -> None:
        if not is_valid(self):
            return
        with contextlib.suppress(RuntimeError):
            self.refresh_translations()

    def refresh_translations(self) -> None:
        if not is_valid(self):
            return
        self._translation_binder.set_translator(self.tr)
        self._translation_binder.retranslate()
        if self._palette:
            self._palette.refresh_translations()
        if self._chip_flow:
            self._chip_flow.refresh_translations()
        # Rebuild chips to refresh display text
        self._rebuild_component_widgets()
        self._populate_library_type_filter()
        self._populate_library_kind_combo()
        self._update_library_tag_filter()
        self._refresh_library_list_labels()
        self._llm_pane.refresh_translations()
        if self._status_source:
            self.set_status_message_translated(self._status_source, **self._status_kwargs)
        if self._library_status_source:
            self._set_library_status_translated(
                self._library_status_source,
                **self._library_status_kwargs,
            )

    def _ts(self, text: str) -> str:
        return _panel_ts(text)

    def changeEvent(self, event: QEvent) -> None:
        if not is_valid(self):
            return
        with contextlib.suppress(RuntimeError):
            super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.refresh_translations()

    def _add_form_row(self, layout: QFormLayout, text: str, field: QWidget) -> None:
        label = QLabel()
        self._translation_binder.register(label.setText, text)
        layout.addRow(label, field)

    def _build_compose_tab(self) -> QWidget:  # noqa: PLR0915
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        tab = QWidget(scroll)
        scroll.setWidget(tab)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        palette_title = self._ts("Components")
        palette_box = QGroupBox(palette_title, tab)
        self._translation_binder.register(palette_box.setTitle, palette_title)
        palette_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        palette_layout = QVBoxLayout(palette_box)
        palette_layout.setContentsMargins(8, 8, 8, 8)
        palette_layout.setSpacing(6)
        self._palette = ComponentPaletteWidget(tab)
        self._palette.itemDoubleClicked.connect(self._handle_palette_double_click)
        palette_layout.addWidget(self._palette)
        margins = palette_layout.contentsMargins()
        icon_height = self._palette.iconSize().height()
        text_height = self.fontMetrics().height()
        fixed_height = max(
            icon_height
            + text_height
            + margins.top()
            + margins.bottom()
            + (self._palette.spacing() * 2)
            + 24,
            120,
        )
        palette_box.setMinimumHeight(fixed_height)
        palette_box.setMaximumHeight(fixed_height + 0)
        layout.addWidget(palette_box)

        live_prompt_text = self._ts("Live prompt")
        live_prompt_label = QLabel(live_prompt_text, tab)
        self._translation_binder.register(live_prompt_label.setText, live_prompt_text)
        layout.addWidget(live_prompt_label)
        self._chip_flow = PromptChipFlowWidget(tab)
        self._chip_flow.chipInserted.connect(self._insert_component)
        self._chip_flow.chipMoved.connect(self._on_chip_moved)
        self._chip_flow.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout.addWidget(self._chip_flow, 1)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        separator_text = self._ts("Separator")
        separator_label = QLabel(separator_text, tab)
        self._translation_binder.register(separator_label.setText, separator_text)
        self._separator_edit = QLineEdit(tab)
        self._separator_edit.setPlaceholderText("\\n")
        self._separator_edit.setFixedWidth(64)
        self._separator_edit.textChanged.connect(self._on_separator_changed)
        action_row.addWidget(separator_label)
        action_row.addWidget(self._separator_edit)
        copy_text = self._ts("Copy")
        self._copy_button = self._create_compact_icon_button(
            tab,
            tooltip=copy_text,
            theme_icon="edit-copy",
            fallback_icon=QStyle.SP_DialogOpenButton,
            handler=self._handle_copy,
        )
        action_row.addWidget(self._copy_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        save_title = self._ts("Save")
        save_box = QGroupBox(save_title, tab)
        self._translation_binder.register(save_box.setTitle, save_title)
        save_layout = QVBoxLayout(save_box)
        save_layout.setContentsMargins(8, 8, 8, 8)
        save_layout.setSpacing(6)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        self._title_edit = QLineEdit(tab)
        title_placeholder = self._ts("Prompt title")
        self._translation_binder.register(self._title_edit.setPlaceholderText, title_placeholder)
        self._title_edit.textChanged.connect(self._on_title_changed)
        self._add_form_row(form, self._ts("Title"), self._title_edit)
        self._tags_edit = QLineEdit(tab)
        tags_placeholder = self._ts("Tags (comma separated)")
        self._translation_binder.register(self._tags_edit.setPlaceholderText, tags_placeholder)
        self._tags_edit.textChanged.connect(self._on_tags_changed)
        self._add_form_row(form, self._ts("Tags"), self._tags_edit)
        save_layout.addLayout(form)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        new_text = self._ts("New")
        self._new_button = self._create_compact_icon_button(
            tab,
            tooltip=new_text,
            theme_icon="document-new",
            fallback_icon=QStyle.SP_FileDialogNewFolder,
            handler=self._new_prompt,
        )
        button_row.addWidget(self._new_button)
        save_text = self._ts("Save")
        self._save_button = self._create_compact_icon_button(
            tab,
            tooltip=save_text,
            theme_icon="document-save",
            fallback_icon=QStyle.SP_DialogSaveButton,
            handler=self._save_prompt,
        )
        button_row.addWidget(self._save_button)
        save_as_text = self._ts("Save As")
        self._save_as_button = self._create_compact_icon_button(
            tab,
            tooltip=save_as_text,
            theme_icon="document-save-as",
            fallback_icon=QStyle.SP_DialogSaveButton,
            handler=lambda: self._save_prompt(force_new=True),
        )
        button_row.addWidget(self._save_as_button)
        button_row.addStretch(1)
        save_layout.addLayout(button_row)
        layout.addWidget(save_box)

        self._status = QLabel("", tab)
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        if self._separator_edit is not None:
            block = self._separator_edit.blockSignals(True)
            self._separator_edit.setText("\\n")
            self._separator_edit.blockSignals(block)

        return scroll

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
            icon = self.style().standardIcon(fallback_icon)
        button.setIcon(icon)
        self._translation_binder.register(button.setToolTip, tooltip)
        button.setIconSize(QSize(icon_size, icon_size))
        button.setFixedSize(QSize(button_size, button_size))
        button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        button.setAutoRaise(True)
        button.clicked.connect(handler)
        return button

    def _create_library_action_button(
        self,
        parent: QWidget,
        *,
        tooltip: str,
        icon_key: str,
        fallback_icon: QStyle.StandardPixmap,
        handler: Callable[[], None],
    ) -> QToolButton:
        button = QToolButton(parent)
        from loomstroke.ui.icon_loader import load_svg_icon_from_data  # noqa: PLC0415

        svg_data = _LIBRARY_ACTION_SVG_ICONS.get(icon_key)
        svg_icon = (
            load_svg_icon_from_data(svg_data, _LIBRARY_ACTION_ICON_SIZE) if svg_data else None
        )
        button.setIcon(
            svg_icon if svg_icon is not None else self.style().standardIcon(fallback_icon)
        )
        self._translation_binder.register(button.setToolTip, tooltip)
        button.setIconSize(_LIBRARY_ACTION_ICON_SIZE)
        button.setFixedSize(_LIBRARY_ACTION_BUTTON_SIZE)
        button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        button.setAutoRaise(True)
        button.clicked.connect(handler)
        return button

    def _build_library_tab(self) -> QWidget:  # noqa: PLR0915
        tab = QWidget(self)
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        filter_layout = QVBoxLayout()
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_layout.setSpacing(6)
        self._library_search = QLineEdit(tab)
        search_placeholder = self._ts("Search prompts...")
        self._translation_binder.register(
            self._library_search.setPlaceholderText, search_placeholder
        )
        filter_layout.addWidget(self._library_search)
        self._library_tag_filter = QComboBox(tab)
        _configure_compact_combo(self._library_tag_filter, min_chars=8)
        self._library_tag_filter.addItem(self.tr(self._library_all_tags_source), None)
        filter_layout.addWidget(self._library_tag_filter)
        self._library_type_filter = QComboBox(tab)
        _configure_compact_combo(self._library_type_filter, min_chars=8)
        self._populate_library_type_filter()
        filter_layout.addWidget(self._library_type_filter)
        layout.addLayout(filter_layout)

        self._library_list = QListWidget(tab)
        self._library_list.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(self._library_list, 2)

        details_title = self._ts("Details")
        details_box = QGroupBox(details_title, tab)
        self._translation_binder.register(details_box.setTitle, details_title)
        details_layout = QVBoxLayout(details_box)
        details_layout.setContentsMargins(8, 8, 8, 8)
        details_layout.setSpacing(6)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setSpacing(6)
        self._library_title = QLineEdit(tab)
        self._add_form_row(form, self._ts("Title"), self._library_title)
        self._library_tags = QLineEdit(tab)
        self._add_form_row(form, self._ts("Tags"), self._library_tags)
        self._library_kind_combo = QComboBox(tab)
        _configure_compact_combo(self._library_kind_combo, min_chars=8)
        self._populate_library_kind_combo()
        self._add_form_row(form, self._ts("Component type"), self._library_kind_combo)
        details_layout.addLayout(form)
        preview_text = self._ts("Preview")
        preview_label = QLabel(preview_text, tab)
        self._translation_binder.register(preview_label.setText, preview_text)
        details_layout.addWidget(preview_label)
        self._library_preview = QPlainTextEdit(tab)
        self._library_preview.setMinimumHeight(60)
        details_layout.addWidget(self._library_preview)
        layout.addWidget(details_box, 1)

        action_row = QHBoxLayout()
        action_row.setContentsMargins(0, 0, 0, 0)
        self._library_load_button = self._create_library_action_button(
            tab,
            tooltip=self._ts("Load to Compose"),
            icon_key="load",
            fallback_icon=QStyle.SP_ArrowRight,
            handler=self._load_selected_to_compose,
        )
        self._library_load_button.setEnabled(False)
        action_row.addWidget(self._library_load_button)
        self._library_new_button = self._create_library_action_button(
            tab,
            tooltip=self._ts("New"),
            icon_key="new",
            fallback_icon=QStyle.SP_FileDialogNewFolder,
            handler=self._create_library_item,
        )
        action_row.addWidget(self._library_new_button)
        self._library_duplicate_button = self._create_library_action_button(
            tab,
            tooltip=self._ts("Duplicate"),
            icon_key="duplicate",
            fallback_icon=QStyle.SP_FileDialogDetailedView,
            handler=self._duplicate_selected_prompt,
        )
        action_row.addWidget(self._library_duplicate_button)
        self._library_delete_button = self._create_library_action_button(
            tab,
            tooltip=self._ts("Delete"),
            icon_key="delete",
            fallback_icon=QStyle.SP_TrashIcon,
            handler=self._delete_selected_prompt,
        )
        action_row.addWidget(self._library_delete_button)
        self._library_export_button = self._create_library_action_button(
            tab,
            tooltip=self._ts("Export"),
            icon_key="export",
            fallback_icon=QStyle.SP_DialogSaveButton,
            handler=self._export_selected_prompt,
        )
        action_row.addWidget(self._library_export_button)
        self._library_refresh_button = self._create_library_action_button(
            tab,
            tooltip=self._ts("Refresh"),
            icon_key="refresh",
            fallback_icon=QStyle.SP_BrowserReload,
            handler=self.refresh_data,
        )
        action_row.addWidget(self._library_refresh_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        self._library_status = QLabel("", tab)
        self._library_status.setWordWrap(True)
        layout.addWidget(self._library_status)

        self._library_autosave = QTimer(tab)
        self._library_autosave.setInterval(600)
        self._library_autosave.setSingleShot(True)
        self._library_autosave.timeout.connect(self._autosave_library_changes)

        if self._library_search:
            self._library_search.textChanged.connect(self._refresh_library_list)
        if self._library_tag_filter:
            self._library_tag_filter.currentIndexChanged.connect(self._refresh_library_list)
        if self._library_type_filter:
            self._library_type_filter.currentIndexChanged.connect(self._refresh_library_list)
        if self._library_list:
            self._library_list.currentItemChanged.connect(self._on_library_selection_changed)
            self._library_list.itemDoubleClicked.connect(self._on_library_double_click)
        if self._library_title:
            self._library_title.textChanged.connect(self._schedule_library_autosave)
        if self._library_tags:
            self._library_tags.textChanged.connect(self._schedule_library_autosave)
        if self._library_kind_combo:
            self._library_kind_combo.currentIndexChanged.connect(self._schedule_library_autosave)
        if self._library_preview:
            self._library_preview.textChanged.connect(self._schedule_library_autosave)

        self._set_library_actions_enabled(False)

        return tab

    def _populate_library_type_filter(self) -> None:
        if self._library_type_filter is None:
            return
        current = self._library_type_filter.currentData()
        block = self._library_type_filter.blockSignals(True)
        self._library_type_filter.clear()
        self._library_type_filter.addItem(
            translate_runtime_text(self._library_type_placeholder, self.tr),
            None,
        )
        for kind in self._library_kind_order:
            label_source = self._library_kind_sources.get(kind, kind.title())
            self._library_type_filter.addItem(translate_runtime_text(label_source, self.tr), kind)
        if current is not None:
            index = self._library_type_filter.findData(current)
            if index >= 0:
                self._library_type_filter.setCurrentIndex(index)
        self._library_type_filter.blockSignals(block)

    def _populate_library_kind_combo(self) -> None:
        if self._library_kind_combo is None:
            return
        current = self._library_kind_combo.currentData()
        block = self._library_kind_combo.blockSignals(True)
        self._library_kind_combo.clear()
        for kind in self._library_kind_order:
            label_source = self._library_kind_sources.get(kind, kind.title())
            self._library_kind_combo.addItem(translate_runtime_text(label_source, self.tr), kind)
        if current is not None:
            index = self._library_kind_combo.findData(current)
            if index >= 0:
                self._library_kind_combo.setCurrentIndex(index)
        self._library_kind_combo.blockSignals(block)

    def _refresh_library_list_labels(self) -> None:
        if self._library_list is None:
            return
        for row in range(self._library_list.count()):
            item = self._library_list.item(row)
            if item is None:
                continue
            snippet_id = item.data(Qt.UserRole)
            snippet = self._catalog.get(snippet_id) if snippet_id else None
            if snippet is None:
                continue
            kind_label = self._library_kind_label(snippet.kind)
            title = snippet.title or snippet.id
            item.setText(f"[{kind_label}] {title}")

    def _set_library_actions_enabled(self, enabled: bool) -> None:
        for button in (
            self._library_duplicate_button,
            self._library_delete_button,
            self._library_export_button,
        ):
            if button:
                button.setEnabled(enabled)
        if self._library_load_button:
            is_prompt = (
                enabled
                and self._selected_prompt is not None
                and self._selected_prompt.kind == SK_PROMPT
            )
            self._library_load_button.setEnabled(is_prompt)
        for widget in (
            self._library_title,
            self._library_tags,
            self._library_preview,
        ):
            if widget:
                widget.setEnabled(enabled)
        if self._library_kind_combo:
            self._library_kind_combo.setEnabled(True)

    def _set_library_status(self, message: str) -> None:
        if self._library_status is None:
            return
        self._library_status_source = None
        self._library_status_kwargs = {}
        self._render_library_status(message)

    def _render_library_status(self, message: str) -> None:
        if self._library_status is None:
            return
        self._library_status.setText(message)

    def _set_library_status_translated(self, source_text: str, **kwargs: Any) -> None:
        self._library_status_source = source_text
        self._library_status_kwargs = dict(kwargs)
        self._render_library_status(self._ts(source_text).format(**kwargs))

    def _schedule_library_autosave(self) -> None:
        if self._library_block_updates:
            return
        if not self._selected_prompt:
            return
        self._library_dirty = True
        if self._library_autosave:
            self._library_autosave.start()

    def _autosave_library_changes(self) -> None:
        self._commit_library_changes(refresh=False)

    def _flush_pending_library_autosave(self) -> None:
        if self._library_autosave:
            self._library_autosave.stop()
        if self._library_dirty:
            self._commit_library_changes(refresh=False)

    def _commit_library_changes(self, *, refresh: bool = True) -> None:
        if not self._library_dirty or not self._selected_prompt:
            return
        if (
            not self._library_title
            or not self._library_tags
            or not self._library_kind_combo
            or not self._library_preview
        ):
            return
        title = self._library_title.text().strip() or self._selected_prompt.title or "Prompt"
        tags = [tag.strip() for tag in self._library_tags.text().split(",") if tag.strip()]
        body = self._library_preview.toPlainText()
        metadata = dict(self._selected_prompt.metadata or {})
        metadata["title"] = title
        metadata["tags"] = tags
        selected_kind = self._library_kind_combo.currentData() or self._selected_prompt.kind
        list_items: tuple[str, ...] = tuple()
        if selected_kind == SK_LIST:
            list_items = tuple()
        snippet = Snippet(
            id=self._selected_prompt.id,
            title=title,
            body=body,
            tags=tags,
            category=self._selected_prompt.category or SK_PROMPT,
            favorite=self._selected_prompt.favorite,
            metadata=metadata,
            kind=str(selected_kind),
            list_items=list_items,
        )
        try:
            saved = self._snippet_repo.save_snippet(snippet)
        except (OSError, sqlite3.Error, ValueError) as exc:
            self._set_library_status_translated("Save failed: {error}", error=exc)
            return
        previous_kind = self._selected_prompt.kind
        self._library_dirty = False
        self._selected_prompt = saved
        for index, existing in enumerate(self._prompt_snippets):
            if existing.id == saved.id:
                self._prompt_snippets[index] = saved
                break
        else:
            self._prompt_snippets.append(saved)
        kind_changed = previous_kind != saved.kind
        self._catalog.refresh()
        self._update_preview()
        self._update_library_tag_filter()
        if refresh or kind_changed:
            self._refresh_library_list()
            self._select_library_prompt(saved.id)
        else:
            self._update_library_list_item(saved)
        self._refresh_llm_prompt_lists()
        self._set_library_status_translated("Saved '{title}'.", title=saved.title)

    def _library_kind_label(self, kind: str) -> str:
        label_source = self._library_kind_sources.get(kind)
        if label_source:
            return translate_runtime_text(label_source, self.tr)
        return kind.title()

    def _update_library_list_item(self, snippet: Snippet) -> None:
        if self._library_list is None:
            return
        kind_label = self._library_kind_label(snippet.kind)
        title = snippet.title or snippet.id
        for row in range(self._library_list.count()):
            item = self._library_list.item(row)
            if item.data(Qt.UserRole) == snippet.id:
                item.setText(f"[{kind_label}] {title}")
                break

    def _update_library_tag_filter(self) -> None:
        if self._library_tag_filter is None:
            return
        current = self._library_tag_filter.currentData()
        tags: set[str] = set()
        for snippet in self._prompt_snippets:
            for tag in snippet.tags or []:
                tag = str(tag).strip()  # noqa: PLW2901
                if tag:
                    tags.add(tag)
        block = self._library_tag_filter.blockSignals(True)
        self._library_tag_filter.clear()
        self._library_tag_filter.addItem(
            translate_runtime_text(self._library_all_tags_source, self.tr),
            None,
        )
        for tag in sorted(tags):
            self._library_tag_filter.addItem(tag, tag)
        if current:
            index = self._library_tag_filter.findData(current)
            if index >= 0:
                self._library_tag_filter.setCurrentIndex(index)
        self._library_tag_filter.blockSignals(block)

    def _filtered_prompt_snippets(self) -> list[Snippet]:
        snippets = list(self._prompt_snippets)
        query = ""
        tag = None
        type_value = None
        if self._library_search is not None:
            query = self._library_search.text().strip().lower()
        if self._library_tag_filter is not None:
            tag = self._library_tag_filter.currentData()
        if self._library_type_filter is not None:
            type_value = self._library_type_filter.currentData()
        if type_value:
            snippets = [snippet for snippet in snippets if snippet.kind == type_value]
        if tag:
            snippets = [
                snippet
                for snippet in snippets
                if any(t.lower() == str(tag).lower() for t in (snippet.tags or []))
            ]
        if query:
            snippets = [
                snippet
                for snippet in snippets
                if query in (snippet.title or snippet.id).lower()
                or query in (snippet.body or "").lower()
            ]
        return sorted(snippets, key=lambda snippet: (snippet.title or snippet.id).lower())

    def _refresh_library_list(self) -> None:
        if self._library_list is None:
            return
        current_id = self._selected_prompt.id if self._selected_prompt else None
        snippets = self._filtered_prompt_snippets()
        block = self._library_list.blockSignals(True)
        self._library_list.clear()
        selected_row = -1
        for idx, snippet in enumerate(snippets):
            kind_label = self._library_kind_label(snippet.kind)
            title = snippet.title or snippet.id
            item = QListWidgetItem(f"[{kind_label}] {title}")
            item.setData(Qt.UserRole, snippet.id)
            self._library_list.addItem(item)
            if snippet.id == current_id:
                selected_row = idx
        if selected_row >= 0:
            self._library_list.setCurrentRow(selected_row)
        elif snippets:
            self._library_list.setCurrentRow(0)
        self._library_list.blockSignals(block)
        if snippets:
            current_item = self._library_list.currentItem()
            self._on_library_selection_changed(current_item, None)
        if not snippets:
            self._selected_prompt = None
            self._update_library_details(None)

    def _on_library_selection_changed(
        self,
        current: QListWidgetItem | None,
        _previous: QListWidgetItem | None,
    ) -> None:
        self._flush_pending_library_autosave()
        snippet_id = current.data(Qt.UserRole) if current else None
        snippet = self._catalog.get(snippet_id) if snippet_id else None
        self._selected_prompt = snippet
        self._update_library_details(snippet)

    def _select_library_prompt(self, prompt_id: str | None) -> None:
        if self._library_list is None or not prompt_id:
            return
        for row in range(self._library_list.count()):
            item = self._library_list.item(row)
            if item.data(Qt.UserRole) == prompt_id:
                block = self._library_list.blockSignals(True)
                self._library_list.setCurrentRow(row)
                self._library_list.blockSignals(block)
                self._on_library_selection_changed(item, None)
                return

    def _update_library_details(self, snippet: Snippet | None) -> None:
        if (
            self._library_title is None
            or self._library_tags is None
            or self._library_kind_combo is None
            or self._library_preview is None
        ):
            return
        self._library_block_updates = True
        title_block = self._library_title.blockSignals(True)
        tags_block = self._library_tags.blockSignals(True)
        kind_block = self._library_kind_combo.blockSignals(True)
        preview_block = self._library_preview.blockSignals(True)
        if snippet is None:
            self._library_title.clear()
            self._library_tags.clear()
            self._library_kind_combo.setCurrentIndex(0)
            self._library_preview.clear()
            self._set_library_actions_enabled(False)
        else:
            self._library_title.setText(snippet.title or snippet.id)
            self._library_tags.setText(", ".join(snippet.tags or []))
            kind_index = self._library_kind_combo.findData(snippet.kind)
            if kind_index >= 0:
                self._library_kind_combo.setCurrentIndex(kind_index)
            self._library_preview.setPlainText(snippet.body or "")
            self._set_library_actions_enabled(True)
        self._library_title.blockSignals(title_block)
        self._library_tags.blockSignals(tags_block)
        self._library_kind_combo.blockSignals(kind_block)
        self._library_preview.blockSignals(preview_block)
        self._library_dirty = False
        self._library_block_updates = False

    def _duplicate_selected_prompt(self) -> None:
        if not self._selected_prompt:
            self._set_library_status_translated("No prompt selected.")
            return
        source = self._selected_prompt
        new_id = f"prompt-{uuid.uuid4().hex[:12]}"
        new_title = f"{source.title or 'Prompt'} copy"
        metadata = dict(source.metadata or {})
        metadata["title"] = new_title
        snippet = Snippet(
            id=new_id,
            title=new_title,
            body=source.body,
            tags=list(source.tags or []),
            category=source.category or SK_PROMPT,
            favorite=False,
            metadata=metadata,
            kind=SK_PROMPT,
            list_items=tuple(source.list_items or ()),
        )
        saved = self._snippet_repo.save_snippet(snippet)
        self.refresh_data()
        self._selected_prompt = saved
        self._select_library_prompt(saved.id)
        self._set_library_status_translated(
            "Duplicated '{title}'.",
            title=source.title or source.id,
        )

    def _delete_selected_prompt(self) -> None:
        if not self._selected_prompt:
            self._set_library_status_translated("No prompt selected.")
            return
        self._delete_prompt(self._selected_prompt.id)

    def _export_selected_prompt(self) -> None:
        if not self._selected_prompt:
            self._set_library_status_translated("No prompt selected.")
            return
        title = self._selected_prompt.title or "prompt"
        default_name = sanitize_filename(title) + ".txt"
        path, _ = QFileDialog.getSaveFileName(
            self,
            self._ts("Export prompt"),
            default_name,
            self._ts("Text Files (*.txt);;All Files (*.*)"),
        )
        if not path:
            return
        try:
            Path(path).write_text(self._selected_prompt.body or "", encoding="utf-8")
        except OSError as exc:
            self._set_library_status_translated("Export failed: {error}", error=exc)
            return
        self._set_library_status_translated("Exported to {path}.", path=path)

    def _create_library_item(self) -> None:
        kind = SK_PROMPT
        if self._library_kind_combo:
            selection = self._library_kind_combo.currentData()
            if selection:
                kind = str(selection)
        label = self._library_kind_label(kind)
        title = f"New {label}"
        snippet_id = f"{kind}-{uuid.uuid4().hex[:12]}"
        snippet = Snippet(
            id=snippet_id,
            title=title,
            body="",
            tags=[],
            category=kind,
            favorite=False,
            metadata={"title": title, "kind": kind},
            kind=kind,
            list_items=tuple(),
        )
        saved = self._snippet_repo.save_snippet(snippet)
        self._prompt_snippets.append(saved)
        self._catalog.refresh()
        self._update_library_tag_filter()
        self._refresh_library_list()
        self._selected_prompt = saved
        self._select_library_prompt(saved.id)
        self._refresh_llm_prompt_lists()
        self._set_library_status_translated("Created '{title}'.", title=saved.title)

    def _on_llm_output_context_menu(self, pos: Any) -> None:
        self._llm_pane.on_output_context_menu(pos)

    def _add_output_to_library(self, kind: str, text: str) -> None:
        """Create a new Library snippet of ``kind`` whose body is *text*.

        Mirrors :meth:`_create_library_item` so the new entry behaves
        identically to one created via the Library tab's "New" button:
        the caller can rename / re-tag it inline via the Library
        editor. After creation the Library tab is brought to the front
        and the new entry is selected so the user gets immediate
        visual confirmation.
        """
        body = text.strip()
        if not body:
            return
        if kind not in (SK_TEXT, SK_LIST, SK_PROMPT):
            return
        label = self._library_kind_label(kind)
        title = f"New {label}"
        snippet_id = f"{kind}-{uuid.uuid4().hex[:12]}"
        snippet = Snippet(
            id=snippet_id,
            title=title,
            body=body,
            tags=[],
            category=kind,
            favorite=False,
            metadata={"title": title, "kind": kind},
            kind=kind,
            list_items=tuple(),
        )
        try:
            saved = self._snippet_repo.save_snippet(snippet)
        except (OSError, sqlite3.Error, ValueError) as exc:
            self._set_library_status_translated("Save failed: {error}", error=exc)
            return
        self._prompt_snippets.append(saved)
        self._catalog.refresh()
        self._update_library_tag_filter()
        self._refresh_library_list()
        self._selected_prompt = saved
        self._select_library_prompt(saved.id)
        self._refresh_llm_prompt_lists()
        # Bring the Library tab to the front so the user sees the
        # new entry immediately and can edit / rename it.
        if self._tabs is not None and self._library_tab is not None:
            tab_index = self._tabs.indexOf(self._library_tab)
            if tab_index >= 0:
                self._tabs.setCurrentIndex(tab_index)
        self._set_library_status_translated("Created '{title}'.", title=saved.title)

    def _initialize_llm_tab(self) -> None:
        self._llm_pane.initialize()

    def _on_llm_json_toggled(self, checked: bool) -> None:
        self._llm_pane.on_json_toggled(checked)

    def _on_llm_provider_changed(self, _index: int) -> None:
        self._llm_pane.on_provider_changed(_index)

    def _load_llm_providers(self) -> None:
        self._llm_pane.load_providers()

    def _load_llm_models(self) -> None:
        self._llm_pane.load_models()

    def _load_llm_schemas(self) -> None:
        self._llm_pane.load_schemas()

    def _restore_llm_state(self) -> None:  # noqa: PLR0912
        self._llm_pane.restore_state()

    def _persist_llm_state(self) -> None:
        self._llm_pane.persist_state()

    def _refresh_llm_prompt_lists(self) -> None:
        self._llm_pane.refresh_prompt_lists()

    def _populate_llm_prompt_combo(
        self,
        combo: QComboBox,
        snippets: list[Snippet],
        placeholder: str,
        selected_id: str | None,
    ) -> None:
        self._llm_pane.populate_prompt_combo(combo, snippets, placeholder, selected_id)

    def _apply_llm_prompt_selection(
        self,
        combo: QComboBox | None,
        target: QPlainTextEdit | None,
    ) -> None:
        self._llm_pane.apply_prompt_selection(combo, target)

    def _on_llm_system_prompt_selected(self, _index: int) -> None:
        self._llm_pane.on_system_prompt_selected(_index)

    def _on_llm_user_prompt_selected(self, _index: int) -> None:
        self._llm_pane.on_user_prompt_selected(_index)

    def _llm_submit(self) -> None:
        self._llm_pane.submit()

    async def _llm_submit_async(self, payload: PromptPayload) -> None:
        await self._llm_pane.submit_async(payload)

    async def _llm_consume_stream(self, job_id: str) -> None:
        await self._llm_pane.consume_stream(job_id)

    async def _llm_await_result(self, job_id: str) -> None:
        await self._llm_pane.await_result(job_id)

    def _llm_cancel(self) -> None:
        self._llm_pane.cancel()

    async def _llm_cancel_async(self) -> None:
        await self._llm_pane.cancel_async()

    def _llm_clear(self) -> None:
        self._llm_pane.clear()

    def _set_llm_status(self, message: str, *, level: str = "info") -> None:
        self._llm_pane.set_status(message, level=level)

    def _render_llm_status(self, message: str, *, level: str = "info") -> None:
        self._llm_pane.render_status(message, level=level)

    def _set_llm_status_translated(
        self,
        source_text: str,
        *,
        level: str = "info",
        **kwargs: Any,
    ) -> None:
        self._llm_pane.set_status_translated(source_text, level=level, **kwargs)

    @property
    def _llm_provider_combo(self) -> QComboBox | None:
        return self._llm_pane.provider_combo

    @property
    def _llm_model_combo(self) -> QComboBox | None:
        return self._llm_pane.model_combo

    @property
    def _llm_stream_toggle(self) -> QCheckBox | None:
        return self._llm_pane.stream_toggle

    @property
    def _llm_json_toggle(self) -> QCheckBox | None:
        return self._llm_pane.json_toggle

    @property
    def _llm_temperature(self) -> QDoubleSpinBox | None:
        return self._llm_pane.temperature

    @property
    def _llm_max_tokens(self) -> QSpinBox | None:
        return self._llm_pane.max_tokens

    @property
    def _llm_schema_combo(self) -> QComboBox | None:
        return self._llm_pane.schema_combo

    @property
    def _llm_schema_paths(self) -> dict[str, Path]:
        return self._llm_pane.schema_paths

    @property
    def _llm_system_prompt_combo(self) -> QComboBox | None:
        return self._llm_pane.system_prompt_combo

    @property
    def _llm_user_prompt_combo(self) -> QComboBox | None:
        return self._llm_pane.user_prompt_combo

    @property
    def _llm_system_prompt(self) -> QPlainTextEdit | None:
        return self._llm_pane.system_prompt

    @property
    def _llm_user_prompt(self) -> QPlainTextEdit | None:
        return self._llm_pane.user_prompt

    @property
    def _llm_output(self) -> QPlainTextEdit | None:
        return self._llm_pane.output

    @property
    def _llm_submit_button(self) -> QToolButton | None:
        return self._llm_pane.submit_button

    @property
    def _llm_cancel_button(self) -> QToolButton | None:
        return self._llm_pane.cancel_button

    @property
    def _llm_clear_button(self) -> QToolButton | None:
        return self._llm_pane.clear_button

    @property
    def _llm_status(self) -> QLabel | None:
        return self._llm_pane.status

    @property
    def _llm_job_id(self) -> str | None:
        return self._llm_pane.job_id

    @property
    def _llm_task(self) -> asyncio.Task | None:
        return self._llm_pane.task

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

    def set_status_message(self, message: str) -> None:
        self._status_source = None
        self._status_kwargs = {}
        self._render_status_message(message)

    def _render_status_message(self, message: str) -> None:
        self._status.setText(message)

    def set_status_message_translated(self, source_text: str, **kwargs: Any) -> None:
        self._status_source = source_text
        self._status_kwargs = dict(kwargs)
        self._render_status_message(self._ts(source_text).format(**kwargs))

    def _subscribe_text_editor_events(self) -> None:
        if not self._event_bus:
            return
        for event_type in (
            EventTypes.TEXTEDITOR_SNIPPET_CREATED,
            EventTypes.TEXTEDITOR_SNIPPET_UPDATED,
            EventTypes.TEXTEDITOR_SNIPPET_DELETED,
        ):

            def handler(payload, event_type=event_type) -> None:
                self._handle_texteditor_snippet_event(event_type, payload)

            self._event_handlers.append(handler)
            token = self._event_bus.on(event_type, handler)
            self._event_tokens.append(token)

    def _handle_texteditor_snippet_event(self, event_type: str, event: PluginEvent | None) -> None:
        snippet_id: str | None = None
        snippet_data: dict | None = None
        if isinstance(event, PluginEvent):
            payload = event.payload or {}
            snippet_raw = payload.get("snippet")
            if isinstance(snippet_raw, dict):
                snippet_id = snippet_raw.get("id")
                snippet_data = snippet_raw
            if snippet_id is None:
                potential_id = payload.get("snippet_id")
                if isinstance(potential_id, str):
                    snippet_id = potential_id

        # Use differential update when possible
        if event_type == EventTypes.TEXTEDITOR_SNIPPET_DELETED and snippet_id:
            self._catalog.remove_snippet(snippet_id)
            self._prompt_snippets = [s for s in self._prompt_snippets if s.id != snippet_id]
        elif (
            snippet_data
            and snippet_id
            and event_type
            in (
                EventTypes.TEXTEDITOR_SNIPPET_CREATED,
                EventTypes.TEXTEDITOR_SNIPPET_UPDATED,
            )
        ):
            try:
                updated = Snippet.from_dict(snippet_data)
                self._catalog.update_snippet(updated)
                self._prompt_snippets = [s for s in self._prompt_snippets if s.id != updated.id]
                if updated.kind in ALL_SNIPPET_KINDS:
                    self._prompt_snippets.append(updated)
            except (KeyError, TypeError, ValueError):
                logger.debug("Failed to parse snippet from event, falling back to full refresh")
                self.refresh_data()
        else:
            self.refresh_data()

        self._update_library_tag_filter()
        self._refresh_library_list()
        for widget in self._component_widgets:
            widget.refresh_catalog(self._catalog)
        self._refresh_llm_prompt_lists()
        if (
            snippet_id and any(component.snippet_id == snippet_id for component in self._components)
        ) or event_type == EventTypes.TEXTEDITOR_SNIPPET_DELETED:
            self._update_preview()

    def cleanup_subscriptions(self) -> None:
        if self._event_bus:
            for token in self._event_tokens:
                try:
                    self._event_bus.off(token.event, token)
                except Exception:
                    logger.debug("Failed to unsubscribe from event", exc_info=True)
        self._event_tokens.clear()
        self._event_handlers.clear()
        self._cleanup_translation_subscriptions()
        self._flush_pending_library_autosave()
        self.cancel_owned_tasks()
        self._llm_pane.cleanup()

    def _cleanup_translation_subscriptions(self) -> None:
        bus = self._translation_event_bus
        if bus:
            for token in self._translation_tokens:
                try:
                    bus.off(token.event, token)
                except Exception:
                    logger.debug("Failed to unsubscribe from language event", exc_info=True)
        self._translation_tokens.clear()
        self._translation_handlers.clear()

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]  # PySide6 override stub limitation
        self.cleanup_subscriptions()
        super().closeEvent(event)

    def refresh_data(self) -> None:
        try:
            self._catalog.refresh()
        except (OSError, sqlite3.Error, ValueError) as exc:
            logger.error("Failed to refresh prompt catalog: %s", exc, exc_info=True)
            self.set_status_message_translated("Failed to refresh snippets.")
            return
        allowed_kinds = ALL_SNIPPET_KINDS
        try:
            self._prompt_snippets = self._snippet_repo.list_snippets(kinds=allowed_kinds)
        except (OSError, sqlite3.Error, ValueError) as exc:
            logger.error("Failed to refresh prompt snippets: %s", exc, exc_info=True)
            self._prompt_snippets = []
        self._update_library_tag_filter()
        self._refresh_library_list()
        for widget in self._component_widgets:
            widget.refresh_catalog(self._catalog)
        self._update_preview()
        self._refresh_llm_prompt_lists()

    def _handle_palette_double_click(self, item: QListWidgetItem) -> None:
        kind = item.data(Qt.UserRole)
        if not kind:
            return
        self._insert_component(kind, len(self._components))

    def _insert_component(self, kind: str, row: int) -> None:
        component = PromptComponent(kind=kind)
        default = self._catalog.default_snippet(kind)
        if default:
            component.snippet_id = default.id
            if kind == CK_LIST and default.list_items:
                component.list_index = 0
        elif kind == CK_LIST:
            component.list_index = None
        if row < 0 or row > len(self._components):
            self._components.append(component)
        else:
            self._components.insert(row, component)
        self._rebuild_component_widgets()
        self._mark_dirty(True)

    def _expand_prompt_at(self, chip_index: int) -> None:
        """Replace a prompt component with its sub-components."""
        if chip_index < 0 or chip_index >= len(self._components):
            return
        component = self._components[chip_index]
        if component.kind != CK_PROMPT or not component.snippet_id:
            return
        snippet = self._catalog.get(component.snippet_id)
        if not snippet:
            return
        metadata = snippet.metadata or {}
        components_data = metadata.get("components")
        if not isinstance(components_data, list) or not components_data:
            return
        sub = [PromptComponent.from_metadata(entry) for entry in components_data]
        self._components[chip_index : chip_index + 1] = sub
        self._rebuild_component_widgets()
        self._mark_dirty(True)

    def _rebuild_component_widgets(self) -> None:
        if self._chip_flow is None:
            return
        chips = self._chip_flow.rebuild(
            self._components,
            self._catalog,
            self._render_component,
        )
        self._component_widgets = chips
        for chip in chips:
            chip.removed.connect(self._remove_component_widget)
            chip.changed.connect(self._on_component_changed)
            chip.expand_requested.connect(self._on_chip_expand)
        self._update_preview()

    def _remove_component_widget(self, widget: PromptChipWidget) -> None:
        if self._chip_flow is None or widget not in self._component_widgets:
            return
        index = self._chip_flow.remove_chip(widget)
        if 0 <= index < len(self._components):
            self._components.pop(index)
        self._component_widgets = [w for w in self._component_widgets if w is not widget]
        self._update_preview()
        self._mark_dirty(True)

    def _on_component_changed(self, _component: PromptComponent) -> None:
        self._update_preview()
        self._mark_dirty(True)

    def _on_chip_expand(self, widget: PromptChipWidget) -> None:
        if widget in self._component_widgets:
            index = self._component_widgets.index(widget)
            self._expand_prompt_at(index)

    def _on_chip_moved(self, from_index: int, to_index: int) -> None:
        if from_index == to_index:
            return
        comp = self._components.pop(from_index)
        if to_index > from_index:
            to_index -= 1
        self._components.insert(to_index, comp)
        self._rebuild_component_widgets()
        self._mark_dirty(True)

    def _clear_components(self) -> None:
        if not self._components:
            return
        self._components.clear()
        self._rebuild_component_widgets()
        self._mark_dirty(True)

    def _load_prompt(self, snippet: Snippet) -> None:
        self._current_prompt_id = snippet.id
        self._selected_prompt = snippet
        self._title_edit.setText(snippet.title or snippet.id)
        self._tags_edit.setText(", ".join(snippet.tags or []))
        metadata = snippet.metadata or {}
        components_data = metadata.get("components")
        if isinstance(components_data, list):
            components = [PromptComponent.from_metadata(entry) for entry in components_data]
        else:
            components = [PromptComponent(kind=CK_TEXT, text_value=snippet.body or "")]
        if self._separator_edit:
            separator_token = metadata.get("separator")
            block = self._separator_edit.blockSignals(True)
            if isinstance(separator_token, str) and separator_token:
                self._separator_edit.setText(separator_token)
            else:
                self._separator_edit.setText("\n")
            self._separator_edit.blockSignals(block)
        self._components = components
        self._rebuild_component_widgets()
        self._mark_dirty(False)
        self._select_library_prompt(snippet.id)
        self.set_status_message_translated("Loaded prompt '{title}'.", title=snippet.title)

    def _load_selected_to_compose(self) -> None:
        """Load the selected Library prompt into the Compose tab."""
        snippet = self._selected_prompt
        if not snippet:
            self._set_library_status_translated("No prompt selected.")
            return
        if snippet.kind != SK_PROMPT:
            self._set_library_status_translated("Only prompt snippets can be loaded.")
            return
        self._load_prompt(snippet)
        if self._tabs:
            self._tabs.setCurrentIndex(0)

    def _on_library_double_click(self, item: QListWidgetItem) -> None:
        """Double-click on a prompt snippet loads it into Compose."""
        snippet_id = item.data(Qt.UserRole) if item else None
        snippet = self._catalog.get(snippet_id) if snippet_id else None
        if snippet and snippet.kind == SK_PROMPT:
            self._load_prompt(snippet)
            if self._tabs:
                self._tabs.setCurrentIndex(0)

    def _new_prompt(self) -> None:
        self._current_prompt_id = None
        self._title_edit.clear()
        self._tags_edit.clear()
        if self._separator_edit:
            block_sep = self._separator_edit.blockSignals(True)
            self._separator_edit.setText("\n")
            self._separator_edit.blockSignals(block_sep)
        self._components = []
        self._rebuild_component_widgets()
        self._mark_dirty(False)

    def _on_title_changed(self, _text: str) -> None:
        self._mark_dirty(True)

    def _on_tags_changed(self, _text: str) -> None:
        self._mark_dirty(True)

    def _mark_dirty(self, dirty: bool) -> None:
        self._dirty = dirty
        self._update_save_buttons()

    def _update_save_buttons(self) -> None:
        prompt_text = self._compose_preview_text().strip()
        self._save_button.setEnabled(self._dirty and bool(prompt_text))
        self._save_as_button.setEnabled(bool(prompt_text))

    def _save_prompt(self, *, force_new: bool = False) -> None:
        prompt_text = self._compose_preview_text().strip()
        if not prompt_text:
            self.set_status_message_translated("Prompt is empty.")
            return
        title = (
            self._title_edit.text().strip()
            or prompt_text.splitlines()[0][:60]
            or self._ts("Prompt")
        )
        tags = [tag.strip() for tag in self._tags_edit.text().split(",") if tag.strip()]
        components_meta = [component.to_metadata() for component in self._components]
        snippet_id = (
            self._current_prompt_id
            if self._current_prompt_id and not force_new
            else f"prompt-{uuid.uuid4().hex[:12]}"
        )
        snippet = Snippet(
            id=snippet_id,
            title=title,
            body=prompt_text,
            tags=tags,
            category=SK_PROMPT,
            favorite=False,
            metadata={
                "components": components_meta,
                "tags": tags,
                "separator": self._separator_edit.text() if self._separator_edit else "\n",
            },
            kind=SK_PROMPT,
            list_items=tuple(),
        )
        saved = self._snippet_repo.save_snippet(snippet)
        try:
            self._store.extend([prompt_text])
        except Exception:
            logger.debug("Prompt store update failed", exc_info=True)
        self._current_prompt_id = saved.id
        self.refresh_data()
        self._selected_prompt = saved
        self._select_library_prompt(saved.id)
        self._mark_dirty(False)
        self.set_status_message_translated("Saved prompt '{title}'.", title=saved.title)

    def _delete_prompt(self, prompt_id: str | None = None) -> None:
        target_id = prompt_id or self._current_prompt_id
        if not target_id:
            return
        snippet = self._catalog.get(target_id)
        title = snippet.title if snippet else (self._title_edit.text().strip() or target_id)
        confirm = QMessageBox.question(
            self,
            self._ts("Delete prompt"),
            self._ts("Delete '{title}'?").format(title=title),
        )
        if confirm != QMessageBox.Yes:
            return
        self._snippet_repo.delete_snippet(target_id)
        if self._current_prompt_id == target_id:
            self._current_prompt_id = None
            self._new_prompt()
        if self._selected_prompt and self._selected_prompt.id == target_id:
            self._selected_prompt = None
        self.refresh_data()
        self.set_status_message_translated("Deleted prompt '{title}'.", title=title)
        self._set_library_status_translated("Deleted prompt '{title}'.", title=title)

    _ESCAPE_MAP: ClassVar[dict[str, str]] = {
        r"\n": "\n",
        r"\t": "\t",
        r"\r": "\r",
        r"\\": "\\",
    }

    def _current_separator(self) -> str:
        if self._separator_edit is None:
            return "\n"
        raw = self._separator_edit.text()
        if not raw:
            return "\n"
        result = raw
        for escape, char in self._ESCAPE_MAP.items():
            result = result.replace(escape, char)
        return result

    def _compose_preview_text(self) -> str:
        separator = self._current_separator()
        parts = [self._render_component(component) for component in self._components]
        return separator.join(part for part in parts if part)

    def _render_component(self, component: PromptComponent) -> str:
        if component.kind == CK_TEXT:
            return component.text_value.strip()
        snippet = self._catalog.get(component.snippet_id)
        if snippet is None:
            if component.snippet_id:
                logger.warning("Snippet not found: %s", component.snippet_id)
            return ""
        if component.kind in {CK_STRING, CK_PROMPT}:
            return (snippet.body or snippet.title or "").strip()
        if component.kind == CK_LIST:
            items = _resolve_list_items(snippet)
            index = component.list_index if component.list_index is not None else 0
            if 0 <= index < len(items):
                return items[index].strip()
        return ""

    def _update_preview(self) -> None:
        # Update separator display in chip flow
        if self._chip_flow and self._separator_edit:
            raw = self._separator_edit.text() or "\\n"
            # Show a compact visual separator (replace escape sequences)
            visual = raw.replace("\\n", ", ").replace("\\t", " ").replace("\\r", "")
            if not visual.strip():
                visual = ", "
            self._chip_flow.set_separator_text(visual)
        self._update_save_buttons()

    def _on_separator_changed(self, _text: str) -> None:
        self._update_preview()
        self._mark_dirty(True)

    def _handle_copy(self) -> None:
        QApplication.clipboard().setText(self._compose_preview_text())
        self.set_status_message_translated("Prompt copied to clipboard.")


class PromptEditorPanelPlugin(UIPanelPlugin):
    """UIPanel plugin wiring for the Prompt Editor."""

    metadata = PluginMetadata(
        id="prompt_editor_panel",
        name=_plugin_ts("Prompt Editor"),
        version="0.3.0",
        description=_plugin_ts("Compose prompts, manage a library, and submit to LLM."),
        author="LoomStroke Team",
        license="MIT",
        types=[PluginType.UI_PANEL],
    )

    def __init__(self) -> None:
        super().__init__(self.metadata)
        self._host: HostAPI | None = None
        self._store: PromptSnippetStore | None = None
        self._widget: PromptEditorPanel | None = None
        self._event_bus: ExtendedEventBus | None = None
        self._composer: PromptComposer | None = None
        self._event_handler: PromptEditorEventHandler | None = None
        self._snippet_event_handler: SnippetEventHandler | None = None
        self._comfyui: ComfyUIBridge | None = None
        self._llm_client: LLMGatewayClient | None = None

    def descriptor(self) -> PanelDescriptor:
        return PanelDescriptor(
            id="prompt_editor",
            display_name=_plugin_ts("Prompt Editor"),
            category="text_tools",
            icon_svg=self._icon_svg(),
            default_position="right",
            default_size=_PROMPT_EDITOR_DEFAULT_SIZE,
            resizable=True,
        )

    def activate(self, host_api: HostAPI) -> None:
        super().activate(host_api)
        self._host = host_api
        settings = getattr(host_api, "settings", None)
        if settings is None:
            raise RuntimeError("Host settings unavailable for Prompt Editor")
        self._store = PromptSnippetStore(settings)
        event_bus = getattr(host_api, "events", None)
        if not isinstance(event_bus, ExtendedEventBus):
            event_bus = get_extended_event_bus()
        self._event_bus = event_bus
        self._composer = PromptComposer(event_bus) if event_bus else None
        if self._event_handler:
            try:
                self._event_handler.cleanup()
            except Exception:
                logger.warning(
                    "PromptEditor event handler cleanup on activate failed",
                    exc_info=True,
                )
        self._event_handler = (
            PromptEditorEventHandler(event_bus, self._composer)
            if event_bus and self._composer
            else None
        )
        self._comfyui = ComfyUIBridge(event_bus) if event_bus else None
        self._llm_client = LLMGatewayClient(host_api, settings, None)
        # Snippet request handlers (migrated from TextEditor)
        if self._snippet_event_handler:
            try:
                self._snippet_event_handler.cleanup()
            except Exception:
                logger.warning("Snippet event handler cleanup failed", exc_info=True)
        snippet_repo = SnippetRepository(settings)
        self._snippet_event_handler = (
            SnippetEventHandler(event_bus, snippet_repo) if event_bus else None
        )
        if self._widget:
            self._widget.cleanup_subscriptions()
            self._widget.deleteLater()
            self._widget = None

    def deactivate(self) -> None:
        if self._widget:
            try:
                self._widget.cleanup_subscriptions()
            except Exception:
                logger.warning("PromptEditorPanel subscription cleanup failed", exc_info=True)
            try:
                self._widget.deleteLater()
            except Exception:
                logger.warning("PromptEditorPanel deleteLater failed", exc_info=True)
            self._widget = None
        self._host = None
        self._store = None
        self._event_bus = None
        self._composer = None
        if self._event_handler:
            try:
                self._event_handler.cleanup()
            except Exception:
                logger.warning(
                    "PromptEditor event handler cleanup on deactivate failed",
                    exc_info=True,
                )
        self._event_handler = None
        if self._snippet_event_handler:
            try:
                self._snippet_event_handler.cleanup()
            except Exception:
                logger.warning("Snippet event handler cleanup failed", exc_info=True)
        self._snippet_event_handler = None
        self._comfyui = None
        self._llm_client = None
        super().deactivate()

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        if not self._host or not self._store:
            raise RuntimeError("Prompt Editor plugin not activated")
        if self._widget is None:
            self._widget = PromptEditorPanel(
                self._store,
                self._host,
                event_bus=self._event_bus,
                composer=self._composer,
                comfyui_bridge=self._comfyui,
                llm_client=self._llm_client,
                parent=parent,
            )
        return self._widget

    def on_panel_shown(self) -> None:
        if self._widget:
            self._widget.refresh_data()
            self._widget.set_status_message_translated("Panel ready.")

    @staticmethod
    def _icon_svg() -> str:
        return (
            "<svg viewBox='0 0 24 24' xmlns='http://www.w3.org/2000/svg'>"
            "<rect x='3' y='4' width='18' height='16' rx='2' fill='#1e293b'/>"
            "<path d='M7 8h10v2H7zm0 4h10v2H7zm0 4h6v2H7z' fill='white'/>"
            "</svg>"
        )


def create_plugin() -> PromptEditorPanelPlugin:
    """Factory used by plugin loader."""

    return PromptEditorPanelPlugin()
