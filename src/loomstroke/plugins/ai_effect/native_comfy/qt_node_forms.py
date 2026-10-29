"""Reuse the original AI Effect Qt controls for verified basic node widgets."""

from __future__ import annotations

import copy
import json
import logging
from typing import Any

from PySide6.QtWidgets import QFormLayout, QStackedWidget, QWidget

from loomstroke.plugins.ai_effect.gateways.comfyui import _constraints_from_object_info
from loomstroke.plugins.ai_effect.ui.dynamic_prompt_submission import advance_live_prompts
from loomstroke.plugins.ai_effect.ui.param_widgets import SeedParamWidget, create_param_widget

from .panel import tr
from .values import parameter_bindings, prompt_ui_state
from .widget_classification import NodeWidgetClassification, classify_node

logger = logging.getLogger(__name__)

_LABELS = {
    "basic": "Basic widgets (Qt)",
    "custom": "Custom UI (ComfyUI)",
    "unknown": "Undetermined UI (ComfyUI)",
}


class QtNodeForms(QStackedWidget):
    def __init__(self, panel: Any) -> None:
        super().__init__(panel)
        self.panel = panel
        self.widgets: dict[str, Any] = {}
        self.pages: dict[str, QWidget] = {}
        self.classifications: dict[str, NodeWidgetClassification] = {}
        self._updating = False
        self.hide()

    def clear(self) -> None:
        self.hide()
        self.widgets.clear()
        self.pages.clear()
        self.classifications.clear()
        while self.count():
            page = self.widget(0)
            self.removeWidget(page)
            page.hide()
            page.deleteLater()

    def rebuild(self, info: dict, state: Any) -> None:
        self.clear()
        bindings = parameter_bindings(state)
        entries = state.preset_snapshot.get("manifest", {}).get("parameters", [])
        by_node: dict[str, list[dict]] = {}
        for entry in entries:
            ref = bindings.get(entry.get("internal_id"), {})
            by_node.setdefault(str(ref.get("node_id")), []).append(entry)
        for node in info.get("nodes", []):
            key = ":".join(node["path"])
            result = classify_node(node)
            self.classifications[key] = result
            published = by_node.get(key, [])
            if result.kind != "basic" or not published:
                continue
            page = QWidget(self)
            form = QFormLayout(page)
            form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
            pending = {}
            try:
                for entry in published:
                    internal_id = entry["internal_id"]
                    ref = bindings[internal_id]
                    if not any(w["name"] == ref["input"] for w in node["widgets"]):
                        raise ValueError("Published widget is missing")
                    effective = self._entry(entry, node, ref["input"])
                    widget = create_param_widget(
                        effective, page, host_api=getattr(self.panel.section, "_host", None)
                    )
                    value = state.param_values.get(internal_id, effective.get("default"))
                    widget.set_value(value)
                    # Do not substitute a clamped/rounded value for a saved one.
                    if widget.get_value() != value:
                        raise ValueError("Qt control cannot represent the saved value")
                    widget.set_ui_state(state.param_ui_state.get(internal_id, {}))
                    if widget.get_value() != value:
                        widget.set_value(value)
                    widget.value_changed.connect(self._changed)
                    form.addRow(str(entry.get("display_name") or ref["input"]), widget)
                    pending[internal_id] = widget
            except (TypeError, ValueError, OverflowError):
                logger.warning("Retaining native UI for node %s", key, exc_info=True)
                self.classifications[key] = NodeWidgetClassification("unknown", key)
                page.deleteLater()
                continue
            self.widgets.update(pending)
            self.pages[key] = page
            self.addWidget(page)

    @staticmethod
    def _entry(entry: dict, node: dict, input_name: str) -> dict:
        kind, bounds, choices, multiline = _constraints_from_object_info(
            {node["type"]: {"input": node.get("inputSchema", {})}}, node["type"], input_name
        )
        if kind is None:
            raise ValueError("Input has no supported Qt schema")
        effective = dict(entry, type=kind)
        if bounds:
            effective["range"] = {**bounds, **(entry.get("range") or {})}
        if choices is not None:
            effective["choices"] = entry.get("choices") or choices
        if multiline:
            effective["required_text"] = True
        return effective

    def select(self, path: list[str]) -> bool:
        key = ":".join(path)
        result = self.classifications.get(key, NodeWidgetClassification("unknown"))
        self.panel.classification_label.setText(tr(_LABELS[result.kind]))
        self.panel.classification_label.setToolTip(tr(result.reason) if result.reason else "")
        page = self.pages.get(key)
        if page is not None:
            self.setCurrentWidget(page)
        self.setVisible(page is not None)
        if self.panel.view is not None:
            self.panel.view.setVisible(page is None)
        return page is not None

    def retranslate(self) -> None:
        path = self.panel.nodes.currentData()
        if path is not None:
            self.select(path)

    async def focus(self, path: list[str]) -> None:
        if not self.select(path):
            await self.panel.view.call("focus", path)

    def sync(self, state: Any) -> None:
        if not self.widgets:
            return
        self._updating = True
        invalid_nodes = set()
        bindings = parameter_bindings(state)
        try:
            for key, widget in self.widgets.items():
                value = state.param_values.get(key)
                widget.set_value(value)
                if isinstance(widget, SeedParamWidget):
                    widget.set_mode("fixed")
                widget.set_ui_state(state.param_ui_state.get(key, {}))
                if widget.get_value() != value:
                    invalid_nodes.add(str(bindings[key]["node_id"]))
        finally:
            self._updating = False
        for node_id in invalid_nodes:
            self.classifications[node_id] = NodeWidgetClassification("unknown", node_id)
            page = self.pages.pop(node_id)
            self.removeWidget(page)
            page.hide()
            page.deleteLater()
            for key, ref in bindings.items():
                if str(ref["node_id"]) == node_id:
                    self.widgets.pop(key, None)
        if invalid_nodes and self.panel.nodes.currentData() is not None:
            self.select(self.panel.nodes.currentData())

    def _changed(self, key: str, value: Any) -> None:
        panel = self.panel
        if (
            self._updating
            or key not in self.widgets
            or not panel._nodes_loaded
            or getattr(panel, "_pending_layer", None)
        ):
            return
        try:
            state = panel.current_state()
        except ValueError:
            return
        widget = self.widgets[key]
        values = dict(state.param_values, **{key: value})
        updates = {key: widget.get_ui_state()}
        ui = prompt_ui_state(state.param_ui_state, values, updates)
        if not panel.section._host.canvas.set_ai_effect_layer_state(
            panel.layer_id, param_values=values, slot_refs=state.slot_refs, param_ui_state=ui
        ):
            panel.status.setText(tr("Could not update the AI Effect layer."))
            self.sync(state)
            return
        schedule = getattr(panel.section, "_maybe_schedule_auto_generate", None)
        if callable(schedule):
            schedule(state)

    def advance_seeds(self) -> None:
        if not advance_live_prompts(self.widgets, self.panel.section._host, self.panel.layer_id):
            raise ValueError(tr("Could not update the AI Effect layer."))
        for widget in self.widgets.values():
            if isinstance(widget, SeedParamWidget):
                widget.apply_pre_generate_mutation()

    async def apply_values(self, state: Any, *, exclude: Any = ()) -> None:
        """Merge committed Qt values without overwriting unapplied custom-node edits."""
        if not self.widgets:
            return
        entries = [
            dict(ref, value=copy.deepcopy(state.param_values[key]))
            for key, ref in parameter_bindings(state).items()
            if key in self.widgets and key in state.param_values and key not in exclude
        ]
        await self.panel.view.evaluate("window.loomstrokeApplyValues(" + json.dumps(entries) + ")")
