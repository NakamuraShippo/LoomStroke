"""Native node editor bound to one AI Effect layer draft."""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any

from PySide6.QtCore import QEvent, QTimer
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel

from loomstroke.plugins.comfyui.server_registry import get_registry
from loomstroke.plugins.comfyui.session_target import active_url

from .layer_cache import identity, take, target_url
from .live_prompt_choices import NativeLivePromptChoices
from .panel import NativePanel, tr
from .qt_node_forms import QtNodeForms
from .seed_control import advance_controls
from .values import layer_document, parameter_bindings, prompt_ui_state, snapshot_values


class ComfyLayerPanel(NativePanel):
    def __init__(self, section: Any, state: Any, parent: Any) -> None:
        super().__init__(parent)
        self.section = section
        self.layer_id = section._current_ai_effect_id
        self._synced = copy.deepcopy(state.param_values)
        self._auto_open_attempted = False
        self._nodes_loaded = False
        self._focus_path: list[str] | None = None
        self._focus_task: Any = None
        self._session_timer = QTimer(self)
        self._session_timer.setInterval(1000)
        self._session_timer.timeout.connect(self.sync_connection)
        self._session_timer.start()
        self.nodes = QComboBox(self)
        self.nodes.currentIndexChanged.connect(self.focus_node)
        bar = QHBoxLayout()
        self.open_button = self.button("Reload nodes")
        self.open_button.clicked.connect(self.request_reload)
        self.save_button = self.button("Apply values")
        self.save_button.setEnabled(False)
        self.save_button.clicked.connect(lambda: self.run(self.commit()))
        bar.addWidget(self.open_button)
        bar.addWidget(self.save_button)
        self.prompt_button = self.button("Prompt input…")
        self.prompt_button.clicked.connect(self.open_prompt)
        bar.addWidget(self.prompt_button)
        self.body.insertLayout(0, bar)
        self.body.insertWidget(1, self.nodes)
        self.classification_label = QLabel(self)
        self.body.insertWidget(2, self.classification_label)
        self.qt_forms = QtNodeForms(self)
        self.live_prompt_choices = NativeLivePromptChoices(self)
        self.body.insertWidget(3, self.qt_forms)
        self.status.setText(
            tr(
                "Apply values before generating or changing layers. "
                "Only published parameters are applied."
            )
        )

    def showEvent(self, event: Any) -> None:  # noqa: N802
        super().showEvent(event)
        if self._auto_open_attempted:
            return
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        self._auto_open_attempted = True
        self.run(self.open_nodes())

    def changeEvent(self, event: Any) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.LanguageChange and hasattr(self, "qt_forms"):
            self.qt_forms.retranslate()

    def current_state(self) -> Any:
        layer_id, state = self.section._get_selected_ai_effect()
        if layer_id != self.layer_id or state is None:
            raise ValueError(tr("The selected layer changed. Reopen its node editor."))
        return state

    def sync_connection(self) -> None:
        url = active_url(get_registry())
        if (
            self.isVisible()
            and not self._busy
            and self.view is not None
            and url
            and self.view.url.rstrip("/") != url.rstrip("/")
        ):
            self.run(self.reload_nodes())

    def request_reload(self) -> None:
        if self._busy:
            self.status.setText(tr("An operation is in progress. Wait before reloading nodes."))
            return
        self.run(self.reload_nodes())

    async def reload_nodes(self) -> None:
        self.current_state()
        getattr(self, "_layer_drafts", {}).pop(self.layer_id, None)
        self.clear_nodes()
        self.dispose_view()
        await self.open_nodes()

    def clear_nodes(self) -> None:
        self._nodes_loaded = False
        self._focus_path = None
        self.qt_forms.clear()
        self.live_prompt_choices.clear()
        self.classification_label.clear()
        self.save_button.setEnabled(False)
        self.nodes.blockSignals(True)
        self.nodes.clear()
        self.nodes.blockSignals(False)

    async def open_nodes(self) -> None:
        self.clear_nodes()
        state = self.current_state()
        gateway = state.preset_snapshot.get("gateway_file", {})
        server_id = state.target_server_id or gateway.get("target_server_id") or "local"
        server = get_registry().get(server_id)
        url = active_url(get_registry()) or (server.url if server else gateway.get("endpoint_url"))
        if not url:
            raise ValueError(tr("Select a ComfyUI server."))
        if self.view is not None and self.view.url.rstrip("/") != url.rstrip("/"):
            self.dispose_view()
            self._layer_drafts = {}
        self._session_url = url.rstrip("/")
        view = await self.connect_view(url)
        from .layer_session import draft_for  # noqa: PLC0415

        draft = draft_for(self, state)
        method, workflow = layer_document(state)
        if draft is not None:
            method, workflow = "load", draft[2]["workflow"]
        await view.call(method, workflow)
        await view.call("setMode", "parameters")
        if draft is None:
            await self.push_values(state)
        elif draft[1] != state.param_values:
            await self.push_values(state, previous_values=draft[1])
        else:
            self._synced = copy.deepcopy(state.param_values)
        info = await view.call("describe", True)
        self.current_state()
        self.qt_forms.rebuild(info, state)
        ids = {str(ref["node_id"]) for ref in parameter_bindings(state).values()}
        self.nodes.blockSignals(True)
        self.nodes.clear()
        for node in info["nodes"]:
            if ":".join(node["path"]) in ids:
                self.nodes.addItem(" / ".join(node["path"]) + "  " + node["title"], node["path"])
        self.nodes.blockSignals(False)
        if self.nodes.count():
            if draft is not None:
                index = self.nodes.findData(draft[2].get("probe", {}).get("selectedPath"))
                if index >= 0:
                    self.nodes.blockSignals(True)
                    self.nodes.setCurrentIndex(index)
                    self.nodes.blockSignals(False)
            await self.qt_forms.focus(self.nodes.currentData())
        else:
            raise ValueError(tr("No published nodes were found in this workflow."))
        self.current_state()
        self.save_button.setEnabled(True)
        self._nodes_loaded = True
        self.status.setText(
            tr(
                "Apply values before generating or changing layers. "
                "Only published parameters are applied."
            )
        )

    async def push_values(self, state: Any, *, previous_values: dict | None = None) -> None:
        bindings = parameter_bindings(state)
        entries = [
            dict(ref, value=state.param_values[key])
            for key, ref in bindings.items()
            if key in state.param_values
            and (previous_values is None or previous_values.get(key) != state.param_values[key])
        ]
        await self.view.evaluate("window.loomstrokeApplyValues(" + json.dumps(entries) + ")")
        self._synced = copy.deepcopy(state.param_values)

    def sync(self, state: Any) -> None:
        if self.section._current_ai_effect_id != self.layer_id or getattr(
            self, "_pending_layer", None
        ):
            return
        self.qt_forms.sync(state)
        if self.view is not None and not self._busy and state.param_values != self._synced:
            self.run(self.push_values(copy.deepcopy(state), previous_values=self._synced))

    def focus_node(self, index: int) -> None:
        self._focus_path = None
        if self.view is None or index < 0:
            return
        path = self.nodes.itemData(index)
        if self.qt_forms.select(path):
            return
        self._focus_path = path
        if self._focus_task is None or self._focus_task.done():
            self._focus_task = self.create_owned_task(
                self._focus_pending(), consume_exceptions=True
            )

    async def _focus_pending(self) -> None:
        while self._focus_path is not None:
            while self._busy:
                await asyncio.sleep(0.02)
            path, self._focus_path = self._focus_path, None
            if path is None or not self._nodes_loaded or getattr(self, "_pending_layer", None):
                continue
            task = self.run(self.view.call("focus", path))
            if task is not None:
                await task

    def open_prompt(self) -> None:
        from .prompt_dialog import edit_prompt  # noqa: PLC0415

        self.run(edit_prompt(self))

    async def commit(
        self, *, generate: bool = False, ui_updates: dict | None = None, dispatch: bool = True
    ) -> None:
        with self.live_prompt_choices.transaction():
            await self._commit_values(generate=generate, ui_updates=ui_updates, dispatch=dispatch)

    async def _commit_values(
        self, *, generate: bool, ui_updates: dict | None, dispatch: bool = True
    ) -> None:
        if self.view is None:
            if generate and dispatch:
                self.section.ai_generate_requested.emit(self.layer_id)
            return
        if generate:
            self.qt_forms.advance_seeds()
        before = copy.deepcopy(self.current_state().param_values)
        await self.qt_forms.apply_values(self.current_state(), exclude=ui_updates or ())
        if generate:
            await advance_controls(self.view, self.current_state())
        snapshot = await self.view.call("snapshot")
        state = self.current_state()
        if state.param_values != before:
            raise ValueError(
                tr("Layer values changed while reading nodes. Reopen the node editor.")
            )
        values = snapshot_values(state, snapshot["output"])
        ui = prompt_ui_state(state.param_ui_state, values, ui_updates)
        if generate:
            snapshot = await self.live_prompt_choices.apply(state, values, ui, before) or snapshot
        ui["comfyui_native"] = {"workflow": snapshot["workflow"]}
        self._synced = copy.deepcopy(values)
        if not self.section._host.canvas.set_ai_effect_layer_state(
            self.layer_id, param_values=values, slot_refs=state.slot_refs, param_ui_state=ui
        ):
            raise ValueError(tr("Could not update the AI Effect layer."))
        self.qt_forms.sync(self.current_state())
        if hasattr(self, "_cache_identity"):
            self._cache_identity = copy.deepcopy(
                (state.preset_snapshot, ui.get("comfyui_native"), state.target_server_id)
            )
        self.status.setText(tr("Values applied to this layer."))
        if generate and dispatch:
            self.section.ai_generate_requested.emit(self.layer_id)
        elif not generate:
            schedule = getattr(self.section, "_maybe_schedule_auto_generate", None)
            if callable(schedule):
                schedule(state)


def mount_layer_panel(section: Any, state: Any, layout: Any) -> ComfyLayerPanel | None:
    gateway = state.preset_snapshot.get("gateway_file", {})
    if getattr(state, "gateway_id", None) != "comfyui" or not (
        gateway.get("native_workflow") or gateway.get("workflow")
    ):
        return None
    panel = take(section, state)
    if panel is None:
        panel = ComfyLayerPanel(section, state, section._ai_effect_group)
        panel._cache_identity = identity(state)
        panel._session_url = target_url(state)
    layout.addWidget(panel)
    panel.show()
    return panel
