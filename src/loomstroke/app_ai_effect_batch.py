"""Connect FilterPanel's continuous generation to the normal App execution path."""

from __future__ import annotations

import asyncio
import copy
import logging
from math import lcm

from shiboken6 import isValid

from .plugins.ai_effect.batch_generation import run_batch
from .plugins.ai_effect.resolve_inputs import HostResolveInputs
from .plugins.ai_effect.ui.dynamic_prompt_submission import advance_live_prompts
from .plugins.comfyui.ui.prompt_composer import PromptComposerWidget
from .plugins.common.outbound_policy import is_external_services_allowed
from .plugins.prompt_editor.dynamic_prompt import DynamicPromptSequence
from .plugins.prompt_editor.panel_plugin import _resolve_list_items

logger = logging.getLogger(__name__)


def _prompt_config(state):
    return copy.deepcopy(
        {
            key: (live.get("components"), live.get("separator"))
            for key, saved in state.param_ui_state.items()
            if isinstance(saved, dict)
            and isinstance(live := saved.get("live_prompt"), dict)
            and (live.get("active") or live.get("has_input"))
        }
    )


def cycle_length(section, state) -> int | None:
    """Read candidate catalogs without consuming the live widgets' counters."""
    periods = []
    editor = PromptComposerWidget(section._host, parent=section)
    try:
        for saved in state.param_ui_state.values():
            live = saved.get("live_prompt") if isinstance(saved, dict) else None
            if not isinstance(live, dict) or not (live.get("active") or live.get("has_input")):
                continue
            editor.restore_liveprompt_state(live)
            if editor._catalog is not None:
                editor._catalog.refresh()
            period = DynamicPromptSequence.cycle_length(
                editor._components, editor._catalog, editor._render_component, _resolve_list_items
            )
            if period is not None:
                periods.append(period)
    finally:
        editor.deleteLater()
    return lcm(*periods) if periods else None


async def prepare(section, layer_id):
    """Use the single-generation preparation, without also emitting a dispatch."""
    if not is_external_services_allowed(section._host.settings):
        raise ValueError("External services are disabled.")
    current_id, state = section._get_selected_ai_effect()
    if current_id != layer_id or state is None:
        raise ValueError("The selected layer changed.")
    native = section._native_comfy_panel
    if native is not None:
        if native._busy or not native._nodes_loaded or native.view is None:
            raise ValueError("Wait until the node editor is ready.")
        native._busy = True
        try:
            await native.commit(generate=True, dispatch=False)
        finally:
            native._busy = False
    else:
        if not advance_live_prompts(section._ai_effect_widgets, section._host, layer_id):
            raise ValueError("Could not prepare LivePrompt values.")
        section._apply_seed_mode_pre_generate()


class BatchBinding:
    """Own one cancellable task; no batch state is written into the document."""

    def __init__(self, app, section):
        self.app, self.section = app, section
        self.task = None
        self._stop_requested = False
        section.batch_generate_requested.connect(self.start)
        section.batch_stop_requested.connect(self.stop)
        section.destroyed.connect(self.stop)

    def stop(self, *_args) -> None:
        self._stop_requested = True
        if self.task is not None and not self.task.done():
            self.task.cancel()

    def start(self, mode: str, count: int) -> None:  # noqa: PLR0911 - explicit preflight refusals
        if self.task is not None and not self.task.done():
            return
        controls = self.section._batch_controls
        layer_id, state = self.section._get_selected_ai_effect()
        if controls is None or state is None or layer_id is None:
            return
        if not is_external_services_allowed(self.section._host.settings):
            controls.show_status("External services are disabled.")
            return
        controller = self.app._ai_effect_gen_controller
        if controller is None or any(
            not task.done() for task in controller._inflight_tasks.get(layer_id, [])
        ):
            controls.show_status("Wait until generation is ready.")
            return
        if mode not in {"count", "cycle", "infinite"} or count < 1:
            return
        native = self.section._native_comfy_panel
        if native is not None and (native._busy or not native._nodes_loaded or native.view is None):
            controls.show_status("Wait until generation is ready.")
            return
        limit = count if mode == "count" else None
        if mode == "cycle":
            limit = cycle_length(self.section, state)
            if limit is None:
                controls.show_status(
                    "A cycle requires Sequential or Combinatorial LivePrompt choices."
                )
                return
        controls.set_running(True)
        self._stop_requested = False
        controls.show_status("Completed {completed} / {total}", completed=0, total=limit or "∞")
        self.section._ai_trigger_timer.stop()
        snapshot = copy.deepcopy(state.preset_snapshot)
        target_server = state.target_server_id
        canvas = self.section._host.canvas
        origin = (
            getattr(canvas, "_doc", None),
            getattr(getattr(self.app, "_page_service", None), "_active_page_id", None),
            canvas._find_layer_by_id_tree(layer_id),
            _prompt_config(state),
            copy.deepcopy(state.slot_refs),
            state.output_mode_override,
        )
        self.task = asyncio.create_task(
            self._run(controls, layer_id, snapshot, target_server, canvas, origin, limit)
        )
        self.task.add_done_callback(lambda task: self._settle(task, controls))

    def _settle(self, task, controls):
        if self.task is task and isValid(controls) and controls.running:
            controls.set_running(False)
            controls.show_status("Stopped: {completed}", completed=0)
        if not task.cancelled():
            task.exception()

    async def _run(self, controls, layer_id, snapshot, target_server, canvas, origin, limit):
        completed = 0

        def current():
            if self._stop_requested or not isValid(self.section) or not isValid(controls):
                return False
            selected_id, selected = self.section._get_selected_ai_effect()
            return (
                selected_id == layer_id
                and selected is not None
                and self.section._host.canvas is canvas
                and selected.preset_snapshot == snapshot
                and selected.target_server_id == target_server
                and getattr(canvas, "_doc", None) is origin[0]
                and getattr(getattr(self.app, "_page_service", None), "_active_page_id", None)
                == origin[1]
                and canvas._find_layer_by_id_tree(layer_id) is origin[2]
                and _prompt_config(selected) == origin[3]
                and selected.slot_refs == origin[4]
                and selected.output_mode_override == origin[5]
                and is_external_services_allowed(self.section._host.settings)
            )

        async def generate():
            await prepare(self.section, layer_id)
            if not current():
                return None
            resolver = HostResolveInputs(
                canvas_service=canvas, gateway_registry=self.app._ai_effect_gateway_registry
            )
            latest = self.section._get_selected_ai_effect()[1]
            return await self.app._run_ai_effect_generation(layer_id, latest, resolver)

        def progress(done, total):
            nonlocal completed
            completed = done
            if isValid(controls):
                controls.show_status(
                    "Completed {completed} / {total}", completed=done, total=total or "∞"
                )

        reason = "stopped"
        try:
            completed, reason = await run_batch(
                limit=limit, generate=generate, is_current=current, progress=progress
            )
        except asyncio.CancelledError:
            reason = "stopped"
        except Exception:
            logger.exception("Continuous AI Effect generation failed for %s", layer_id)
            reason = "failed"
        finally:
            if isValid(controls):
                controls.set_running(False)
                source = {
                    "finished": "Finished: {completed}",
                    "stopped": "Stopped: {completed}",
                    "failed": "Generation failed; stopped after {completed}.",
                }[reason]
                controls.show_status(source, completed=completed)


def bind(app, panel) -> None:
    section = getattr(panel, "_ai_section", None)
    if section is not None and not hasattr(section, "_batch_binding"):
        section._batch_binding = BatchBinding(app, section)
