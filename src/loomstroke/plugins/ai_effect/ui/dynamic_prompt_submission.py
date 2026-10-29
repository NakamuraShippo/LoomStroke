"""Resolve bound AI Effect LivePrompts once, without firing auto-generation edits."""

from __future__ import annotations

import copy

from loomstroke.plugins.common.outbound_policy import is_external_services_allowed

from .param_widgets import StringParamWidget


def advance_live_prompts(widgets, host, layer_id: str) -> bool:
    """Write flattened values through the existing canvas state setter.

    No value_changed signal is emitted: a generated choice must not schedule
    another on_change/debounced job. Failed writes restore the local sequence.
    """
    if host is None or not is_external_services_allowed(getattr(host, "settings", host)):
        return True
    pending = []
    for key, widget in widgets.items():
        if not isinstance(widget, StringParamWidget):
            continue
        composer = widget._prompt_composer
        if composer is None or not (widget._composer_active or widget._composer_has_input):
            continue
        if not any(c.selection_mode != "fixed" for c in composer._components):
            continue
        pending.append(
            (
                key,
                widget,
                composer,
                widget._composer_text,
                copy.deepcopy(composer._dynamic_sequence),
            )
        )
    if not pending:
        return True
    canvas = host.canvas
    layer = canvas._find_layer_by_id_tree(layer_id)
    state = getattr(layer, "ai_effect", None)
    if state is None:
        return False
    values = dict(state.param_values)
    ui = copy.deepcopy(state.param_ui_state or {})
    committed = False
    try:
        for key, widget, composer, _text, _sequence in pending:
            widget._composer_text = composer.resolve_for_generation()
            values[key] = widget.get_value()
            ui[key] = widget.get_ui_state()
        committed = bool(
            canvas.set_ai_effect_layer_state(
                layer_id, param_values=values, slot_refs=state.slot_refs, param_ui_state=ui
            )
        )
        return committed
    finally:
        if not committed:
            for _key, widget, composer, text, sequence in pending:
                widget._composer_text = text
                composer._dynamic_sequence = sequence
