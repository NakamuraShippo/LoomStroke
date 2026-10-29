"""Chip-based prompt composer widget for ComfyUI node parameters.

Embeds the Prompt Editor's LivePrompt chip-flow UI to allow users to
load a prompt composition (saved from Prompt Editor) and tweak each
component individually via inline chip editing.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.plugins.common.constants import (
    CK_LIST,
    CK_PROMPT,
    CK_STRING,
    CK_TEXT,
    SK_PROMPT,
)
from loomstroke.plugins.common.snippet_repository import SnippetRepository
from loomstroke.plugins.prompt_editor.compose_widgets import (
    PromptChipFlowWidget,
    PromptChipWidget,
)
from loomstroke.plugins.prompt_editor.dynamic_prompt import DynamicPromptSequence
from loomstroke.plugins.prompt_editor.panel_plugin import (
    PromptComponent,
    SnippetCatalog,
    _resolve_list_items,
)
from loomstroke.plugins.prompt_editor.resizable_prompt_area import ResizablePromptArea
from loomstroke.ui.translations import translated_literal_domain

if TYPE_CHECKING:
    from PySide6.QtGui import QShowEvent

    from loomstroke.plugins.common.snippet_models import Snippet

logger = logging.getLogger(__name__)


def _tr(text: str) -> str:
    return translated_literal_domain("PromptComposer", text, use_fallback=True)


_ESCAPE_MAP: dict[str, str] = {
    r"\n": "\n",
    r"\t": "\t",
    r"\r": "\r",
    r"\\": "\\",
}


class PromptComposerWidget(QWidget):
    """Chip-based prompt composer that wraps PromptChipFlowWidget.

    Provides:
    - A composition selector combo (loads prompt snippets with components)
    - A chip flow area for inline editing of each component
    - A separator input
    - A read-only preview of the composed text

    When the user selects "(None)", the widget hides the chip flow and
    shows the fallback plain text editor instead.
    """

    composedTextChanged = Signal(str)
    """Emitted whenever the composed prompt text changes."""

    modeChanged = Signal(bool)
    """Emitted when switching between composer (True) and flat-text (False) mode."""

    def __init__(
        self,
        host_api: Any | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._host_api = host_api

        # Data
        self._components: list[PromptComponent] = []
        self._component_widgets: list[PromptChipWidget] = []
        self._catalog: SnippetCatalog | None = None
        self._snippet_repo: SnippetRepository | None = None
        self._catalog_initialized = False
        self._dynamic_sequence = DynamicPromptSequence()

        self._build_ui()
        # Defer catalog / combo population until the widget is first shown
        # to avoid blocking the UI thread during panel construction.

    # ------------------------------------------------------------------
    # Lazy initialisation
    # ------------------------------------------------------------------

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self._ensure_catalog()

    def _ensure_catalog(self) -> None:
        """Initialise catalog and populate combo on first access."""
        if self._catalog_initialized:
            return
        self._catalog_initialized = True
        self._init_catalog()
        self._populate_composition_combo()
        if self._components:
            self._rebuild_chips()
            self._recompose()

    # ------------------------------------------------------------------
    # UI Construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(4)

        # --- Composition selector row ---
        selector_row = QHBoxLayout()
        selector_row.setContentsMargins(0, 0, 0, 0)
        selector_row.setSpacing(4)

        self._composition_combo = QComboBox()
        self._composition_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        selector_row.addWidget(self._composition_combo)

        refresh_btn = QToolButton()
        refresh_btn.setText("\u21bb")
        refresh_btn.setToolTip(_tr("Refresh compositions"))
        refresh_btn.clicked.connect(self._on_refresh)
        selector_row.addWidget(refresh_btn)

        root.addLayout(selector_row)

        # --- Chip flow ---
        self._chip_flow = PromptChipFlowWidget()
        self._chip_flow.chipInserted.connect(self._on_chip_inserted)
        self._chip_flow.chipMoved.connect(self._on_chip_moved)
        self._flow_area = ResizablePromptArea(
            self._chip_flow, getattr(self._host_api, "settings", None)
        )
        root.addWidget(self._flow_area)

        # --- Separator row ---
        self._separator_container = QWidget()
        sep_row = QHBoxLayout(self._separator_container)
        sep_row.setContentsMargins(0, 0, 0, 0)
        sep_row.setSpacing(4)
        sep_label = QLabel(_tr("Separator:"))
        sep_label.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        sep_row.addWidget(sep_label)

        self._separator_edit = QLineEdit(r"\n")
        self._separator_edit.setMaximumWidth(120)
        self._separator_edit.textChanged.connect(self._on_separator_changed)
        sep_row.addWidget(self._separator_edit)
        sep_row.addStretch()
        root.addWidget(self._separator_container)

        # --- Composer area container (chip flow + separator) ---
        self._composer_widgets = [self._flow_area, self._separator_container]

        # Connect combo
        self._composition_combo.currentIndexChanged.connect(self._on_composition_selected)

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------

    def _init_catalog(self) -> None:
        settings = getattr(self._host_api, "settings", None) if self._host_api else None
        try:
            self._snippet_repo = SnippetRepository(settings, db_path=None)
            self._catalog = SnippetCatalog(self._snippet_repo)
        except Exception:
            logger.debug("Failed to initialise SnippetCatalog for PromptComposer", exc_info=True)
            self._catalog = None

    # ------------------------------------------------------------------
    # Composition combo
    # ------------------------------------------------------------------

    def _populate_composition_combo(self) -> None:
        combo = self._composition_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(_tr("(None)"), None)

        if self._snippet_repo:
            try:
                prompts = self._snippet_repo.list_snippets(kinds=[SK_PROMPT])
                prompts.sort(key=lambda s: (s.title or s.id).lower())
                for snippet in prompts:
                    display = snippet.title or snippet.id
                    combo.addItem(display, snippet.id)
            except Exception:
                logger.debug("Failed to load prompt compositions", exc_info=True)

        combo.blockSignals(False)

    def _on_refresh(self) -> None:
        self._ensure_catalog()
        if self._catalog:
            self._catalog.refresh()
        self._populate_composition_combo()

    def _on_composition_selected(self, index: int) -> None:
        snippet_id = self._composition_combo.itemData(index)
        if snippet_id is None:
            self._dynamic_sequence.reset()
            # Switch to flat-text mode
            self._components.clear()
            self._rebuild_chips()
            self._set_composer_visible(False)
            self.modeChanged.emit(False)
            self.composedTextChanged.emit("")
            return

        # Load composition
        snippet = self._catalog.get(snippet_id) if self._catalog else None
        if snippet is None:
            return
        self._set_composer_visible(True)
        # A composed-text callback can synchronously persist and restore this
        # widget. Publish the active mode before it snapshots the new chips.
        self.modeChanged.emit(True)
        self._load_composition(snippet)

    def _load_composition(self, snippet: Snippet) -> None:
        self._dynamic_sequence.reset()
        metadata = snippet.metadata or {}
        components_data = metadata.get("components")
        if isinstance(components_data, list):
            self._components = [PromptComponent.from_metadata(entry) for entry in components_data]
        else:
            self._components = [PromptComponent(kind=CK_TEXT, text_value=snippet.body or "")]
        # Restore separator
        separator_token = metadata.get("separator")
        block = self._separator_edit.blockSignals(True)
        if isinstance(separator_token, str) and separator_token:
            self._separator_edit.setText(separator_token)
        else:
            self._separator_edit.setText(r"\n")
        self._separator_edit.blockSignals(block)

        self._rebuild_chips()
        self._recompose()

    def liveprompt_state(self) -> dict[str, Any]:
        """Return LoomStroke-only LivePrompt UI state for persistence.

        The returned dict describes the chip composition UI. Gateway payloads
        must continue to use the flattened text emitted via
        ``composedTextChanged`` / ``param_values``.
        """
        data: dict[str, Any] = {
            "version": 1,
            "active": self.is_composer_active(),
            "separator": self._separator_edit.text(),
            "components": [component.to_metadata() for component in self._components],
            "composed_text": self._compose_text(),
        }
        composition_id = self._composition_combo.currentData()
        if isinstance(composition_id, str) and composition_id:
            data["composition_id"] = composition_id
        return data

    def restore_liveprompt_state(self, state: Mapping[str, Any]) -> str:
        """Restore a previously saved LivePrompt composition.

        Returns the flattened composed text so the embedding widget can keep
        ``get_value()`` aligned with the restored chip state without writing
        this metadata into any ComfyUI workflow JSON.
        """
        self._ensure_catalog()

        raw_components = state.get("components")
        components: list[PromptComponent] = []
        if isinstance(raw_components, list):
            for entry in raw_components:
                if isinstance(entry, Mapping):
                    components.append(PromptComponent.from_metadata(dict(entry)))
        separator = state.get("separator")
        separator = separator if isinstance(separator, str) else r"\n"
        components_changed = components != self._components
        if components_changed or separator != self._separator_edit.text():
            self._dynamic_sequence.reset()
        self._components = components if components_changed else self._components
        previous_sep_block = self._separator_edit.blockSignals(True)
        try:
            self._separator_edit.setText(separator if isinstance(separator, str) else r"\n")
        finally:
            self._separator_edit.blockSignals(previous_sep_block)

        active = bool(state.get("active", False))
        composition_id = state.get("composition_id")
        combo = self._composition_combo
        previous_combo_block = combo.blockSignals(True)
        try:
            if active:
                data = composition_id if isinstance(composition_id, str) else "__saved_liveprompt__"
                index = combo.findData(data)
                if index < 0:
                    label = (
                        composition_id
                        if isinstance(composition_id, str)
                        else _tr("Saved LivePrompt")
                    )
                    combo.addItem(label, data)
                    index = combo.count() - 1
                combo.setCurrentIndex(index)
            else:
                none_index = combo.findData(None)
                if none_index >= 0:
                    combo.setCurrentIndex(none_index)
        finally:
            combo.blockSignals(previous_combo_block)

        self._set_composer_visible(active or bool(components))
        # Keep both widget and component identities while the canvas echoes an
        # identical state. Replacing them closes the editor popup mid-click.
        if components_changed or len(self._component_widgets) != len(self._components):
            self._rebuild_chips()
        composed = self._compose_text()
        fallback = state.get("composed_text")
        if isinstance(fallback, str) and (
            not composed or any(c.selection_mode != "fixed" for c in components)
        ):
            composed = fallback
        return composed

    # ------------------------------------------------------------------
    # Chip management
    # ------------------------------------------------------------------

    def _rebuild_chips(self) -> None:
        if self._catalog is None:
            return
        chips = self._chip_flow.rebuild(
            self._components,
            self._catalog,
            self._render_component,
        )
        self._component_widgets = chips
        for chip in chips:
            chip.removed.connect(self._on_chip_removed)
            chip.changed.connect(self._on_chip_changed)
            chip.expand_requested.connect(self._on_chip_expand)
        self._update_separator_display()

    def _on_chip_inserted(self, kind: str, row: int) -> None:
        if self._catalog is None:
            return
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
        self._rebuild_chips()
        self.modeChanged.emit(True)
        self._recompose()

    def _on_chip_moved(self, from_index: int, to_index: int) -> None:
        if from_index == to_index:
            return
        comp = self._components.pop(from_index)
        if to_index > from_index:
            to_index -= 1
        self._components.insert(to_index, comp)
        self._rebuild_chips()
        self._recompose()

    def _on_chip_removed(self, widget: PromptChipWidget) -> None:
        if widget not in self._component_widgets:
            return
        index = self._chip_flow.remove_chip(widget)
        if 0 <= index < len(self._components):
            self._components.pop(index)
        self._component_widgets = [w for w in self._component_widgets if w is not widget]
        self.modeChanged.emit(self.is_composer_active())
        self._recompose()

    def _on_chip_changed(self, _component: PromptComponent) -> None:
        self._dynamic_sequence.reset()
        self._recompose()

    def _on_chip_expand(self, widget: PromptChipWidget) -> None:
        if widget not in self._component_widgets:
            return
        chip_index = self._component_widgets.index(widget)
        if chip_index < 0 or chip_index >= len(self._components):
            return
        component = self._components[chip_index]
        if component.kind != CK_PROMPT or not component.snippet_id:
            return
        if self._catalog is None:
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
        self._rebuild_chips()
        self._recompose()

    # ------------------------------------------------------------------
    # Composition / rendering
    # ------------------------------------------------------------------

    def _render_component(self, component: PromptComponent) -> str:
        if component.kind == CK_TEXT:
            return component.text_value.strip()
        if self._catalog is None:
            return ""
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

    def _current_separator(self) -> str:
        raw = self._separator_edit.text()
        if not raw:
            return "\n"
        result = raw
        for escape, char in _ESCAPE_MAP.items():
            result = result.replace(escape, char)
        return result

    def _compose_text(self) -> str:
        separator = self._current_separator()
        parts = [self._render_component(c) for c in self._components]
        return separator.join(part for part in parts if part)

    def resolve_for_generation(self) -> str:
        """Resolve one submission without consuming choices during preview/save."""
        return self._dynamic_sequence.generate(
            self._components,
            self._catalog,
            self._render_component,
            _resolve_list_items,
            self._current_separator(),
        )

    def _recompose(self) -> None:
        self.composedTextChanged.emit(self._compose_text())

    def _on_separator_changed(self, _text: str) -> None:
        self._update_separator_display()
        self._recompose()

    def _update_separator_display(self) -> None:
        raw = self._separator_edit.text() or r"\n"
        visual = raw.replace(r"\n", ", ").replace(r"\t", " ").replace(r"\r", "")
        if not visual.strip():
            visual = ", "
        self._chip_flow.set_separator_text(visual)

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    def _set_composer_visible(self, visible: bool) -> None:
        for widget in self._composer_widgets:
            widget.setVisible(visible)

    def is_composer_active(self) -> bool:
        """Return True for a saved composition or manually added live chips."""
        return self._composition_combo.currentData() is not None or bool(self._components)
