"""Stop auto-queue through the real ComfyUI frontend's existing queue state."""

from __future__ import annotations

from typing import Any

from .types import McpStructuredError, ReasonCode

# The official frontend's queueSettingsStore and autoQueueService define
# 'disabled' as the mode that stops both instant and change auto-queue.
# https://github.com/Comfy-Org/ComfyUI_frontend/blob/main/src/stores/queueSettingsStore.ts
STOP_REPEAT_SCRIPT = """(() => {
  const root = document.getElementById('vue-app');
  const vue = root && root.__vue_app__;
  const globals = vue && vue.config && vue.config.globalProperties;
  const pinia = globals && globals.$pinia;
  const store = pinia && pinia._s && pinia._s.get('queueSettingsStore');
  const app = window.comfyAPI && window.comfyAPI.app && window.comfyAPI.app.app;
  const ui = app && app.ui;
  if (vue && (!store || typeof store.$patch !== 'function' || typeof store.mode !== 'string'))
    return {supported: false, reason: 'queue_store_unavailable'};
  if (!store && (!ui || typeof ui.autoQueueEnabled !== 'boolean'))
    return {supported: false, reason: 'repeat_control_unavailable'};
  const previousMode = store ? store.mode : (ui.autoQueueEnabled ? 'legacy-enabled' : 'disabled');
  if (store) store.$patch({mode: 'disabled'});
  if (ui && typeof ui.autoQueueEnabled === 'boolean') {
    ui.autoQueueEnabled = false;
    ui.graphHasChanged = false;
    const checkbox = document.getElementById('autoQueueCheckbox');
    if (checkbox) checkbox.checked = false;
  }
  const stopped = (!store || store.mode === 'disabled') && (!ui || !ui.autoQueueEnabled);
  return {supported: true, repeatStopped: stopped, previousMode};
})()"""


async def stop_native_repeat(author: Any) -> dict:
    views = [getattr(author, "view", None)]
    views.extend(value[0] for value in getattr(author, "_server_views", {}).values())
    views = list({id(view): view for view in views if view is not None}.values())
    if not views:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE, "No native ComfyUI view is open."
        )
    results = []
    for view in views:
        result = await view.evaluate(STOP_REPEAT_SCRIPT, timeout=5)
        if (
            not isinstance(result, dict)
            or result.get("supported") is not True
            or result.get("repeatStopped") is not True
        ):
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE, "Native ComfyUI repeat control is unavailable."
            )
        results.append(result)
    return {"repeatStopped": True, "views": results}
