"""Advance published native controls before the AI Effect cache key is computed."""

from __future__ import annotations

import json
from typing import Any

from loomstroke.plugins.ai_effect.ui.param_widgets import SeedParamWidget

from .values import parameter_bindings

# Use the frontend's own range, step and randomization rules, including controls
# whose names are prefixed by custom nodes. Only invoke recognized value controls.
ADVANCE_CONTROLS = r"""async entries => {
  const app = window.comfyAPI.app.app;
  const api = window.comfyAPI.valueControl;
  const hierarchy = window.createProbeHierarchy(app, app.canvas);
  const seen = new Set();
  for (const ref of entries) {
    const entry = hierarchy.walk().find(e => e.path.join(':') === String(ref.node_id));
    const node = entry?.node;
    const target = node?.widgets?.find(w => w.name === ref.input);
    if (!target || seen.has(target)) continue;
    seen.add(target);
    if (node.inputs?.some(i => i.widget?.name === target.name && i.link != null)) continue;
    const controls = target.linkedWidgets ?? [];
    const control = controls.find(w => api?.isValueControlWidget?.(w) ||
      (w.name?.endsWith('control_after_generate') &&
       ['fixed', 'randomize', 'increment', 'increment-wrap', 'decrement'].includes(w.value)));
    if (!control) continue;
    if (typeof control.beforeQueued === 'function' &&
        typeof control.afterQueued === 'function') {
      await control.beforeQueued();
      await control.afterQueued();
      continue;
    }
    if (control.value === 'fixed') continue;
    if (!api?.nextValueForLinkedTarget)
      throw new Error('This ComfyUI frontend does not expose native value controls');
    const value = api.nextValueForLinkedTarget({target, linkedWidgets: controls, nodeId: node.id});
    if (value !== undefined) {
      target.value = value;
      await target.callback?.(value);
    }
  }
  app.graph.setDirtyCanvas(true, true);
  return true;
}"""


SYNC_QT_MODES = r"""async entries => {
  const app = window.comfyAPI.app.app;
  const hierarchy = window.createProbeHierarchy(app, app.canvas);
  for (const ref of entries) {
    const node = hierarchy.walk().find(e => e.path.join(':') === String(ref.node_id))?.node;
    const target = node?.widgets?.find(w => w.name === ref.input);
    const control = target?.linkedWidgets?.find(w =>
      w.name?.endsWith('control_after_generate') && w.type === 'combo');
    if (!control) throw new Error('Published seed control is missing');
    control.value = ref.mode;
    await control.callback?.(ref.mode);
  }
  return true;
}"""


async def sync_qt_seed_modes(view: Any, state: Any, widgets: dict[str, Any]) -> None:
    bindings = parameter_bindings(state)
    entries = [
        dict(bindings[key], mode=widget.get_mode())
        for key, widget in widgets.items()
        if isinstance(widget, SeedParamWidget) and key in bindings
    ]
    if entries:
        await view.evaluate(f"({SYNC_QT_MODES})({json.dumps(entries)})")


async def advance_controls(view: Any, state: Any, *, exclude: Any = ()) -> None:
    entries = [ref for key, ref in parameter_bindings(state).items() if key not in exclude]
    await view.evaluate(f"({ADVANCE_CONTROLS})({json.dumps(entries)})")
