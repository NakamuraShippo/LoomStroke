"""Resident LivePrompt editors for native nodes with published text parameters."""

from __future__ import annotations

import copy
from typing import Any

from PySide6.QtWidgets import (
    QButtonGroup,
    QFormLayout,
    QHBoxLayout,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from loomstroke.plugins.ai_effect.ui.param_widgets import StringParamWidget

from .values import parameter_bindings

LIVE = "live"
TEXT = "text"
# Saved beside ``live_prompt`` when the user chose the node text for this input.
TEXT_MODE = "text_mode"
_TOGGLE_STYLE = (
    "QPushButton:checked { background: palette(highlight); color: palette(highlighted-text); }"
)


def _restorable(saved: Any, value: Any) -> bool:
    """A saved composition is shown only while it still matches the node text."""
    live = saved.get("live_prompt") if isinstance(saved, dict) else None
    return (
        isinstance(live, dict)
        and bool(live.get("active") or live.get("has_input"))
        and live.get("composed_text") == value
    )


class PromptArea(QWidget):
    """Edit published string inputs in the panel instead of the ComfyUI node.

    LivePrompt mode hides the webview for the selected node and the values
    reach the node on the panel's Apply/generate commit, as the former dialog
    did on OK. Text mode leaves the node text as the source.
    """

    def __init__(self, panel: Any) -> None:
        super().__init__(panel)
        self.panel = panel
        self.editors: dict[str, StringParamWidget] = {}
        self.node_keys: dict[str, list[str]] = {}
        self.modes: dict[str, str] = {}
        self._disabled: set[str] = set()
        self._loaded: dict[str, Any] = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        toggle = QHBoxLayout()
        self.live_button = panel.button("Live prompt")
        self.text_button = panel.button("Text (node)")
        self.group = QButtonGroup(self)
        for button in (self.live_button, self.text_button):
            button.setCheckable(True)
            button.setStyleSheet(_TOGGLE_STYLE)
            self.group.addButton(button)
            toggle.addWidget(button)
        toggle.addStretch(1)
        self.live_button.clicked.connect(lambda: self.set_mode(LIVE))
        self.text_button.clicked.connect(lambda: self.set_mode(TEXT))
        layout.addLayout(toggle)
        self.pages = QStackedWidget(self)
        layout.addWidget(self.pages)
        self._pages: dict[str, QWidget] = {}
        self.hide()

    def clear(self) -> None:
        self.hide()
        self.editors.clear()
        self.node_keys.clear()
        self.modes.clear()
        self._disabled.clear()
        self._loaded.clear()
        self._pages.clear()
        while self.pages.count():
            page = self.pages.widget(0)
            self.pages.removeWidget(page)
            page.hide()
            page.deleteLater()

    def rebuild(self, state: Any, skip: Any = ()) -> None:
        """Build editors for string inputs of nodes not rendered by Qt forms."""
        self.clear()
        bindings = parameter_bindings(state)
        for entry in state.preset_snapshot.get("manifest", {}).get("parameters", []):
            key = entry.get("internal_id")
            if entry.get("type") != "string" or key not in bindings or key in skip:
                continue
            node = str(bindings[key]["node_id"])
            page = self._pages.get(node)
            if page is None:
                page = QWidget(self.pages)
                QFormLayout(page).setFieldGrowthPolicy(
                    QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow
                )
                self._pages[node] = page
                self.pages.addWidget(page)
            editor = StringParamWidget(
                dict(entry, required_text=True), page, host_api=self.panel.section._host
            )
            page.layout().addRow(entry.get("display_name") or key, editor)
            self.editors[key] = editor
            self.node_keys.setdefault(node, []).append(key)
        self._load(state.param_values, state.param_ui_state)

    def _load(self, values: dict, ui: dict) -> None:
        for node, keys in self.node_keys.items():
            live = False
            for key in keys:
                editor = self.editors[key]
                editor.set_value(values.get(key))
                saved = ui.get(key, {})
                # Native edits take precedence over a previously composed string.
                if _restorable(saved, values.get(key)):
                    editor.set_ui_state(copy.deepcopy(saved))
                    if editor.get_value() != values.get(key):
                        editor.set_value(values.get(key))
                    live = live or not saved.get(TEXT_MODE)
                self._loaded[key] = copy.deepcopy(values.get(key))
            self.modes[node] = LIVE if live else TEXT
        self._disabled.clear()

    def sync(self, state: Any) -> None:
        """Reload when the layer values changed outside these editors."""
        if any(state.param_values.get(k) != v for k, v in self._loaded.items()):
            self._load(state.param_values, state.param_ui_state)
            self.apply_visibility()

    def committed(self, values: dict, ui: dict) -> None:
        """Show what the commit saved, including choices advanced for generation."""
        self._load(values, ui)
        self.apply_visibility()

    def current_node(self) -> str | None:
        path = self.panel.nodes.currentData()
        node = ":".join(path) if path else None
        return node if node in self.node_keys else None

    def set_mode(self, mode: str) -> None:
        node = self.current_node()
        if node is None:
            return
        self.modes[node] = mode
        if mode == TEXT:
            self._disabled.update(self.node_keys[node])
        else:
            self._disabled.difference_update(self.node_keys[node])
        self.apply_visibility()

    def apply_visibility(self) -> None:
        """Called by QtNodeForms.select after it shows the webview for this node."""
        node = self.current_node()
        if node is None or node in self.panel.qt_forms.pages:
            self.hide()
            return
        live = self.modes.get(node) == LIVE
        self.live_button.setChecked(live)
        self.text_button.setChecked(not live)
        self.pages.setCurrentWidget(self._pages[node])
        self.pages.setVisible(live)
        self.show()
        if self.panel.view is not None:
            self.panel.view.setVisible(not live)

    def node_updates(self, state: Any) -> list[dict]:
        live = [node for node in self.node_keys if self.modes.get(node) == LIVE]
        keys = [key for node in live for key in self.node_keys[node]]
        if not keys:
            return []
        bindings = parameter_bindings(state)
        return [dict(bindings[key], value=self.editors[key].get_value()) for key in keys]

    def ui_updates(self, ui: dict) -> dict:
        updates: dict = {}
        for node, keys in self.node_keys.items():
            for key in keys:
                if self.modes.get(node) == LIVE:
                    updates[key] = self.editors[key].get_ui_state()
                elif key in self._disabled and isinstance(ui.get(key), dict):
                    # Keep the composition but let the node text win at generation.
                    updates[key] = dict(copy.deepcopy(ui[key]), **{TEXT_MODE: True})
        return updates
