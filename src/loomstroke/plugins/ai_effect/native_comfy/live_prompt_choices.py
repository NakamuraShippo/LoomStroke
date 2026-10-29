"""Resolve persisted LivePrompts for nodes rendered by the ComfyUI frontend."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

from loomstroke.plugins.comfyui.ui.prompt_composer import PromptComposerWidget
from loomstroke.plugins.common.outbound_policy import is_external_services_allowed

from .panel import tr
from .values import parameter_bindings, snapshot_values


class NativeLivePromptChoices:
    """Own session-only sequences when there is no live Qt parameter widget.

    Reuse the shared composer and snippet catalog rather than introducing a
    second choice interpreter. These hidden composers expose no extra UI.
    """

    def __init__(self, panel: Any) -> None:
        self.panel = panel
        self.composers: dict[str, PromptComposerWidget] = {}

    def clear(self) -> None:
        for composer in self.composers.values():
            composer.deleteLater()
        self.composers.clear()

    @contextmanager
    def transaction(self) -> Iterator[None]:
        before = {key: copy.deepcopy(c._dynamic_sequence) for key, c in self.composers.items()}
        try:
            yield
        except BaseException:
            for key in list(self.composers):
                if key in before:
                    self.composers[key]._dynamic_sequence = before[key]
                else:
                    self.composers.pop(key).deleteLater()
            raise

    def resolve(self, state: Any, values: dict, ui: dict, *, exclude: Mapping) -> list[dict]:
        host = self.panel.section._host
        if not is_external_services_allowed(getattr(host, "settings", host)):
            return []
        bindings = parameter_bindings(state)
        updates = []
        for key, ref in bindings.items():
            if key in exclude:
                continue  # Qt forms already advanced this parameter once.
            saved = ui.get(key, {})
            live = saved.get("live_prompt") if isinstance(saved, Mapping) else None
            if not self._eligible(live, values.get(key)):
                continue
            composer = self.composers.get(key)
            if composer is None:
                composer = PromptComposerWidget(host_api=host, parent=self.panel)
                self.composers[key] = composer
            composer.restore_liveprompt_state(live)
            if composer._catalog is not None:
                composer._catalog.refresh()
            text = composer.resolve_for_generation()
            values[key] = text
            live["composed_text"] = text
            updates.append(dict(ref, value=text))
        return updates

    async def apply(self, state: Any, values: dict, ui: dict, before: dict) -> dict | None:
        """Update native widgets and verify their readback before dispatch."""
        updates = self.resolve(state, values, ui, exclude=self.panel.qt_forms.widgets)
        if not updates:
            return None
        await self.panel.view.evaluate("window.loomstrokeApplyValues(" + json.dumps(updates) + ")")
        snapshot = await self.panel.view.call("snapshot")
        current = self.panel.current_state()
        if current.param_values != before:
            raise ValueError(
                tr("Layer values changed while reading nodes. Reopen the node editor.")
            )
        confirmed = snapshot_values(current, snapshot["output"])
        if any(confirmed[key] != value for key, value in values.items()):
            raise ValueError(tr("Could not update the AI Effect layer."))
        return snapshot

    @staticmethod
    def _eligible(live: Any, value: Any) -> bool:
        if not isinstance(live, dict) or not isinstance(value, str):
            return False
        if not (live.get("active") or live.get("has_input")):
            return False
        if live.get("composed_text") != value:
            return False  # Direct frontend text edits take precedence.
        components = live.get("components")
        return isinstance(components, list) and any(
            isinstance(c, Mapping)
            and c.get("selection_mode") in ("random", "sequential", "combinatorial")
            for c in components
        )
