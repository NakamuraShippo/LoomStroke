"""AI Effect section delegated from :mod:`filter_panel.panel`."""

from __future__ import annotations

import contextlib
import copy
import logging
import time
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from loomstroke.platform.async_task_owner import AsyncTaskOwner
from loomstroke.platform.launch_options import ai_features_enabled

logger = logging.getLogger(__name__)

AUTO_CONNECT_COOLDOWN_S = 30.0
_TRIGGER_CHOICES: tuple[str, ...] = ("manual", "on_change", "debounced")


def _ts(text: str) -> str:
    from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

    return translated_literal_domain("FilterPanel", text, use_fallback=True)


class AIEffectPanelSection(QWidget, AsyncTaskOwner):
    """Own AI Effect UI state while FilterPanel remains the public facade."""

    ai_generate_requested = Signal(str)
    batch_generate_requested = Signal(str, int)
    batch_stop_requested = Signal()

    def __init__(self, panel: QWidget) -> None:
        super().__init__(panel)
        AsyncTaskOwner.__init__(self)
        self._panel = panel
        self._current_ai_effect_id: str | None = None
        self._ai_effect_group: QGroupBox | None = None
        self._ai_effect_widgets: dict[str, Any] = {}
        self._ai_slot_combos: dict[str, QComboBox] = {}
        self._ai_slot_rows: dict[str, QWidget] = {}
        self._ai_trigger_combo: QComboBox | None = None
        self._ai_generate_button: QPushButton | None = None
        self._ai_server_combo: QComboBox | None = None
        self._ai_output_to_self_checkbox: QCheckBox | None = None
        self._ai_trigger_timer = QTimer(self)
        self._ai_trigger_timer.setSingleShot(True)
        self._ai_trigger_timer.timeout.connect(self._on_ai_trigger_timer_fired)
        self._object_info_fetch_task: Any = None
        self._preview_label: QLabel | None = None
        self._auto_connect_banner: QWidget | None = None
        self._auto_connect_banner_visible: bool = False
        self._last_auto_connect_attempt_t: float = float("-inf")
        self._auto_connect_task: Any = None
        self._native_comfy_panel: Any = None
        self._batch_controls: Any = None

    @property
    def _host(self) -> Any:
        return getattr(self._panel, "_host", None)

    @property
    def _selected_layer_id(self) -> str | None:
        return getattr(self._panel, "_selected_layer_id", None)

    @property
    def _updating(self) -> bool:
        return bool(getattr(self._panel, "_updating", False))

    @_updating.setter
    def _updating(self, value: bool) -> None:
        self._panel._updating = bool(value)  # noqa: SLF001 - facade owns the shared guard

    def __getattr__(self, name: str) -> Any:
        return getattr(self._panel, name)

    def set_host(self, host: Any) -> None:
        del host
        self._last_auto_connect_attempt_t = float("-inf")

    def detach_host(self) -> None:
        self._commit_current_ai_effect_values()
        self.cancel_owned_tasks()
        self._object_info_fetch_task = None
        self._auto_connect_task = None
        self._current_ai_effect_id = None
        self._clear_ai_effect_ui()
        from loomstroke.plugins.ai_effect.native_comfy.layer_cache import clear  # noqa: PLC0415

        clear(self)

    # ------------------------------------------------------------------
    # AI Effect branch — Step 2 of AIEffectPanel merger
    # ------------------------------------------------------------------

    def _get_selected_ai_effect(self) -> tuple[str | None, Any]:
        """Return ``(layer_id, ai_effect_state)`` for the selection, or
        ``(None, None)`` when the selected layer is not an AI Effect."""
        if not ai_features_enabled() or self._host is None or self._selected_layer_id is None:
            return (None, None)
        canvas = getattr(self._host, "canvas", None)
        if canvas is None:
            return (None, None)
        finder = getattr(canvas, "_find_layer_by_id_tree", None)
        if not callable(finder):
            return (None, None)
        layer = finder(self._selected_layer_id)
        if layer is None or getattr(layer, "layer_type", "") != "ai_effect":
            return (None, None)
        ai_state = getattr(layer, "ai_effect", None)
        if ai_state is None:
            return (None, None)
        return (self._selected_layer_id, ai_state)

    def _refresh_ai_effect(self, layer_id: str, state: Any) -> None:
        """Build the AI Effect UI on layer change, then sync values."""
        if layer_id != self._current_ai_effect_id:
            self._current_ai_effect_id = layer_id
            self._build_ai_effect_ui(state)
        self._sync_ai_effect_values(state)

    def _build_ai_effect_ui(  # noqa: PLR0912, PLR0915 - linear assembly of group + slots + params + banner
        self, state: Any
    ) -> None:
        """Rebuild the preset-name header + parameter form.

        Slot binding / Trigger / Generate will be added in Steps 3-4.
        The manifest is pulled straight from
        ``state.preset_snapshot.manifest`` — no library lookup — so
        reopened documents render the same parameters they were saved
        with, independent of the current library.
        """
        from loomstroke.plugins.ai_effect.ui.param_widgets import (  # noqa: PLC0415
            ParamWidget,
            create_param_widget,
        )

        self._clear_ai_effect_ui()

        snapshot = getattr(state, "preset_snapshot", None)
        manifest: Mapping[str, Any] = (
            snapshot.get("manifest", {}) if isinstance(snapshot, Mapping) else {}
        )
        if not isinstance(manifest, Mapping):
            manifest = {}
        metadata = manifest.get("metadata", {})
        preset_name = ""
        if isinstance(metadata, Mapping):
            raw_name = metadata.get("name")
            if isinstance(raw_name, str) and raw_name:
                preset_name = raw_name
        if not preset_name:
            preset_name = _ts("(Unnamed preset)")

        self._ai_effect_group = QGroupBox(preset_name, self)
        self._ai_effect_group.setObjectName("aiEffectPresetGroup")
        group_layout = QVBoxLayout(self._ai_effect_group)
        group_layout.setContentsMargins(10, 14, 10, 10)
        group_layout.setSpacing(8)

        # Phase 8d: re-mount the auto-connect failure banner before any
        # other section so the [Retry] action stays visible to the user
        # across layer-rebuilds. ``_clear_ai_effect_ui`` destroyed the
        # previous instance; re-create from the persistent flag.
        if self._auto_connect_banner_visible:
            self._show_auto_connect_failure_banner()

        # ComfyUI layers use this panel for parameter editing only. With no
        # thumbnail label, late preview events are dropped before PNG decoding.
        if str(getattr(state, "gateway_id", "") or "") != "comfyui":
            self._preview_label = QLabel(self._ai_effect_group)
            self._preview_label.setObjectName("aiEffectPreviewThumbnail")
            self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._preview_label.setStyleSheet(self._preview_thumbnail_stylesheet())
            self._preview_label.hide()
            group_layout.addWidget(self._preview_label)

        # ---- Input slots (Step 3) ----
        # Follows the mockup order: slots before params. Built before
        # params so the combos appear first even when the preset has
        # no parameters at all.
        self._build_ai_slots_ui(group_layout, manifest)

        if str(getattr(state, "gateway_id", "") or "") == "comfyui":
            self._build_ai_server_section(group_layout)

        # Phase 6-h: "Output to this layer" toggle sits directly under
        # the slots section (not in the action bar) so the user reads
        # output-target alongside input-bindings.
        self._build_ai_output_to_self_section(group_layout)

        from loomstroke.plugins.ai_effect.native_comfy.layer_panel import (  # noqa: PLC0415
            mount_layer_panel,
        )

        self._native_comfy_panel = mount_layer_panel(self, state, group_layout)
        params_raw = [] if self._native_comfy_panel else manifest.get("parameters", [])
        # Look up the gateway's cached object_info so we can enrich
        # manifest entries that were authored before backend
        # constraints were captured. Old presets stored "string" /
        # "int" with no range / choices for params that ComfyUI
        # actually exposes as enums (sampler_name, scheduler) or
        # bounded ints (steps, width, height). At-runtime enrichment
        # gives those entries the right widget without forcing the
        # author to re-save the preset.
        runtime_enrichment = self._build_runtime_enrichment(state)
        # When the cache hasn't been populated yet (user opened the
        # AI Effect Panel before clicking Connect on the ComfyUI
        # panel), kick off a passive fetch and rebuild the widgets
        # once it lands. Safe-guarded to only fetch when a connection
        # already exists, so we don't race the ComfyUI panel's own
        # Connect lifecycle.
        #
        # Gate on ``state.gateway_id == "comfyui"``: the fetch path
        # hits ComfyUI's ``get_object_info`` and rebuilds when it
        # returns. ``_build_runtime_enrichment`` always returns None
        # for NanoBanana (no ``_object_info_cache`` on its service),
        # so without this guard a NanoBanana layer would fire the
        # ComfyUI fetch on every build, succeed, and re-trigger
        # ``_build_ai_effect_ui`` from the rebuild path — an infinite
        # loop that flashes the parameter form on/off until the user
        # selects another layer.
        gateway_id_str = str(getattr(state, "gateway_id", "") or "")
        if (
            self._native_comfy_panel is None
            and runtime_enrichment is None
            and gateway_id_str == "comfyui"
        ):
            self._schedule_object_info_fetch_for_panel()
        # Resolve each param's owning ComfyUI node so we can fold the
        # form into per-node collapsible group boxes. Falls back to a
        # single "Parameters" group if no mapping is available (test
        # stubs, future gateways without a node_mappings concept).
        node_resolver = self._build_param_node_resolver(state)
        node_groups: dict[str, tuple[QGroupBox, QFormLayout]] = {}
        if isinstance(params_raw, list):
            for entry in params_raw:
                if not isinstance(entry, Mapping):
                    continue
                effective_entry = self._enrich_param_entry(entry, runtime_enrichment)
                try:
                    widget: ParamWidget = create_param_widget(
                        effective_entry,
                        self._ai_effect_group,
                        host_api=self._host,
                    )
                except Exception:
                    # Malformed manifest entry — log + skip rather than
                    # blowing up the whole panel build.
                    logger.warning(
                        "Skipping malformed AI param entry: %r",
                        entry.get("internal_id"),
                        exc_info=True,
                    )
                    continue
                widget.value_changed.connect(self._on_ai_param_changed)
                label = (
                    effective_entry.get("display_name")
                    if isinstance(effective_entry.get("display_name"), str)
                    else None
                ) or widget.internal_id
                group_label = node_resolver(str(widget.internal_id))
                bucket = node_groups.get(group_label)
                if bucket is None:
                    bucket = self._build_node_group(group_label)
                    node_groups[group_label] = bucket
                bucket[1].addRow(label, widget)
                self._ai_effect_widgets[widget.internal_id] = widget
        for group_box, _form in node_groups.values():
            group_layout.addWidget(group_box)

        # ---- Action bar (Trigger + Generate) ----
        self._build_ai_action_bar(group_layout)

        # Insert above stretch spacer so the group sits at the top.
        self._root_layout.insertWidget(0, self._ai_effect_group)
        self._placeholder.hide()

    def _sync_ai_effect_values(self, state: Any) -> None:
        """Push current ``param_values`` + ``slot_refs`` into their
        widgets without firing change signals (guarded by ``_updating``)."""
        if self._native_comfy_panel is not None:
            self._native_comfy_panel.sync(state)
        self._updating = True
        try:
            values = getattr(state, "param_values", None) or {}
            if isinstance(values, Mapping):
                for internal_id, widget in self._ai_effect_widgets.items():
                    if internal_id in values:
                        try:
                            widget.set_value(values[internal_id])
                        except Exception:
                            logger.debug("set_value failed on %s", internal_id, exc_info=True)
            self._sync_ai_effect_param_ui_state(state)
            # Slot combos: select the entry whose userData matches the
            # current SlotRef.layer_id (or the "(unbound)" sentinel).
            slot_refs = getattr(state, "slot_refs", None) or {}
            if isinstance(slot_refs, Mapping):
                for slot_id, combo in self._ai_slot_combos.items():
                    ref = slot_refs.get(slot_id)
                    target = getattr(ref, "layer_id", None) if ref is not None else None
                    self._select_slot_target(combo, target)
            # Trigger combo: reflect state.trigger (fallback "manual").
            trigger = getattr(state, "trigger", "manual")
            if not isinstance(trigger, str) or trigger not in _TRIGGER_CHOICES:
                trigger = "manual"
            self._select_trigger_value(trigger)
            self._select_ai_server_value(getattr(state, "target_server_id", None))
            # Output-to-self checkbox: reflect ``output_mode_override``
            # without firing toggled (otherwise programmatic state
            # restore would write back to the canvas).
            override = getattr(state, "output_mode_override", None)
            self._select_output_to_self(override == "self")
        finally:
            self._updating = False

    def _sync_ai_effect_param_ui_state(self, state: Any) -> None:
        """Restore optional LoomStroke-only UI state into AI param widgets."""
        ui_states = getattr(state, "param_ui_state", None) or {}
        if not isinstance(ui_states, Mapping):
            return
        for internal_id, widget in self._ai_effect_widgets.items():
            if internal_id not in ui_states:
                continue
            setter = getattr(widget, "set_ui_state", None)
            if not callable(setter):
                continue
            try:
                setter(ui_states[internal_id])
            except Exception:
                logger.debug("set_ui_state failed on %s", internal_id, exc_info=True)

    def _clear_ai_effect_ui(self) -> None:
        """Drop the AI preset GroupBox, param widgets, and slot combos."""
        from loomstroke.plugins.ai_effect.ui.batch_controls import clear  # noqa: PLC0415

        clear(self)
        if self._native_comfy_panel is not None:
            from loomstroke.plugins.ai_effect.native_comfy.layer_cache import park  # noqa: PLC0415

            park(self, self._native_comfy_panel)
            self._native_comfy_panel = None
        self._ai_effect_widgets.clear()
        self._ai_slot_combos.clear()
        self._ai_slot_rows.clear()
        self._ai_trigger_combo = None
        self._ai_generate_button = None
        self._ai_server_combo = None
        self._ai_output_to_self_checkbox = None
        # Cancel any pending auto-fire so a layer-switch or
        # layer-deletion during a debounce window doesn't fire
        # generation against a stale id.
        if self._ai_trigger_timer.isActive():
            self._ai_trigger_timer.stop()
        if self._ai_effect_group is not None:
            self._root_layout.removeWidget(self._ai_effect_group)
            self._ai_effect_group.deleteLater()
            self._ai_effect_group = None
        # Phase 8d: the failure banner was a child of the now-destroyed
        # group, so the C++ widget is gone. Drop the Python reference
        # to avoid dangling-pointer access on the next show. The
        # ``_auto_connect_banner_visible`` flag is preserved so the
        # next ``_build_ai_effect_ui`` re-mounts a fresh banner.
        self._auto_connect_banner = None
        # Preview thumbnail was a child of the destroyed group too —
        # drop the reference so a stale ``set_preview_image`` after
        # layer-switch doesn't poke a deleted widget.
        self._preview_label = None
        self._placeholder.show()

    # ------------------------------------------------------------------
    # Runtime parameter-entry enrichment from object_info
    # ------------------------------------------------------------------

    def _build_runtime_enrichment(  # noqa: PLR0911, PLR0912
        self, state: Any
    ) -> dict[str, Any] | None:
        """Resolve the gateway-side data needed to enrich manifest entries.

        Returns a dict keyed by ``internal_id`` mapping to a constraint
        record ``{"type": str, "range": {...}|None, "choices": list|None,
        "multiline": bool}`` for each parameter the panel can find a
        matching object_info schema for. Returns ``None`` when:

        - the host doesn't expose an ``ai_effect_gateway_registry``
          (test stand-ins, AI Effect subsystem unavailable)
        - the gateway is not registered yet (ComfyUI plugin still
          loading)
        - the service has no cached ``object_info`` (user hasn't
          opened ComfyUI panel + Connect; the panel falls back to
          whatever range/choices the manifest already carries)

        Hot path: built once per ``_build_ai_effect_ui`` call, *not*
        per parameter, so the registry / service / object_info
        lookups only happen once even on a 50-parameter preset.
        """
        if self._host is None:
            return None
        host_get = getattr(self._host, "get_service", None)
        if not callable(host_get):
            return None
        try:
            registry = host_get("ai_effect_gateway_registry")
        except Exception:
            return None
        if registry is None:
            return None
        gateway_id = str(getattr(state, "gateway_id", "") or "")
        if not gateway_id:
            return None
        gateway = None
        try:
            gateway = registry.get(gateway_id)
        except Exception:
            return None
        if gateway is None:
            return None
        service = getattr(gateway, "_service", None)
        if service is None:
            return None
        object_info = self._cached_object_info_for_state(state, gateway, service)
        nodes = object_info.get("nodes") if isinstance(object_info, Mapping) else None
        if not isinstance(nodes, dict) or not nodes:
            return None
        # Build the manifest-internal_id → constraint record table by
        # walking the gateway_file's node_mappings + workflow.
        snapshot = getattr(state, "preset_snapshot", None)
        if not isinstance(snapshot, Mapping):
            return None
        gateway_file = snapshot.get("gateway_file", {})
        if not isinstance(gateway_file, Mapping):
            return None
        workflow = gateway_file.get("workflow", {})
        mappings = gateway_file.get("node_mappings", {})
        if not isinstance(workflow, Mapping) or not isinstance(mappings, Mapping):
            return None
        # Use the gateway's own helper so the schema-shape parsing
        # (INT/FLOAT/STRING/combo + options dict) lives in one place.
        from loomstroke.plugins.ai_effect.gateways.comfyui import (  # noqa: PLC0415
            _constraints_from_object_info,
        )

        out: dict[str, Any] = {}
        for internal_id, ref in mappings.items():
            if not isinstance(ref, dict):
                continue
            node_id = ref.get("node_id")
            input_key = ref.get("input")
            if node_id is None or not isinstance(input_key, str):
                continue
            node = workflow.get(str(node_id))
            if not isinstance(node, Mapping):
                continue
            class_type = str(node.get("class_type", ""))
            if not class_type:
                continue
            ptype, prange, pchoices, pmultiline = _constraints_from_object_info(
                object_info, class_type, input_key
            )
            if ptype is None and prange is None and pchoices is None:
                continue
            out[str(internal_id)] = {
                "type": ptype,
                "range": prange,
                "choices": pchoices,
                "multiline": pmultiline,
            }
        return out or None

    def _target_server_id_for_state(self, state: Any, server_registry: Any = None) -> str | None:
        raw = getattr(state, "target_server_id", None)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
        snapshot = getattr(state, "preset_snapshot", None)
        if isinstance(snapshot, Mapping):
            gateway_file = snapshot.get("gateway_file", {})
            if isinstance(gateway_file, Mapping):
                raw = gateway_file.get("target_server_id")
                if isinstance(raw, str) and raw.strip():
                    return raw.strip()
        default_id = getattr(server_registry, "default_server_id", None)
        return default_id if isinstance(default_id, str) and default_id else None

    def _cached_object_info_for_state(
        self,
        state: Any,
        gateway: Any,
        service: Any,
    ) -> dict[str, Any] | None:
        server_registry = getattr(gateway, "_registry", None)
        target_server_id = self._target_server_id_for_state(state, server_registry)
        if server_registry is not None and target_server_id:
            getter = getattr(server_registry, "get_object_info_cache", None)
            cached = getter(target_server_id) if callable(getter) else None
            if isinstance(cached, Mapping):
                nodes = cached.get("nodes")
                if isinstance(nodes, dict) and nodes:
                    return dict(cached)
            target = getattr(server_registry, "get", lambda _sid: None)(target_server_id)
            target_url = str(getattr(target, "url", "") or "").rstrip("/")
            connection = getattr(service, "_connection", None)
            current_url = str(getattr(connection, "base_url", "") or "").rstrip("/")
            if target_url and current_url and target_url != current_url:
                return None

        cache = getattr(service, "_object_info_cache", None)
        nodes = getattr(cache, "nodes", None) if cache is not None else None
        if isinstance(nodes, dict) and nodes:
            models = getattr(cache, "models", {}) if cache is not None else {}
            return {"nodes": nodes, "models": models if isinstance(models, dict) else {}}
        return None

    def _schedule_object_info_fetch_for_panel(self) -> None:  # noqa: PLR0911, PLR0912
        """Trigger a passive ``object_info`` fetch + rebuild on completion.

        Safety contract: only fetches when the gateway service already
        has a live ``_connection`` (which happens after the user has
        clicked Connect on the ComfyUI panel). Otherwise returns
        immediately so we never race the ComfyUI panel's connection
        setup. See history: an earlier version of the Editor's similar
        path called ``service.initialize()`` itself, which deadlocked
        the qasync loop when the user clicked Connect with the Editor
        open. This panel-side variant follows the lesson: don't drive
        connection lifecycle, only consume.

        Cancels any previous in-flight fetch so layer selection
        thrash doesn't queue a backlog of redundant requests.
        """
        if self._host is None:
            return
        host_get = getattr(self._host, "get_service", None)
        if not callable(host_get):
            return
        try:
            registry = host_get("ai_effect_gateway_registry")
        except Exception:
            return
        if registry is None or self._current_ai_effect_id is None:
            return
        layer_id, state = self._get_selected_ai_effect()
        if layer_id is not None and layer_id != self._current_ai_effect_id:
            return
        gateway_id = "comfyui"  # Only ComfyUI today; future gateways add their own.
        gateway = None
        try:
            gateway = registry.get(gateway_id)
        except Exception:
            return
        if gateway is None:
            return
        service = getattr(gateway, "_service", None)
        if service is None or not hasattr(service, "get_object_info"):
            return
        server_registry = getattr(gateway, "_registry", None)
        target_server_id = (
            self._target_server_id_for_state(state, server_registry) if state is not None else None
        )
        if server_registry is not None and target_server_id:
            cache_get = getattr(server_registry, "get_object_info_cache", None)
            cached = cache_get(target_server_id) if callable(cache_get) else None
            nodes = cached.get("nodes") if isinstance(cached, Mapping) else None
            if isinstance(nodes, dict) and nodes:
                return
            conn_get = getattr(server_registry, "get_connection", None)
            connection = conn_get(target_server_id) if callable(conn_get) else None
        else:
            connection = getattr(service, "_connection", None)
        is_connected_fn = getattr(connection, "is_connected", None) if connection else None
        connected = callable(is_connected_fn) and bool(is_connected_fn())
        try:
            if connected:
                previous = self._object_info_fetch_task
                self._object_info_fetch_task = self.create_owned_task(
                    self._fetch_object_info_and_rebuild(
                        service,
                        server_registry=server_registry,
                        target_server_id=target_server_id,
                    ),
                    cancel_previous=previous,
                    require_running=True,
                )
                return
            # No live connection. The ComfyUI panel normally owns
            # Connect lifecycle, but for first-time AI Effect layer
            # encounter (placement via Filter menu, .lstroke load
            # with an existing AI Effect layer) the user expects
            # widgets to come up with the right float/step semantics
            # without first hunting for the ComfyUI panel. Mirror the
            # ``ComfyUIGateway.submit`` pattern: attempt
            # ``service.connect()``; if that succeeds, fetch
            # object_info and rebuild.
            #
            # Cooldown rate-limits retries: layer selection clicks can
            # fire 5-10/sec, so without a gate every click would spawn
            # a connect Task and a failure toast. After each attempt
            # the panel waits ``AUTO_CONNECT_COOLDOWN_S`` (30s) before
            # trying again — long enough for the user to fix their
            # ComfyUI URL / restart the server, short enough that the
            # next click after that recovery window naturally retries.
            #
            # Safe to drive from this side because:
            # - ``service.connect()`` checks ``is_connected()`` itself
            #   so a race with the ComfyUI panel's Connect resolves to
            #   a single open WebSocket.
            # - Earlier deadlock (Editor + panel both calling
            #   ``initialize()`` simultaneously, fixed in commit
            #   ``81682702``) was caused by competing init lifecycles
            #   with no idempotency check; we use ``connect()`` which
            #   does have one.
            in_flight = self._auto_connect_task
            if in_flight is not None and not in_flight.done():
                return
            now = time.monotonic()
            if now - self._last_auto_connect_attempt_t < AUTO_CONNECT_COOLDOWN_S:
                return
            self._last_auto_connect_attempt_t = now
            self._auto_connect_task = self.create_owned_task(
                self._auto_connect_and_fetch(
                    service,
                    server_registry=server_registry,
                    target_server_id=target_server_id,
                ),
                require_running=True,
            )
        except RuntimeError:
            logger.debug(
                "FilterPanel: no running event loop for object_info fetch; "
                "widgets will use whatever the manifest carries"
            )

    async def _fetch_object_info_and_rebuild(
        self,
        service: Any,
        *,
        server_registry: Any = None,
        target_server_id: str | None = None,
    ) -> None:
        """Await the service's HTTP-only ``get_object_info`` and rebuild
        the AI Effect UI so freshly-cached constraints reach the widgets."""
        try:
            if server_registry is not None and target_server_id:
                await self._mount_registry_connection(service, server_registry, target_server_id)
            object_info = await self._call_service_get_object_info(
                service,
                force_refresh=server_registry is not None and bool(target_server_id),
            )
        except Exception:
            logger.warning("object_info fetch failed", exc_info=True)
            return
        if server_registry is not None and target_server_id:
            cache_set = getattr(server_registry, "set_object_info_cache", None)
            if callable(cache_set) and isinstance(object_info, dict):
                cache_set(target_server_id, object_info)
        # Re-fetch state and rebuild — the layer might have changed
        # while we awaited.
        layer_id, state = self._get_selected_ai_effect()
        if layer_id is None or state is None:
            return
        if layer_id != self._current_ai_effect_id:
            return
        self._build_ai_effect_ui(state)
        self._sync_ai_effect_values(state)

    async def _auto_connect_and_fetch(
        self,
        service: Any,
        *,
        server_registry: Any = None,
        target_server_id: str | None = None,
    ) -> None:
        """One-shot: open a WebSocket via ``service.connect()`` and then
        fetch ``object_info`` so the AI Effect param widgets render with
        correct type/range/step.

        Triggered from ``_schedule_object_info_fetch_for_panel`` when an
        AI Effect layer is encountered (placed or restored from disk)
        and no live connection exists. ``service.connect()`` is
        idempotent against the ComfyUI panel's own Connect button (it
        re-checks ``is_connected`` internally) so a race resolves to a
        single open WebSocket — the historical deadlock came from
        Editor + panel calling the older ``initialize()`` without that
        guard.

        Errors are surfaced via the main-window status bar (non-modal
        toast) so the user knows why the widgets did not enrich, and
        logged at warning level for triage. The panel does not retry
        immediately — the cooldown in
        ``_schedule_object_info_fetch_for_panel`` ensures the next
        retry is at least ``AUTO_CONNECT_COOLDOWN_S`` away.
        """
        connect = getattr(service, "connect", None)
        if not callable(connect):
            return
        connect_exc: BaseException | None = None
        try:
            if server_registry is not None and target_server_id:
                await self._mount_registry_connection(service, server_registry, target_server_id)
            await connect()
        except Exception as exc:
            # Belt-and-suspenders: ``ExtendedComfyUIService.connect``
            # currently swallows network errors and returns None
            # (logging the failure at the AsyncComfyUIConnection level
            # only). Catching here covers any future contract change.
            connect_exc = exc
            logger.warning(
                "FilterPanel: service.connect() raised (%s); widgets will "
                "use manifest values until user clicks Connect",
                exc,
                exc_info=True,
            )
        # Always re-check ``is_connected`` after the call: today's
        # ``service.connect()`` returns silently on failure (catches
        # ``aiohttp.ClientConnectorError`` internally), so a False
        # outcome surfaces ONLY through the connection state.
        connection = getattr(service, "_connection", None)
        is_connected_fn = getattr(connection, "is_connected", None) if connection else None
        connected = callable(is_connected_fn) and bool(is_connected_fn())
        if not connected:
            if connect_exc is None:
                logger.warning(
                    "FilterPanel: service.connect() returned silently but "
                    "is_connected()==False (ComfyUI server unreachable). "
                    "Surfacing failure toast + banner."
                )
            self._show_auto_connect_failure_toast(connect_exc or RuntimeError("not connected"))
            self._auto_connect_banner_visible = True
            self._show_auto_connect_failure_banner()
            return
        # Connection succeeded — proceed with the fetch + rebuild path
        # the connected branch would have taken. Verify the layer is
        # still selected because the user may have switched while we
        # were awaiting connect(). Drop the failure banner: enrichment
        # is about to fire and the user has nothing to recover from.
        self._auto_connect_banner_visible = False
        self._hide_auto_connect_failure_banner()
        layer_id, _state = self._get_selected_ai_effect()
        if layer_id is None or layer_id != self._current_ai_effect_id:
            return
        await self._fetch_object_info_and_rebuild(
            service,
            server_registry=server_registry,
            target_server_id=target_server_id,
        )

    async def _mount_registry_connection(
        self,
        service: Any,
        server_registry: Any,
        target_server_id: str,
    ) -> Any:
        get_or_create = getattr(server_registry, "get_or_create_connection", None)
        if not callable(get_or_create):
            return getattr(service, "_connection", None)
        connection = await get_or_create(target_server_id)
        if getattr(service, "_connection", None) is not connection:
            service._connection = connection  # noqa: SLF001
        return connection

    async def _call_service_get_object_info(
        self,
        service: Any,
        *,
        force_refresh: bool,
    ) -> dict[str, Any] | None:
        get_object_info = getattr(service, "get_object_info", None)
        if not callable(get_object_info):
            return None
        try:
            return await get_object_info(force_refresh=force_refresh)
        except TypeError:
            return await get_object_info()

    # ------------------------------------------------------------------
    # Phase 8c-2: preview thumbnail surface
    # ------------------------------------------------------------------

    def _on_ai_effect_preview_event(self, payload: Any) -> None:
        """Bus subscriber: route ``ai_effect.preview`` to the thumbnail.

        Payload shape: ``{"layer_id": str, "preview_png": bytes}``. We
        ignore previews aimed at a different layer (the user may have
        switched while a generation was in flight) and silently drop
        malformed payloads — the controller-side throttle guarantees
        we get at most one event per ``ui.preview_throttle_ms`` window
        so noisy logging would just clutter the console.
        """
        if not isinstance(payload, dict):
            return
        layer_id = payload.get("layer_id")
        png = payload.get("preview_png")
        if not isinstance(layer_id, str) or not isinstance(png, (bytes, bytearray)):
            return
        self.set_preview_image(layer_id, bytes(png))

    def set_preview_image(self, layer_id: str, png_bytes: bytes) -> None:
        """Public entry point for preview-thumbnail updates.

        Called either via the bus subscriber above (the production
        wiring in app.py) or directly from tests. ``layer_id`` is
        compared against ``_current_ai_effect_id`` so previews from
        background generations do not overwrite the currently-shown
        layer's thumbnail.
        """
        if layer_id != self._current_ai_effect_id:
            return
        label = self._preview_label
        if label is None:
            # ComfyUI intentionally has no result thumbnail. This also covers
            # controller emissions that race with a panel rebuild.
            return
        from PySide6.QtGui import QImage, QPixmap  # noqa: PLC0415

        image = QImage()
        if not image.loadFromData(bytes(png_bytes)):
            logger.debug("set_preview_image: QImage.loadFromData failed; dropped")
            return
        pixmap = QPixmap.fromImage(image)
        # Cap at panel width — the AI Effect dock is typically narrow
        # so we scale to fit. KeepAspectRatio + SmoothTransformation
        # gives a clean thumbnail without crushing portrait previews.
        target_w = max(120, label.width() or 240)
        scaled = pixmap.scaledToWidth(
            target_w,
            Qt.TransformationMode.SmoothTransformation,
        )
        label.setPixmap(scaled)
        label.show()

    def _show_auto_connect_failure_toast(  # noqa: PLR0911 - linear early-return chain for diagnostic logging
        self, exc: BaseException
    ) -> None:
        """Surface an auto-connect failure on the main-window status bar.

        Two delivery paths are tried so a dogfood "I see no toast"
        report does not silently fail:

        1. ``host.events.emit("ai_effect.toast", ...)``: app.py
           subscribes and routes through ``_notify_ai_effect_error``
           which uses ``self.main_window.statusBar()`` directly.
           Same surface as Phase 6-g's AIError toast.
        2. Direct ``host.ui.get_main_window().statusBar().showMessage``
           as a fallback when the bus subscription is missing
           (older host wiring, test stand-ins).

        Each early-return logs at WARNING so loomstroke.log shows
        which channel reached / failed.
        """
        del exc  # currently unused — kept for future "details" toast
        host = self._host
        if host is None:
            logger.warning("Auto-connect toast skipped: no host attached")
            return
        title = "AI Effect: ComfyUI に自動接続できませんでした"
        detail = "ComfyUI パネルの Connect で手動接続してください"

        # Channel 1: event bus. app.py subscribes and routes through
        # ``_notify_ai_effect_error`` which uses ``self.main_window``
        # directly. If the subscriber isn't wired (older host, test
        # stand-in, GC'd weakref) the emit is a no-op — channel 2
        # below is the safety net.
        events = getattr(host, "events", None)
        emit = getattr(events, "emit", None) if events is not None else None
        if callable(emit):
            try:
                emit("ai_effect.toast", {"title": title, "detail": detail})
                logger.info("Auto-connect failure toast emitted on bus (ai_effect.toast)")
            except Exception:
                logger.warning(
                    "Auto-connect toast: bus emit raised; channel 2 fallback " "still attempted",
                    exc_info=True,
                )

        # Channel 2: direct status-bar access via UIService. Runs
        # alongside channel 1 (no early return) so the toast surfaces
        # even when the bus subscription is silently dropped (the
        # weakref-based EventBus has a known GC-then-no-op failure
        # mode). Qt's ``showMessage`` overwrites the previous one, so
        # the user sees a single toast — no duplicate stacking.
        ui = getattr(host, "ui", None)
        if ui is None:
            logger.warning("Auto-connect toast skipped: host has no 'ui' service")
            return
        getter = getattr(ui, "get_main_window", None)
        if not callable(getter):
            logger.warning("Auto-connect toast skipped: ui.get_main_window not callable")
            return
        mw: Any = None
        try:
            mw = getter()
        except Exception:
            logger.warning("Auto-connect toast skipped: get_main_window raised", exc_info=True)
            return
        if mw is None:
            logger.warning(
                "Auto-connect toast skipped: main window not yet attached "
                "(ui_service.set_main_window() has not run)"
            )
            return
        status_fn = getattr(mw, "statusBar", None)
        if not callable(status_fn):
            logger.warning("Auto-connect toast skipped: main window has no statusBar()")
            return
        try:
            status_bar = status_fn()
            status_bar.showMessage(f"{title} — {detail}", 8000)
            logger.info("Auto-connect failure toast posted to status bar (direct)")
        except Exception:
            logger.warning("Auto-connect toast: showMessage raised", exc_info=True)

    # ------------------------------------------------------------------
    # Auto-connect failure banner (in-panel persistent retry surface)
    # ------------------------------------------------------------------

    def _show_auto_connect_failure_banner(self) -> None:
        """Insert a [Retry] banner at the top of the AI Effect group.

        Idempotent: re-showing while the banner is already mounted is a
        no-op. The banner is destroyed during ``_clear_ai_effect_ui``
        and re-created on the next ``_build_ai_effect_ui`` whenever
        ``_auto_connect_banner_visible`` is True, so it survives layer
        rebuilds without leaking widgets.
        """
        if self._ai_effect_group is None:
            # Panel not built yet (e.g. failure fired during early
            # startup). The flag is set; the next build will mount it.
            return
        if self._auto_connect_banner is not None:
            return
        from PySide6.QtWidgets import QFrame  # noqa: PLC0415

        banner = QFrame(self._ai_effect_group)
        banner.setObjectName("aiAutoConnectBanner")
        banner.setFrameShape(QFrame.Shape.StyledPanel)
        banner.setStyleSheet(self._auto_connect_banner_stylesheet())
        layout = QHBoxLayout(banner)
        layout.setContentsMargins(8, 6, 6, 6)
        layout.setSpacing(6)
        label = QLabel(_ts("Could not auto-connect to ComfyUI."), banner)
        layout.addWidget(label, 1)
        retry_btn = QPushButton(_ts("Retry"), banner)
        retry_btn.setObjectName("aiAutoConnectRetryButton")
        retry_btn.clicked.connect(self._on_auto_connect_retry_clicked)
        layout.addWidget(retry_btn)
        dismiss_btn = QToolButton(banner)
        dismiss_btn.setObjectName("aiAutoConnectDismissButton")
        # MULTIPLICATION SIGN U+00D7 is the conventional close-banner glyph.
        # Use the Python escape so ruff RUF001 stays quiet on the literal.
        dismiss_btn.setText("\u00d7")
        dismiss_btn.setToolTip(_ts("Dismiss"))
        dismiss_btn.clicked.connect(self._on_auto_connect_dismiss_clicked)
        layout.addWidget(dismiss_btn)

        # Insert at the top of the group's layout so the banner sits
        # above slots / params and is the first thing the user sees.
        group_layout = self._ai_effect_group.layout()
        if group_layout is not None:
            group_layout.insertWidget(0, banner)
        self._auto_connect_banner = banner

    def _hide_auto_connect_failure_banner(self) -> None:
        """Detach + delete the banner widget. Idempotent."""
        if self._auto_connect_banner is None:
            return
        with contextlib.suppress(Exception):
            self._auto_connect_banner.setParent(None)
            self._auto_connect_banner.deleteLater()
        self._auto_connect_banner = None

    def _on_auto_connect_retry_clicked(self) -> None:
        """Bypass the cooldown gate and force one connect attempt.

        Explicit user action — distinct from the auto-fire path that
        the cooldown is meant to throttle. Resets the timestamp to
        ``-inf`` so subsequent layer-click auto-tries are not held
        back by this manual attempt either.
        """
        self._last_auto_connect_attempt_t = float("-inf")
        in_flight = self._auto_connect_task
        if in_flight is not None and not in_flight.done():
            return
        # Re-trigger the same scheduling path so service / connection /
        # registry resolution stays in one place. The cooldown reset
        # above guarantees the call will pass straight through.
        self._schedule_object_info_fetch_for_panel()

    def _on_auto_connect_dismiss_clicked(self) -> None:
        """User dismissed the banner — hide it without retrying.

        The banner will reappear on the next failed auto-connect
        attempt (e.g. user switches to another AI Effect layer and the
        cooldown window has elapsed). Hides via the flag + helper so a
        rebuild after dismiss does not re-mount the widget.
        """
        self._auto_connect_banner_visible = False
        self._hide_auto_connect_failure_banner()

    def _enrich_param_entry(
        self,
        entry: Mapping[str, Any],
        enrichment: dict[str, Any] | None,
    ) -> Mapping[str, Any]:
        """Layer object_info-derived constraints over a manifest entry.

        Manifest values win when present (so a hand-tuned preset that
        explicitly narrowed a range stays narrow even if the backend
        permits more). Only fields the manifest left blank are
        filled in from the object_info side.
        """
        if not enrichment:
            return entry
        record = enrichment.get(str(entry.get("internal_id") or ""))
        if not record:
            return entry
        merged = dict(entry)
        # Param type: prefer manifest if it set a non-string type, otherwise
        # adopt the schema-derived one (catches old presets that wrote
        # "string" for what's actually an enum). Promote string->enum,
        # int->enum (ComfyUI sometimes uses int-valued combos), or
        # promote int->float when ComfyUI's schema says FLOAT (the
        # editor's authoring path infers ``int`` from a default value
        # like ``cfg_scale: 7`` whenever object_info wasn't yet cached
        # — without this promotion the panel renders an IntParamWidget
        # for a parameter whose backend step is 0.1).
        record_type = record.get("type")
        merged_type = merged.get("type")
        if record_type and record_type != merged_type:
            promote = (
                record_type == "enum"
                or merged_type in (None, "string")
                or (record_type == "float" and merged_type == "int")
            )
            if promote and merged_type in (None, "string", "int", "float"):
                merged["type"] = record_type
        # range: field-level merge. Manifest values win for fields it
        # specifies (so an author-narrowed min/max stays narrow), but
        # missing fields — most importantly ``step`` — are filled from
        # the schema. Without this, a manifest range of ``{min, max}``
        # with no step would shadow the ComfyUI-supplied step=0.1 and
        # the FloatParamWidget heuristic would fall back to step=1.0.
        record_range = record.get("range")
        if isinstance(record_range, Mapping):
            manifest_range_raw = merged.get("range")
            manifest_range = manifest_range_raw if isinstance(manifest_range_raw, Mapping) else {}
            merged_range: dict[str, Any] = dict(record_range)
            for key, value in manifest_range.items():
                if value is not None:
                    merged_range[key] = value
            merged["range"] = merged_range
        if merged.get("choices") is None and record.get("choices"):
            merged["choices"] = list(record["choices"])
        return merged

    # ------------------------------------------------------------------
    # Per-node parameter grouping
    # ------------------------------------------------------------------

    def _build_param_node_resolver(self, state: Any) -> Any:
        """Return a callable that maps ``internal_id`` → node group label.

        The label is shaped ``"Node#{node_id} {class_type}"`` so the
        Effects panel mirrors the Editor's parameter section. When the
        preset snapshot doesn't expose a ``node_mappings`` / ``workflow``
        pair (test stubs, future gateways without a workflow concept)
        we fall back to a single ``"Parameters"`` bucket so the panel
        still renders.
        """
        snapshot = getattr(state, "preset_snapshot", None)
        if not isinstance(snapshot, Mapping):
            return lambda _internal_id: _ts("Parameters")
        gateway_file = snapshot.get("gateway_file", {})
        if not isinstance(gateway_file, Mapping):
            return lambda _internal_id: _ts("Parameters")
        workflow = gateway_file.get("workflow", {})
        mappings = gateway_file.get("node_mappings", {})
        if not isinstance(workflow, Mapping) or not isinstance(mappings, Mapping):
            return lambda _internal_id: _ts("Parameters")
        # Pre-compute internal_id → label so the per-row lookup stays
        # O(1). Unmapped ids get the generic bucket so the user still
        # sees them rather than silently losing the row.
        cache: dict[str, str] = {}
        fallback = _ts("Parameters")
        for internal_id, ref in mappings.items():
            if not isinstance(ref, dict):
                continue
            node_id = ref.get("node_id")
            if node_id is None:
                continue
            node = workflow.get(str(node_id))
            class_type = ""
            if isinstance(node, Mapping):
                raw_class = node.get("class_type")
                if isinstance(raw_class, str):
                    class_type = raw_class
            label = f"Node#{node_id} {class_type}".strip()
            cache[str(internal_id)] = label or fallback
        return lambda internal_id: cache.get(str(internal_id), fallback)

    def _build_node_group(self, label: str) -> tuple[QGroupBox, QFormLayout]:
        """Construct one collapsible node group.

        ``QGroupBox`` is set checkable so the title bar gets a checkbox
        the user can click to fold the parameters underneath. We
        override the default disable-children behaviour by hiding the
        inner content widget instead — the user expects collapse, not
        a greyed-out form.
        """
        group_box = QGroupBox(label, self._ai_effect_group)
        group_box.setObjectName("aiNodeGroup")
        group_box.setCheckable(True)
        group_box.setChecked(True)
        group_box.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        outer = QVBoxLayout(group_box)
        outer.setContentsMargins(8, 14, 8, 8)
        outer.setSpacing(6)

        content = QWidget(group_box)
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(2)
        outer.addWidget(content)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        # Same wrap policy the legacy effect form uses — lets the dock
        # shrink without horizontal scroll.
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        content_layout.addLayout(form)

        # Toggle = fold/unfold. Default Qt behaviour disables children,
        # which is the wrong UX here — the user wants the widgets gone,
        # not greyed out.
        group_box.toggled.connect(content.setVisible)
        return group_box, form

    # ------------------------------------------------------------------
    # Step 3: Input slots
    # ------------------------------------------------------------------
    def _build_ai_slots_ui(self, parent_layout: QVBoxLayout, manifest: Mapping[str, Any]) -> None:
        """Build one row per ``manifest.input_slots`` entry.

        No-op when the manifest declares no slots. Each row is a
        QComboBox (populated with source-layer candidates) plus a
        clear-binding button (``x``). The combo's ``currentIndexChanged``
        writes back through :meth:`_on_ai_slot_combo_changed`.
        """
        slots_raw = manifest.get("input_slots", [])
        if not isinstance(slots_raw, list) or not slots_raw:
            return
        slots_title = QLabel(_ts("Input slots"), self._ai_effect_group)
        slots_title.setObjectName("aiSectionHeading")
        parent_layout.addWidget(slots_title)

        candidates = self._collect_source_layer_candidates()
        for slot in slots_raw:
            if not isinstance(slot, Mapping):
                continue
            internal_id = slot.get("internal_id")
            if not isinstance(internal_id, str) or not internal_id:
                continue
            display = slot.get("display_name") or internal_id
            required = bool(slot.get("required", False))
            suffix = _ts(" (required)") if required else ""
            row = self._build_ai_slot_row(str(internal_id), str(display), suffix, candidates)
            parent_layout.addWidget(row)

    def _build_ai_slot_row(
        self,
        internal_id: str,
        display_name: str,
        suffix: str,
        candidates: list[tuple[str, str]],
    ) -> QWidget:
        """Compose a single slot row: name label on top, combo + clear
        button below.

        Stacked vertically so a narrow dock width never clips the combo
        or the clear button — a long layer name in the label
        previously squeezed the combo down to a few pixels and pushed
        the clear button off-screen. Vertical stack means the combo
        always gets the full panel width.
        """
        row = QWidget(self._ai_effect_group)
        outer = QVBoxLayout(row)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(2)

        name_label = QLabel(f"• {display_name}{suffix}", row)
        name_label.setObjectName("aiSlotLabel")
        name_label.setWordWrap(True)
        outer.addWidget(name_label)

        combo_row = QWidget(row)
        layout = QHBoxLayout(combo_row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        outer.addWidget(combo_row)

        combo = QComboBox(row)
        combo.addItem(_ts("(unbound)"), userData=None)
        for layer_id, display in candidates:
            combo.addItem(display, userData=layer_id)
        # Cap the combo's natural sizeHint to a small min: long layer
        # names should NOT widen the panel. The popup still shows the
        # full text and the user can resize the dock if they want
        # more horizontal room.
        combo.setMinimumContentsLength(0)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        combo.currentIndexChanged.connect(
            lambda _idx, sid=internal_id, c=combo: self._on_ai_slot_combo_changed(sid, c)
        )
        layout.addWidget(combo, stretch=1)

        clear_btn = QToolButton(row)
        clear_btn.setText("×")  # noqa: RUF001
        clear_btn.setToolTip(_ts("Clear binding"))
        clear_btn.clicked.connect(
            lambda _checked=False, sid=internal_id: self._on_ai_slot_clear_clicked(sid)
        )
        layout.addWidget(clear_btn)

        self._ai_slot_combos[internal_id] = combo
        self._ai_slot_rows[internal_id] = row
        return row

    def _collect_source_layer_candidates(self) -> list[tuple[str, str]]:
        """Walk the canvas tree and return valid source-layer options.

        Filters match the previous AIEffectPanel binder path:
        - Drop the AI Effect layer we are binding slots for (no self).
        - Drop other ``ai_effect`` layers (circular reference).
        - Drop ``group`` / ``mask`` (not composable rasters on their own;
          group children still surface via the recursion).
        - Keep ``raster`` / ``vector``.
        """
        if self._host is None:
            return []
        canvas = getattr(self._host, "canvas", None)
        if canvas is None:
            return []
        layers = getattr(canvas, "_layers", None)
        if not isinstance(layers, list):
            return []
        exclude_id = self._current_ai_effect_id or ""
        result: list[tuple[str, str]] = []

        def _walk(lst: list[Any]) -> None:
            for layer in lst:
                lid = getattr(layer, "id", None)
                ltype = str(getattr(layer, "layer_type", "raster"))
                if ltype in ("ai_effect", "group", "mask"):
                    if ltype == "group":
                        children = getattr(layer, "children", None)
                        if isinstance(children, list):
                            _walk(children)
                    continue
                if ltype not in ("raster", "vector"):
                    continue
                if not isinstance(lid, str) or not lid or lid == exclude_id:
                    continue
                name = str(getattr(layer, "name", "") or lid)
                result.append((lid, name))

        try:
            _walk(layers)
        except Exception:
            logger.debug("source-layer enumeration failed", exc_info=True)
            return []
        return result

    def _select_slot_target(self, combo: QComboBox, target_id: str | None) -> None:
        """Select the combo entry whose userData matches ``target_id``.

        Falls back to the "(unbound)" sentinel (index 0) when the
        target layer is absent from the candidate list. Signals are
        blocked so programmatic selection does not fire a write-back.
        """
        combo.blockSignals(True)
        try:
            index = 0  # "(unbound)" sentinel
            if target_id:
                for i in range(combo.count()):
                    if combo.itemData(i) == target_id:
                        index = i
                        break
            combo.setCurrentIndex(index)
        finally:
            combo.blockSignals(False)

    def _on_ai_slot_combo_changed(self, internal_id: str, combo: QComboBox) -> None:
        """Write the selected target layer back as a SlotRef."""
        if self._updating:
            return
        target_id = combo.currentData()
        self._write_slot_ref(internal_id, target_id if isinstance(target_id, str) else None)

    def _on_ai_slot_clear_clicked(self, internal_id: str) -> None:
        """Clear the slot binding (combo → "(unbound)")."""
        combo = self._ai_slot_combos.get(internal_id)
        if combo is not None:
            self._select_slot_target(combo, None)
        self._write_slot_ref(internal_id, None)

    def _write_slot_ref(self, internal_id: str, target_layer_id: str | None) -> None:
        """Push an updated SlotRef through ``set_ai_effect_layer_state``."""
        from loomstroke.plugins.ai_effect.data_model import SlotRef  # noqa: PLC0415

        layer_id = self._current_ai_effect_id
        if layer_id is None or self._host is None:
            return
        canvas = getattr(self._host, "canvas", None)
        if canvas is None:
            return
        finder = getattr(canvas, "_find_layer_by_id_tree", None)
        if not callable(finder):
            return
        layer = finder(layer_id)
        if layer is None:
            return
        ai_state = getattr(layer, "ai_effect", None)
        if ai_state is None:
            return
        setter = getattr(canvas, "set_ai_effect_layer_state", None)
        if not callable(setter):
            return
        slot_refs = dict(getattr(ai_state, "slot_refs", {}) or {})
        if target_layer_id is None:
            slot_refs[internal_id] = None
        else:
            # Preserve exclude_blend if the slot already had a SlotRef;
            # default to False for fresh bindings.
            previous = slot_refs.get(internal_id)
            exclude_blend = bool(getattr(previous, "exclude_blend", False))
            slot_refs[internal_id] = SlotRef(layer_id=target_layer_id, exclude_blend=exclude_blend)
        param_values = dict(getattr(ai_state, "param_values", {}) or {})
        setter(layer_id, param_values=param_values, slot_refs=slot_refs)
        logger.debug("Updated AI effect %s slot %s -> %s", layer_id, internal_id, target_layer_id)
        # Phase 6-i: slot rebinding counts as an input change → run
        # the same auto-fire dispatch that param edits go through.
        self._maybe_schedule_auto_generate(ai_state)

    # ------------------------------------------------------------------
    # Step 4: Trigger combo + Generate button
    # ------------------------------------------------------------------
    def _build_ai_action_bar(self, parent_layout: QVBoxLayout) -> None:
        """Trigger selector — Generate moved to the Layers Panel row.

        Phase 6-g moved the Generate button from this panel onto each
        AI Effect layer row in the Layers Panel (per user request:
        the button now sits next to the layer it operates on, not in
        a separate panel). Trigger stays here because it's a per-
        preset configuration, not a per-firing action.
        """
        outer = QVBoxLayout()
        outer.setContentsMargins(0, 4, 0, 0)
        outer.setSpacing(4)

        trigger_row = QHBoxLayout()
        trigger_row.setContentsMargins(0, 0, 0, 0)
        trigger_row.setSpacing(8)
        trigger_label = QLabel(_ts("Trigger"), self._ai_effect_group)
        trigger_label.setObjectName("aiTriggerLabel")
        trigger_row.addWidget(trigger_label)

        combo = QComboBox(self._ai_effect_group)
        combo.setObjectName("aiTriggerCombo")
        for value in _TRIGGER_CHOICES:
            combo.addItem(_ts(value), value)
        combo.currentIndexChanged.connect(lambda _i: self._on_ai_trigger_changed())
        # Match slot combo: ignore sizeHint so the dock width drives
        # the combo's actual width, not the longest item label.
        combo.setMinimumContentsLength(0)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        trigger_row.addWidget(combo, stretch=1)
        self._ai_trigger_combo = combo

        outer.addLayout(trigger_row)
        parent_layout.addLayout(outer)
        from loomstroke.plugins.ai_effect.ui.batch_controls import mount  # noqa: PLC0415

        mount(self, parent_layout)

    def _build_ai_server_section(self, parent_layout: QVBoxLayout) -> None:
        """ComfyUI server selector for this AI Effect layer.

        URL editing lives in Preferences; this row only selects one of
        the registered server ids that generation_flow later writes into
        the ComfyUI gateway_file as ``target_server_id``.
        """
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)

        label = QLabel(_ts("Server"), self._ai_effect_group)
        label.setObjectName("aiServerLabel")
        row.addWidget(label)

        combo = QComboBox(self._ai_effect_group)
        combo.setObjectName("aiServerCombo")
        combo.setToolTip(_ts("Choose a ComfyUI server configured in Preferences."))
        combo.setMinimumContentsLength(0)
        combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        combo.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._populate_ai_server_combo(combo)
        combo.currentIndexChanged.connect(lambda _i: self._on_ai_server_changed())
        row.addWidget(combo, stretch=1)

        parent_layout.addLayout(row)
        self._ai_server_combo = combo

    def _populate_ai_server_combo(self, combo: QComboBox) -> None:
        try:
            from loomstroke.plugins.comfyui.server_registry import (  # noqa: PLC0415
                DEFAULT_LOCAL_COMFYUI_URL,
                ComfyUIServer,
                get_registry,
            )

            registry = get_registry()
            if not registry.list_all():
                registry.add(
                    ComfyUIServer(
                        id="local",
                        name="Local",
                        url=DEFAULT_LOCAL_COMFYUI_URL,
                        auto_detect=True,
                        is_default=True,
                    )
                )
            servers = registry.list_all()
        except Exception as exc:
            logger.warning("FilterPanel: failed to populate ComfyUI server list: %s", exc)
            servers = []

        for server in servers:
            combo.addItem(f"{server.name} — {server.url}", server.id)
        if combo.count() == 0:
            combo.addItem("Local — http://127.0.0.1:8188", "local")

    def _build_ai_output_to_self_section(self, parent_layout: QVBoxLayout) -> None:
        """Place the "Output to this layer" toggle right under the
        Input slots section.

        Lives between slots and parameters so the user reads it as
        "this layer's IO contract" alongside the slot bindings rather
        than buried in the action bar at the bottom. When checked,
        every slot row greys out — see :meth:`_apply_slot_disable_state`
        — because the next generation will overwrite this layer's
        pixels regardless of what those bindings produced.
        """
        output_self_checkbox = QCheckBox(
            _ts("Output to this layer (override)"), self._ai_effect_group
        )
        output_self_checkbox.setObjectName("aiOutputSelfCheckbox")
        output_self_checkbox.setToolTip(
            _ts(
                "When checked, every generated output is written back into this "
                "AI Effect layer instead of creating a new layer above it. "
                "Input slot bindings are disabled while this is active."
            )
        )
        output_self_checkbox.toggled.connect(self._on_ai_output_to_self_toggled)
        parent_layout.addWidget(output_self_checkbox)
        self._ai_output_to_self_checkbox = output_self_checkbox

    def _select_trigger_value(self, value: str) -> None:
        """Set the trigger combo without emitting currentIndexChanged."""
        combo = self._ai_trigger_combo
        if combo is None:
            return
        combo.blockSignals(True)
        try:
            index = 0
            for i in range(combo.count()):
                if combo.itemData(i) == value:
                    index = i
                    break
            combo.setCurrentIndex(index)
        finally:
            combo.blockSignals(False)

    def _on_ai_trigger_changed(self) -> None:  # noqa: PLR0911 - defensive guard chain
        """Write the selected trigger back onto ``state.trigger``.

        Matches the pre-merger AIEffectPanel behaviour: trigger is an
        in-memory hint only — it does not go through
        ``set_ai_effect_layer_state`` and does not emit LAYER_CHANGED.
        """
        if self._updating:
            return
        combo = self._ai_trigger_combo
        if combo is None:
            return
        value = combo.currentData()
        if not isinstance(value, str) or value not in _TRIGGER_CHOICES:
            return
        layer_id = self._current_ai_effect_id
        if layer_id is None or self._host is None:
            return
        canvas = getattr(self._host, "canvas", None)
        if canvas is None:
            return
        finder = getattr(canvas, "_find_layer_by_id_tree", None)
        if not callable(finder):
            return
        layer = finder(layer_id)
        if layer is None:
            return
        ai_state = getattr(layer, "ai_effect", None)
        if ai_state is None:
            return
        try:
            ai_state.trigger = value
        except Exception:
            logger.debug("Failed to set trigger on ai_effect state", exc_info=True)
            return
        logger.debug("AI effect %s trigger -> %s", layer_id, value)
        # Switching to ``manual`` cancels any pending auto-fire so the
        # user's "stop auto-generating" intent is honoured immediately
        # rather than firing one last time when the timer expires.
        # Switching to ``on_change`` / ``debounced`` does NOT pre-arm
        # the timer — generation only fires after an actual edit.
        if value == "manual" and self._ai_trigger_timer.isActive():
            self._ai_trigger_timer.stop()

    def _select_ai_server_value(self, target_server_id: Any) -> None:
        combo = self._ai_server_combo
        if combo is None:
            return
        server_id = target_server_id if isinstance(target_server_id, str) else ""
        index = combo.findData(server_id) if server_id else -1
        if index < 0:
            index = combo.findData("local")
        combo.blockSignals(True)
        try:
            combo.setCurrentIndex(index if index >= 0 else 0)
        finally:
            combo.blockSignals(False)

    def _on_ai_server_changed(self) -> None:  # noqa: PLR0911 - defensive guard chain
        """Write the selected ComfyUI server id onto ``AIEffectState``."""
        if self._updating:
            return
        combo = self._ai_server_combo
        if combo is None:
            return
        server_id = combo.currentData()
        if not isinstance(server_id, str) or not server_id:
            return
        layer_id = self._current_ai_effect_id
        if layer_id is None or self._host is None:
            return
        canvas = getattr(self._host, "canvas", None)
        if canvas is None:
            return
        finder = getattr(canvas, "_find_layer_by_id_tree", None)
        if not callable(finder):
            return
        layer = finder(layer_id)
        if layer is None:
            return
        ai_state = getattr(layer, "ai_effect", None)
        if ai_state is None:
            return
        if str(getattr(ai_state, "gateway_id", "") or "") != "comfyui":
            return
        try:
            ai_state.target_server_id = server_id
        except Exception:
            logger.debug("Failed to set target_server_id on ai_effect state", exc_info=True)
            return
        logger.debug("AI effect %s target_server_id -> %s", layer_id, server_id)

    def _select_output_to_self(self, checked: bool) -> None:
        """Set the output-to-self checkbox without emitting toggled."""
        cb = self._ai_output_to_self_checkbox
        if cb is None:
            return
        cb.blockSignals(True)
        try:
            cb.setChecked(checked)
        finally:
            cb.blockSignals(False)
        # Mirror the resulting greyed-out slots state — the toggled
        # signal is suppressed during a programmatic select, so this
        # call has to happen explicitly. Without it, restoring a
        # saved override=self layer would show enabled slot rows that
        # contradicted the active mode.
        self._apply_slot_disable_state(checked)

    def _apply_slot_disable_state(self, output_to_self: bool) -> None:
        """Grey out every slot row when "Output to this layer" is on.

        Acts on the cached ``_ai_slot_rows`` widget map so the entire
        row (label + combo + clear button) becomes non-interactive in
        a single Qt call. Disabling the row is preferred over hiding
        because the user still benefits from seeing the bindings their
        preset declared — disabled state communicates "these are
        ignored right now" without throwing the layout away.
        """
        for row in self._ai_slot_rows.values():
            row.setEnabled(not output_to_self)

    def _on_ai_output_to_self_toggled(self, checked: bool) -> None:
        """Write the override (``"self"`` or cleared) onto the layer.

        Goes through ``canvas.set_ai_effect_layer_output_mode_override``
        so the change publishes a LAYER_CHANGED event and persists in
        the document. ``checked=False`` clears the override so each
        output falls back to the preset's per-output mode (rather than
        forcing every output to ``"new_layer_above"``).
        """
        if self._updating:
            return
        layer_id = self._current_ai_effect_id
        if layer_id is None or self._host is None:
            return
        canvas = getattr(self._host, "canvas", None)
        if canvas is None:
            return
        setter = getattr(canvas, "set_ai_effect_layer_output_mode_override", None)
        if not callable(setter):
            logger.debug(
                "Canvas service missing set_ai_effect_layer_output_mode_override; "
                "Output-to-self toggle is a no-op"
            )
            return
        target = "self" if checked else None
        ok = bool(setter(layer_id, target))
        if not ok:
            logger.warning(
                "set_ai_effect_layer_output_mode_override failed for %s (target=%r)",
                layer_id,
                target,
            )
            return
        # Slots greyed out only after the canvas write succeeds —
        # mirrors the visible UI to the now-authoritative state.
        self._apply_slot_disable_state(checked)

    def _on_ai_generate_clicked(self) -> None:
        """Bound-layer convenience wrapper around :meth:`request_generate`."""
        layer_id = self._current_ai_effect_id
        if layer_id is None:
            return
        self.request_generate(layer_id)

    def request_generate(self, layer_id: str) -> None:
        """Public entry point — emit ``ai_generate_requested`` for ``layer_id``.

        Routing to :class:`AIEffectGenerationController` is wired in
        app.py — FilterPanel only signals intent so this plugin does
        not import AI Effect runtime machinery directly.

        Before emitting, applies any pending seed-mode mutation
        (increment / decrement / randomize) declared on
        :class:`SeedParamWidget` instances in
        :attr:`_ai_effect_widgets`, but only when the requested layer
        matches the one currently bound to this panel. The Layers-
        Panel Generate button (Phase 6-g) can fire for layers other
        than the one currently selected; in that case there are no
        widgets to mutate and the controller picks up whatever seed
        already lives in the canvas-side state. Mirrors the ComfyUI
        panel's ``_apply_seed_modes`` (called just before queue
        submit) for the bound-layer case.
        """
        if not ai_features_enabled() or not isinstance(layer_id, str) or not layer_id:
            return
        if (
            getattr(self, "_batch_controls", None) is not None
            and self._batch_controls.running
            and layer_id == self._current_ai_effect_id
        ):
            return
        if layer_id == self._current_ai_effect_id and self._native_comfy_panel is not None:
            self._native_comfy_panel.run(self._native_comfy_panel.commit(generate=True))
            return
        if layer_id == self._current_ai_effect_id:
            from loomstroke.plugins.ai_effect.ui.dynamic_prompt_submission import (  # noqa: PLC0415
                advance_live_prompts,
            )

            if not advance_live_prompts(self._ai_effect_widgets, self._host, layer_id):
                logger.warning("Could not prepare LivePrompt values for generation")
                return
            self._apply_seed_mode_pre_generate()
        self.ai_generate_requested.emit(layer_id)
        logger.debug("AI effect generate requested for %s", layer_id)

    def _apply_seed_mode_pre_generate(self) -> None:
        """Run ``apply_pre_generate_mutation`` on every seed widget.

        Side effect: each widget's value (and the underlying
        ``state.param_values``) is rewritten with the post-mutation
        seed *before* generate fires. The ``value_changed`` signal each
        widget emits flows through the existing
        ``_on_ai_param_changed`` write-back path, so the controller
        sees the new seed in ``ExecutionRequest.param_values``.

        Skipped silently for widgets without the dedicated method
        (``IntParamWidget`` / others) — only ``SeedParamWidget``
        instances participate, and the rest of the panel doesn't need
        to know seed semantics.
        """
        from loomstroke.plugins.ai_effect.ui.param_widgets import (  # noqa: PLC0415
            SeedParamWidget,
        )

        for internal_id, widget in self._ai_effect_widgets.items():
            if not isinstance(widget, SeedParamWidget):
                continue
            try:
                new_value = widget.apply_pre_generate_mutation()
            except Exception:
                logger.exception(
                    "Seed mode mutation failed for %s; using existing value",
                    internal_id,
                )
                continue
            logger.debug(
                "Seed %s mutated to %s for next generate (mode=%s)",
                internal_id,
                new_value,
                widget.get_mode(),
            )

    # ------------------------------------------------------------------
    # Step 5: exclude_blend (⊘) receiver
    # ------------------------------------------------------------------
    def apply_exclude_blend_toggle(  # noqa: PLR0911 - defensive guard chain
        self, effect_layer_id: str, slot_id: str, new_value: bool
    ) -> None:
        """Flip ``SlotRef.exclude_blend`` on one slot of an AI Effect layer.

        Called by :class:`LayerPanel` via its
        ``ai_effect_exclude_blend_toggled`` signal (wired in app.py).
        Routes through ``set_ai_effect_layer_state`` so the Phase
        2-6-3 registry cascade + LAYER_CHANGED emission fire just
        like a regular slot write-back.

        Guards:
        - Unknown / non-ai_effect layer → silent no-op.
        - Slot has no bound SlotRef → no-op (⊘ is only meaningful for
          bound slots).
        - Missing canvas setter → no-op.
        """
        from loomstroke.plugins.ai_effect.data_model import SlotRef  # noqa: PLC0415

        if self._host is None:
            return
        canvas = getattr(self._host, "canvas", None)
        if canvas is None:
            return
        finder = getattr(canvas, "_find_layer_by_id_tree", None)
        if not callable(finder):
            return
        layer = finder(effect_layer_id)
        if layer is None or getattr(layer, "layer_type", "") != "ai_effect":
            return
        ai_state = getattr(layer, "ai_effect", None)
        if ai_state is None:
            return
        slot_refs_src = getattr(ai_state, "slot_refs", None) or {}
        if not isinstance(slot_refs_src, Mapping):
            return
        current = slot_refs_src.get(slot_id)
        if current is None:
            logger.debug(
                "exclude_blend toggle ignored: slot %s on %s has no ref",
                slot_id,
                effect_layer_id,
            )
            return
        target_layer_id = getattr(current, "layer_id", None)
        if not isinstance(target_layer_id, str) or not target_layer_id:
            return
        setter = getattr(canvas, "set_ai_effect_layer_state", None)
        if not callable(setter):
            return
        slot_refs = dict(slot_refs_src)
        slot_refs[slot_id] = SlotRef(layer_id=target_layer_id, exclude_blend=bool(new_value))
        param_values = dict(getattr(ai_state, "param_values", {}) or {})
        setter(effect_layer_id, param_values=param_values, slot_refs=slot_refs)
        logger.debug(
            "exclude_blend toggled: layer=%s slot=%s -> %s",
            effect_layer_id,
            slot_id,
            new_value,
        )

    def apply_slot_bind(self, effect_layer_id: str, slot_id: str, source_layer_id: str) -> None:
        """Bind a slot to a source layer from a Layers Panel drag-drop.

        Called by :class:`LayerPanel` via its
        ``ai_effect_slot_bind_dropped`` signal (wired in app.py). The
        actual write-back goes through :meth:`_write_slot_ref` so the
        drop and the combo picker share one canvas-mutation path —
        the LAYER_CHANGED emission, the
        :class:`LayerReferenceRegistry` cascade, and the Effect
        Panel's combo refresh all fire identically regardless of
        which UI surfaced the bind.

        Guards (all silent no-ops, mirroring
        :meth:`apply_exclude_blend_toggle`'s defensive style):

        - The drop arrives even when the user has a different layer
          selected, so we have to drive the write against
          ``effect_layer_id`` rather than ``self._current_ai_effect_id``.
          Temporarily swap the panel's "current" id so
          ``_write_slot_ref`` operates on the right layer, then
          restore it.
        - Source layer id is rejected when empty / synthetic — the
          dropEvent already filters synthetic ids out, but we
          double-check here so a future caller can't misuse the
          method.
        """
        if not source_layer_id or source_layer_id.startswith("__slot::"):
            return
        if self._host is None:
            return
        canvas = getattr(self._host, "canvas", None)
        if canvas is None:
            return
        finder = getattr(canvas, "_find_layer_by_id_tree", None)
        if not callable(finder):
            return
        if finder(effect_layer_id) is None:
            return
        previous = self._current_ai_effect_id
        self._current_ai_effect_id = effect_layer_id
        try:
            self._write_slot_ref(slot_id, source_layer_id)
        finally:
            self._current_ai_effect_id = previous
        logger.debug(
            "slot bind via drop: layer=%s slot=%s -> source=%s",
            effect_layer_id,
            slot_id,
            source_layer_id,
        )

    def _on_ai_param_changed(  # noqa: PLR0911 - guard chain for defensive write-back
        self, internal_id: str, value: Any
    ) -> None:
        """Write an AI param edit back through canvas_service.

        Preserves the existing ``slot_refs`` verbatim — the slot UI
        lands in Step 3, so this handler only rewrites ``param_values``.
        Uses ``set_ai_effect_layer_state`` so the Phase 2-6-3
        reference cascade + LAYER_CHANGED emission fire automatically.
        """
        if self._updating:
            return
        layer_id = self._current_ai_effect_id
        if layer_id is None or self._host is None:
            return
        canvas = getattr(self._host, "canvas", None)
        if canvas is None:
            return
        finder = getattr(canvas, "_find_layer_by_id_tree", None)
        if not callable(finder):
            return
        layer = finder(layer_id)
        if layer is None:
            return
        ai_state = getattr(layer, "ai_effect", None)
        if ai_state is None:
            return
        setter = getattr(canvas, "set_ai_effect_layer_state", None)
        if not callable(setter):
            return
        # Snapshot every widget so the write-back reflects any in-flight
        # edits the user made to other fields in the same tick.
        param_values: dict[str, Any] = dict(getattr(ai_state, "param_values", {}) or {})
        for wid_id, w in self._ai_effect_widgets.items():
            try:
                param_values[wid_id] = w.get_value()
            except Exception:
                logger.debug("get_value failed on %s", wid_id, exc_info=True)
        param_ui_state = self._collect_ai_param_ui_state(ai_state)
        slot_refs = dict(getattr(ai_state, "slot_refs", {}) or {})
        setter(
            layer_id,
            param_values=param_values,
            slot_refs=slot_refs,
            param_ui_state=param_ui_state,
        )
        logger.debug("Updated AI effect %s param %s=%s", layer_id, internal_id, value)
        # Phase 6-i: param edits auto-fire generation when the layer's
        # trigger is non-manual. Slot binding writes go through their
        # own helper so they share the same dispatch.
        self._maybe_schedule_auto_generate(ai_state)

    # ------------------------------------------------------------------
    # Phase 6-i: Trigger dispatch (on_change / debounced)
    # ------------------------------------------------------------------
    def _maybe_schedule_auto_generate(self, ai_state: Any) -> None:
        """Restart the per-layer auto-fire timer based on ``ai_state.trigger``.

        - ``manual``: no auto-fire — the user clicks Generate.
        - ``on_change``: 250ms coalescing delay so a burst of edits
          (typing in a prompt, dragging a slider) collapses into one
          generation rather than spamming the gateway.
        - ``debounced``: longer prefs-driven delay (default 2000ms).

        Called from both ``_on_ai_param_changed`` and ``_write_slot_ref``
        so a slot rebind triggers re-generation just like a param edit
        does — both are inputs to the gateway run.
        """
        if getattr(self, "_batch_controls", None) is not None and self._batch_controls.running:
            self._ai_trigger_timer.stop()
            return
        trigger = str(getattr(ai_state, "trigger", "manual") or "manual")
        if trigger == "manual":
            self._ai_trigger_timer.stop()
            return
        if trigger == "on_change":
            delay_ms = 250
        elif trigger == "debounced":
            delay_ms = self._resolve_debounce_ms()
        else:
            # Unknown trigger value — do nothing rather than guess.
            self._ai_trigger_timer.stop()
            return
        self._ai_trigger_timer.start(max(0, int(delay_ms)))

    def _resolve_debounce_ms(self) -> int:
        """Pull the debounce delay from the AI Effect preferences.

        Falls back to the schema default (2000ms) when the host
        doesn't expose preferences yet — keeps the panel functional
        in tests and in early-startup states.
        """
        if self._host is None:
            return 2000
        getter = getattr(self._host, "get_service", None)
        if not callable(getter):
            return 2000
        try:
            prefs = getter("ai_effect_preferences")
        except Exception:
            return 2000
        defaults = getattr(prefs, "defaults", None)
        ms = getattr(defaults, "debounce_ms", None)
        if isinstance(ms, int) and ms >= 0:
            return ms
        return 2000

    def _collect_ai_param_ui_state(self, ai_state: Any) -> dict[str, Any]:
        """Collect optional LoomStroke-only UI state from visible AI widgets."""
        existing = getattr(ai_state, "param_ui_state", None) or {}
        param_ui_state: dict[str, Any] = (
            copy.deepcopy(existing) if isinstance(existing, Mapping) else {}
        )
        for wid_id, widget in self._ai_effect_widgets.items():
            getter = getattr(widget, "get_ui_state", None)
            if not callable(getter):
                continue
            try:
                ui_state = getter()
            except Exception:
                logger.debug("get_ui_state failed on %s", wid_id, exc_info=True)
                continue
            if isinstance(ui_state, Mapping) and ui_state:
                param_ui_state[wid_id] = copy.deepcopy(dict(ui_state))
            else:
                param_ui_state.pop(wid_id, None)
        return param_ui_state

    def _on_ai_trigger_timer_fired(self) -> None:
        """Timer expiry → fire generation for the bound layer.

        Re-resolves the bound layer rather than capturing it at start
        time so a layer-switch or layer-deletion mid-debounce does
        not fire generation against a stale id.
        """
        layer_id = self._current_ai_effect_id
        if not isinstance(layer_id, str) or not layer_id:
            return
        logger.debug("AI effect trigger timer fired for %s", layer_id)
        self.request_generate(layer_id)

    def _commit_current_ai_effect_values(self) -> bool:  # noqa: PLR0911 - defensive guard path
        """Flush visible AI Effect widget values into the selected layer state.

        Most AI Effect parameter widgets write through immediately. This
        explicit flush covers controls whose edit is still in a transient UI
        state when the user switches layers or the panel detaches, matching the
        regular Effect path's "layer owns the current parameter dict" behaviour.
        """
        if self._updating:
            return False
        layer_id = self._current_ai_effect_id
        if layer_id is None or self._host is None or not self._ai_effect_widgets:
            return False
        canvas = getattr(self._host, "canvas", None)
        if canvas is None:
            return False
        finder = getattr(canvas, "_find_layer_by_id_tree", None)
        if not callable(finder):
            return False
        layer = finder(layer_id)
        if layer is None or getattr(layer, "layer_type", "") != "ai_effect":
            return False
        ai_state = getattr(layer, "ai_effect", None)
        if ai_state is None:
            return False
        setter = getattr(canvas, "set_ai_effect_layer_state", None)
        if not callable(setter):
            return False

        param_values: dict[str, Any] = dict(getattr(ai_state, "param_values", {}) or {})
        original_ui_state = (
            copy.deepcopy(getattr(ai_state, "param_ui_state", None))
            if isinstance(getattr(ai_state, "param_ui_state", None), Mapping)
            else {}
        )
        changed = False
        for wid_id, widget in self._ai_effect_widgets.items():
            try:
                value = widget.get_value()
            except Exception:
                logger.debug("get_value failed on %s during AI effect flush", wid_id, exc_info=True)
                continue
            if param_values.get(wid_id) != value:
                param_values[wid_id] = value
                changed = True
        param_ui_state = self._collect_ai_param_ui_state(ai_state)
        if param_ui_state != original_ui_state:
            changed = True
        if not changed:
            return False

        slot_refs = dict(getattr(ai_state, "slot_refs", {}) or {})
        setter(
            layer_id,
            param_values=param_values,
            slot_refs=slot_refs,
            param_ui_state=param_ui_state,
        )
        logger.debug("Flushed AI effect %s params before panel switch/detach", layer_id)
        return True
