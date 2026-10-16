"""Advance published native controls before the AI Effect cache key is computed."""

from __future__ import annotations

import json
from typing import Any

from .values import parameter_bindings

# Use the frontend's own range, step and randomization rules, including controls
# whose names are prefixed by custom nodes. Do not invoke arbitrary queue hooks.
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
       ['fixed', 'randomize', 'increment', 'decrement'].includes(w.value)));
    if (!control || control.value === 'fixed') continue;
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


async def advance_controls(view: Any, state: Any) -> None:
    entries = list(parameter_bindings(state).values())
    await view.evaluate(f"({ADVANCE_CONTROLS})({json.dumps(entries)})")
