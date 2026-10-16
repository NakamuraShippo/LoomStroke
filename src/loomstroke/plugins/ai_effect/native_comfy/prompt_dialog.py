"""Reuse the existing Effects prompt composer for native node text widgets."""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from loomstroke.plugins.ai_effect.ui.param_widgets import StringParamWidget

from .panel import tr
from .values import parameter_bindings, snapshot_values


class PromptDialog(QDialog):
    def __init__(self, panel: Any, entries: list[dict], values: dict, ui: dict) -> None:
        super().__init__(panel)
        self.setModal(True)
        self.resize(640, 520)
        layout = QVBoxLayout(self)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        contents = QWidget(scroll)
        form = QFormLayout(contents)
        self.editors = {}
        for entry in entries:
            key = entry["internal_id"]
            editor = StringParamWidget(
                dict(entry, required_text=True), contents, host_api=panel.section._host
            )
            editor.set_value(values[key])
            saved = ui.get(key, {})
            live = saved.get("live_prompt", {})
            # Native edits take precedence over a previously composed string.
            if live.get("composed_text") == values[key]:
                editor.set_ui_state(copy.deepcopy(saved))
                if editor.get_value() != values[key]:
                    editor.set_value(values[key])
            self.editors[key] = editor
            form.addRow(entry.get("display_name") or key, editor)
        scroll.setWidget(contents)
        layout.addWidget(scroll)
        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel, self
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        self.retranslate()

    def retranslate(self) -> None:
        self.setWindowTitle(tr("Prompt input…"))
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText(tr("Apply to node"))
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText(tr("Cancel"))

    def changeEvent(self, event: Any) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange:
            self.retranslate()


async def edit_prompt(panel: Any) -> None:
    if panel.view is None or not panel.save_button.isEnabled():
        raise ValueError(tr("Open or load a workflow first."))
    state = panel.current_state()
    before = copy.deepcopy(state.param_values)
    bindings = parameter_bindings(state)
    node_id = ":".join(panel.nodes.currentData() or [])
    entries = [
        entry
        for entry in state.preset_snapshot.get("manifest", {}).get("parameters", [])
        if entry.get("type") == "string"
        and str(bindings.get(entry["internal_id"], {}).get("node_id")) == node_id
    ]
    if not entries:
        raise ValueError(tr("This node has no published text parameters."))
    snapshot = await panel.view.call("snapshot")
    state = panel.current_state()
    if state.param_values != before:
        raise ValueError(tr("Layer values changed while reading nodes. Reopen the node editor."))
    values = snapshot_values(state, snapshot["output"])
    dialog = PromptDialog(panel, entries, values, state.param_ui_state)
    finished = asyncio.get_running_loop().create_future()
    dialog.finished.connect(
        lambda result: finished.set_result(result) if not finished.done() else None
    )
    try:
        panel.view.setEnabled(False)
        dialog.open()
        if await finished != QDialog.DialogCode.Accepted:
            return
        if panel.current_state().param_values != before:
            raise ValueError(
                tr("Layer values changed while reading nodes. Reopen the node editor.")
            )
        updates = [
            dict(bindings[key], value=editor.get_value()) for key, editor in dialog.editors.items()
        ]
        await panel.view.evaluate("window.loomstrokeApplyValues(" + json.dumps(updates) + ")")
        if panel.current_state().param_values != before:
            raise ValueError(
                tr("Layer values changed while reading nodes. Reopen the node editor.")
            )
        await panel.commit(
            ui_updates={key: editor.get_ui_state() for key, editor in dialog.editors.items()}
        )
    finally:
        dialog.close()
        dialog.deleteLater()
        if panel.view is not None:
            panel.view.setEnabled(True)
