"""Serialize layer changes on one frontend and retain per-layer drafts."""

from __future__ import annotations

import asyncio
import copy
from typing import Any

from .layer_cache import identity


def select(panel: Any, state: Any) -> None:
    if panel.view is not None:
        panel.view.setEnabled(False)
    panel._pending_layer = (panel.section._current_ai_effect_id, copy.deepcopy(state))
    task = getattr(panel, "_switch_task", None)
    if task is None or task.done():
        panel._switch_task = panel.create_owned_task(drain(panel), consume_exceptions=True)


async def drain(panel: Any) -> None:
    while panel._pending_layer is not None:
        while panel._busy:
            await asyncio.sleep(0.02)
        layer_id, state = panel._pending_layer
        panel._pending_layer = None
        if panel.section._current_ai_effect_id != layer_id:
            continue
        task = panel.run(switch(panel, layer_id, state))
        if task is not None:
            await task
        if panel.view is not None:
            panel.view.setEnabled(
                panel._nodes_loaded and panel.layer_id == panel.section._current_ai_effect_id
            )


async def switch(panel: Any, layer_id: str, state: Any) -> None:
    if (
        panel.layer_id == layer_id
        and panel._nodes_loaded
        and panel._cache_identity == identity(state)
    ):
        if state.param_values != panel._synced:
            await panel.push_values(state)
        return
    drafts = getattr(panel, "_layer_drafts", None)
    if drafts is None:
        drafts = panel._layer_drafts = {}
    if panel._nodes_loaded and panel.view is not None:
        snapshot = await panel.view.call("draft")
        drafts.pop(panel.layer_id, None)
        drafts[panel.layer_id] = (
            panel._cache_identity,
            copy.deepcopy(panel._synced),
            snapshot,
        )
    if panel.section._current_ai_effect_id != layer_id:
        return
    panel.layer_id = layer_id
    panel._cache_identity = identity(state)
    panel._nodes_loaded = False
    panel.save_button.setEnabled(False)
    await panel.open_nodes()


def draft_for(panel: Any, state: Any) -> Any:
    draft = getattr(panel, "_layer_drafts", {}).get(panel.layer_id)
    return draft if draft is not None and draft[0] == identity(state) else None
