"""AI Effect coordinator for :mod:`loomstroke.app`.

The public entry points remain on ``LoomStrokeApp``; this coordinator owns the
AI Effect subsystem state and implementation behind those facade methods.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping
from typing import Any, cast

from PySide6.QtWidgets import QMainWindow

from .app_ai_effect_gallery import _AIEffectGalleryAdapter
from .app_ai_effect_output_names import output_layer_names
from .app_ai_effect_output_result import AIEffectOutputApplication, observed_output_application

logger = logging.getLogger(__name__)

_AI_EFFECT_MANAGER_PANEL_ID = "ui.ai_effect_manager"
_AI_EFFECT_MANAGER_PANEL_OWNER = "loomstroke.ai_effect"


class AIEffectCoordinator:
    """Own AI Effect startup, gateway wiring, launchers, and output dispatch."""

    _ai_effect_manager_panel: Any | None
    _ai_effect_preset_manager_window: Any | None

    _STATE_ATTRS = frozenset(
        {
            "_ai_effect_preferences",
            "_ai_effect_state",
            "_ai_effect_state_path",
            "_ai_effect_controller",
            "_ai_effect_gen_controller",
            "_ai_effect_preset_library",
            "_ai_effect_gateway_registry",
            "_ai_effect_editor_window",
            "_ai_effect_manager_panel",
            "_ai_effect_preset_manager_window",
            "_ai_effect_telemetry",
            "_ai_effect_preview_emitter",
            "_ai_effect_toast_handler",
        }
    )

    def __init__(self, app: Any) -> None:
        object.__setattr__(self, "_app", app)
        for name in self._STATE_ATTRS:
            descriptor = getattr(type(app), name, None)
            value = None if isinstance(descriptor, property) else getattr(app, name, None)
            object.__setattr__(self, name, value)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._app, name)

    def __setattr__(self, name: str, value: Any) -> None:
        object.__setattr__(self, name, value)
        if name in self._STATE_ATTRS:
            app = object.__getattribute__(self, "_app")
            descriptor = getattr(type(app), name, None)
            if not isinstance(descriptor, property):
                try:
                    setattr(app, name, value)
                except Exception:
                    logger.debug("AI Effect state mirror failed for %s", name, exc_info=True)

    async def _init_ai_effect_subsystem(  # noqa: PLR0915 - linear assembly of preferences/state/library/controllers/wiring
        self,
    ) -> None:
        """Wire up the AI Effect Layer subsystem.

        Phase 6-a: creates preferences / state / preset library / controllers /
        ComfyUI backend and gateway, then registers the gateway. Nothing is
        attached to the UI yet — Phase 6-b and later consume these attributes.

        Failure here is non-fatal: if ComfyUI isn't reachable / user_data is
        missing / etc., the app continues without AI Effect. UI code that
        branches on ``self._ai_effect_gen_controller is None`` handles the
        absent case gracefully.
        """
        from pathlib import Path as _Path  # noqa: PLC0415

        from .platform.user_dirs import default_user_root  # noqa: PLC0415
        from .plugins.ai_effect.controller import AIEffectController  # noqa: PLC0415
        from .plugins.ai_effect.generation_flow import (  # noqa: PLC0415
            AIEffectGenerationController,
        )
        from .plugins.ai_effect.preset_library import AIEffectPresetLibrary  # noqa: PLC0415
        from .plugins.ai_effect.registry import GatewayRegistry  # noqa: PLC0415
        from .plugins.ai_effect.settings import (  # noqa: PLC0415
            AIEffectPreferences,
            default_preferences_path,
        )
        from .plugins.ai_effect.state import OnboardingState  # noqa: PLC0415

        assert self.async_manager is not None

        # ---- user_data paths ----
        ai_root = default_user_root() / "ai_effect"
        prefs_path = default_preferences_path()
        state_path = ai_root / "state.json"
        presets_dir = ai_root / "presets"
        bundled_snapshot_dir = ai_root / "bundled"

        # Optional bundled source. The default build ships no .lsfx presets,
        # but downstream builds can provide their own.
        import loomstroke.plugins.ai_effect as _ae_pkg  # noqa: PLC0415

        bundled_src_dir = _Path(_ae_pkg.__file__).parent / "bundled_presets"

        # ---- load state / preferences ----
        prefs = AIEffectPreferences.load(prefs_path)
        state = OnboardingState.load(state_path)
        self._ai_effect_preferences = prefs
        self._ai_effect_state = state
        self._ai_effect_state_path = state_path

        # ---- preset library ----
        preset_library = AIEffectPresetLibrary(
            user_presets_dir=presets_dir,
            bundled_snapshot_dir=bundled_snapshot_dir,
            bundled_source_dir=bundled_src_dir if bundled_src_dir.exists() else None,
        )
        preset_library.initialize_bundled()
        loaded = preset_library.scan_and_load()
        # Path + count both logged so a "presets gone after restart" report
        # can be triaged by checking which directory actually got read.
        # Mismatched paths between Register-time write and startup-time
        # scan was the leading suspect for the persistence regressions.
        logger.info(
            "AIEffectPresetLibrary loaded %d preset(s) from %s",
            loaded,
            presets_dir,
        )
        self._ai_effect_preset_library = preset_library

        # ---- state machine ----
        state_machine = AIEffectController()
        self._ai_effect_controller = state_machine

        # ---- gateway registry ----
        # Empty at this point: ComfyUIGateway needs the ComfyUI plugin's
        # ExtendedComfyUIService which isn't materialised until the
        # plugin registry loads (happens later, after the main window's
        # initialize_async). :meth:`_wire_ai_effect_comfyui_gateway`
        # populates the registry from the plugin's own service instance.
        gateway_registry = GatewayRegistry()
        self._ai_effect_gateway_registry = gateway_registry
        # Expose to panels via host_api so the Filter Panel can fetch
        # service-cached object_info for runtime widget enrichment of
        # presets authored before backend constraints were captured.
        if self._host_api is not None:
            self._host_api.attach_ai_effect_gateway_registry(gateway_registry)

        # ---- generation controller ----
        # Bridge the AI Effect controller's GalleryStore protocol surface
        # to the host's GenerationHistoryStore so completed generations
        # land in the Gallery panel. Previously hardcoded to ``None``,
        # which silently swallowed every record (record_added never
        # fired, the panel stayed empty). The adapter is wrapped in a
        # ``getattr`` for ``_generation_history`` so an early init-order
        # regression that drops the store doesn't crash the AI subsystem
        # — the controller falls back to "no gallery" mode on its own.
        gallery_adapter: _AIEffectGalleryAdapter | None = None
        history_store = getattr(self, "_generation_history", None)
        if history_store is not None:
            gallery_adapter = _AIEffectGalleryAdapter(history_store)
        # Phase 8c-4: telemetry sink — emits one JSON line per
        # generation to user_data/logs/ai_effect.log when
        # developer.log_level == "debug". Otherwise a no-op instance
        # so the controller call site stays unconditional.
        from .plugins.ai_effect.telemetry import (  # noqa: PLC0415
            AIEffectTelemetry,
        )

        ai_effect_telemetry = AIEffectTelemetry.from_preferences(prefs)
        self._ai_effect_telemetry = ai_effect_telemetry

        # Phase 8c-2 follow-up: route the controller's per-preview
        # callback through the event bus so the FilterPanel (and any
        # future preview consumer) can subscribe without holding a
        # direct controller reference. The controller's throttle
        # guarantees ≤ 1 emit per ``ui.preview_throttle_ms`` window.
        host_bus = self._host_api.events

        def _emit_preview(layer_id: str, png_bytes: bytes) -> None:
            host_bus.emit(
                "ai_effect.preview",
                {"layer_id": layer_id, "preview_png": png_bytes},
            )

        self._ai_effect_preview_emitter = _emit_preview

        gen_controller = AIEffectGenerationController(
            state_machine=state_machine,
            preferences=prefs,
            gallery=gallery_adapter,
            telemetry=ai_effect_telemetry,
            on_preview=self._ai_effect_preview_emitter,
        )
        self._ai_effect_gen_controller = gen_controller

        # Phase 8d: panel-side surfaces (auto-connect failure, etc.)
        # do not have direct access to ``self.main_window``; they emit
        # a toast request on the bus and we route it through the same
        # status-bar surface as ``_notify_ai_effect_error``. This is a
        # fallback for environments where ``host.ui.get_main_window()``
        # has not been populated yet — the panel logs the path it
        # took, so a missing toast is diagnosable from the bus side
        # too.
        bus = self._host_api.events
        subscribe = getattr(bus, "on", None)
        if callable(subscribe):
            self._ai_effect_toast_handler = self._on_ai_effect_toast_event
            subscribe("ai_effect.toast", self._ai_effect_toast_handler)

        logger.info(
            "AI Effect subsystem initialized (gateway wiring deferred, " "gallery=%s)",
            "wired" if gallery_adapter is not None else "missing",
        )

    def _on_ai_effect_toast_event(self, payload: Any) -> None:
        """Bus subscriber: route ``ai_effect.toast`` to the status bar.

        Payload is a dict shaped ``{"title": str, "detail": str}``. We
        wrap it in a duck-typed object that ``_notify_ai_effect_error``
        treats the same as a real ``AIError`` so the message lands on
        the existing surface.
        """
        logger.info(
            "ai_effect.toast subscriber invoked (payload_type=%s, main_window=%s)",
            type(payload).__name__,
            "set" if getattr(self, "main_window", None) is not None else "None",
        )
        if not isinstance(payload, dict):
            logger.warning(
                "ai_effect.toast subscriber: payload not a dict (%r); dropped",
                type(payload).__name__,
            )
            return
        title = str(payload.get("title", "") or "")
        detail = str(payload.get("detail", "") or "")
        if not title and not detail:
            logger.warning("ai_effect.toast subscriber: empty title and detail; dropped")
            return

        class _ToastError:
            def __init__(self, t: str, d: str) -> None:
                self.title = t
                self.detail = d

        self._app._notify_ai_effect_error(_ToastError(title, detail))
        logger.info("ai_effect.toast subscriber: routed to _notify_ai_effect_error")

    def _wire_ai_effect_comfyui_gateway(self) -> None:
        """Register :class:`ComfyUIGateway` against the ComfyUI plugin service.

        Runs after the plugin registry has loaded so we can reach the
        ``ExtendedComfyUIService`` instance the ComfyUI UI panel plugin
        owns. Sharing that service means AI Effect generation goes
        through the same HTTP / WebSocket / privacy-consent path the
        ComfyUI panel uses (the old parallel ``AsyncComfyUIConnection``
        + ``ComfyUIBackend`` was retired in the de-duplication refactor).

        Silent skip paths keep startup non-fatal:
        - No gateway registry (subsystem init failed).
        - No ComfyUI UI plugin loaded (``loomstroke.plugins.comfyui.ui_panel_plugin``
          dropped from builtin_plugins / disabled by preferences).
        - Plugin has no ``.service`` attribute (unexpected shape).
        """
        registry = self._ai_effect_gateway_registry
        if registry is None or self.plugin_registry is None:
            return
        # Accept the handful of plausible class names the ComfyUI UI
        # plugin has used in git history — currently "ComfyUIPlugin"
        # but the broader match guards against renames without breaking
        # the gateway registration path.
        _COMFYUI_PLUGIN_CLASS_NAMES = frozenset({"ComfyUIPlugin", "ComfyUIUIPanelPlugin"})
        service: Any = None
        for plugin in self.plugin_registry.all():
            if plugin.__class__.__name__ not in _COMFYUI_PLUGIN_CLASS_NAMES:
                continue
            service = getattr(plugin, "service", None)
            if service is not None:
                break
        if service is None:
            logger.warning(
                "AI Effect: ComfyUI plugin service unavailable; "
                "ComfyUIGateway will not be registered"
            )
            return
        from .plugins.ai_effect.gateways.comfyui import ComfyUIGateway  # noqa: PLC0415
        from .plugins.comfyui.server_registry import (  # noqa: PLC0415
            get_registry as get_server_registry,
        )
        from .plugins.comfyui.server_registry import sync_registry_from_settings  # noqa: PLC0415

        # Phase 10c (2026-04-30): hand the ComfyUI server registry to
        # the gateway so per-preset URL resolution goes through cached
        # per-URL connections instead of mutating the panel-owned
        # service's ``_connection`` slot on every transition.
        #
        # Phase 10c-ui (2026-05-05): Preferences owns the ComfyUI URL
        # list. Seed / refresh the registry from Settings on startup so
        # the ComfyUI panel and AI Effect Editor select from the same
        # server ids and never carry independent endpoint text boxes.
        server_registry = get_server_registry()
        sync_registry_from_settings(self.settings, server_registry)

        gateway = ComfyUIGateway(service, display_name="ComfyUI", registry=server_registry)
        registry.register(gateway, overwrite=True)
        endpoint = getattr(getattr(service, "_connection", None), "base_url", "") or ""
        logger.info(
            "AI Effect: ComfyUIGateway registered via ComfyUI plugin service "
            "(endpoint=%s, registered_servers=%d)",
            endpoint,
            len(server_registry.list_all()),
        )

    def _wire_ai_effect_nanobanana_gateway(self) -> None:
        """Register :class:`NanoBananaGateway` against ``NanoBananaService``.

        Phase 8a-bridge follow-up. Mirrors :meth:`_wire_ai_effect_comfyui_gateway`
        in shape: silently skip when the AI Effect subsystem is absent,
        when the NanoBanana plugin failed to load, or when the plugin
        has an unexpected attribute surface. The Effect Editor then
        sees ``"nanobanana"`` alongside ``"comfyui"`` in its Gateway
        picker, and presets authored against either backend can run.
        """
        registry = self._ai_effect_gateway_registry
        if registry is None or self.plugin_registry is None:
            return
        # The NanoBanana service plugin registers under the class name
        # ``NanoBananaService``; accept the broader pattern guards
        # against rename without breaking the wiring path (mirrors the
        # ComfyUI wiring's tolerance).
        _NANOBANANA_SERVICE_CLASS_NAMES = frozenset({"NanoBananaService"})
        service: Any = None
        for plugin in self.plugin_registry.all():
            if plugin.__class__.__name__ in _NANOBANANA_SERVICE_CLASS_NAMES:
                service = plugin
                break
        if service is None:
            logger.warning(
                "AI Effect: NanoBanana service plugin unavailable; "
                "NanoBananaGateway will not be registered"
            )
            return
        from .plugins.ai_effect.gateways.nanobanana import NanoBananaGateway  # noqa: PLC0415

        gateway = NanoBananaGateway(service, display_name="NanoBanana")
        registry.register(gateway, overwrite=True)
        logger.info("AI Effect: NanoBananaGateway registered via NanoBanana plugin service")

    def _wire_ai_effect_gptimage2_gateway(self) -> None:
        """Register :class:`GPTImage2Gateway` against ``GPTImage2Service``.

        Mirrors the NanoBanana bridge: the standalone GPT Image 2 service
        owns credential lookup, privacy gating, and OpenAI transport. AI
        Effect only adds the preset/MCP orchestration layer on top.
        """
        registry = self._ai_effect_gateway_registry
        if registry is None or self.plugin_registry is None:
            return
        _GPTIMAGE2_SERVICE_CLASS_NAMES = frozenset({"GPTImage2Service"})
        service: Any = None
        for plugin in self.plugin_registry.all():
            if plugin.__class__.__name__ in _GPTIMAGE2_SERVICE_CLASS_NAMES:
                service = plugin
                break
        if service is None:
            logger.warning(
                "AI Effect: GPT Image 2 service plugin unavailable; "
                "GPTImage2Gateway will not be registered"
            )
            return
        from .plugins.ai_effect.gateways.gptimage2 import GPTImage2Gateway  # noqa: PLC0415

        gateway = GPTImage2Gateway(service, display_name="GPT Image 2")
        registry.register(gateway, overwrite=True)
        logger.info("AI Effect: GPTImage2Gateway registered via GPT Image 2 plugin service")

    def _ai_effect_register_target_dir(self) -> Any | None:
        from pathlib import Path as _PathReg  # noqa: PLC0415

        library = self._ai_effect_preset_library
        if library is None:
            return None
        user_dir = getattr(library, "_user_dir", None)
        return user_dir if isinstance(user_dir, _PathReg) else None

    def _can_host_ai_effect_manager_panel(self) -> bool:
        return isinstance(self.main_window, QMainWindow) and callable(
            getattr(self.main_window, "register_dynamic_panel", None)
        )

    def _register_ai_effect_manager_panel(self) -> Any | None:
        """Register the hidden AI Effects Manager dock with the main window.

        This mirrors built-in/plugin panels: the dock is registered
        once with the main window before layout restore and starts
        hidden; menu actions later only toggle visibility.
        """
        panel = getattr(self, "_ai_effect_manager_panel", None)
        if panel is not None:
            return panel
        if not self._can_host_ai_effect_manager_panel():
            return None

        from .plugins.ai_effect.ui.manager_panel import AIEffectManagerPanel  # noqa: PLC0415

        panel = AIEffectManagerPanel(
            library=self._ai_effect_preset_library,
            applier=self._app.apply_ai_effect_preset,
            gateway_registry=self._ai_effect_gateway_registry,
            register_target_dir=self._ai_effect_register_target_dir(),
            preset_exported_handler=self._app._on_effect_preset_exported,
            parent=self.main_window,
        )
        main_window = self.main_window
        registered = main_window.register_dynamic_panel(
            _AI_EFFECT_MANAGER_PANEL_ID,
            "AI Effects Manager",
            panel,
            area="right",
            default_visible=False,
            owner_plugin_id=_AI_EFFECT_MANAGER_PANEL_OWNER,
            default_size=(560, 520),
        )
        if not registered:
            logger.warning("AI Effects Manager panel registration failed")
            panel.deleteLater()
            return None
        self._ai_effect_manager_panel = panel
        self._ai_effect_preset_manager_window = panel
        return panel

    def _ensure_ai_effect_manager_panel(self) -> Any | None:
        """Compatibility wrapper for older launcher tests/fallback hosts."""
        return self._register_ai_effect_manager_panel()

    def _show_ai_effect_manager_panel(self, panel: Any) -> None:
        main_window = self.main_window
        dock = None
        if main_window is not None:
            get_panel_dock = getattr(main_window, "get_panel_dock", None)
            if callable(get_panel_dock):
                dock = get_panel_dock(_AI_EFFECT_MANAGER_PANEL_ID)
        if dock is not None:
            set_panel_visible = getattr(main_window, "set_panel_visible", None)
            if callable(set_panel_visible):
                set_panel_visible(_AI_EFFECT_MANAGER_PANEL_ID, True)
            else:
                dock.setVisible(True)
            dock.raise_()
        else:
            panel.show()
            panel.raise_()
        activate = getattr(panel, "activateWindow", None)
        if callable(activate):
            activate()

    def open_effect_editor(self) -> Any | None:
        """Launch the AI Effect preset editor window.

        Idempotent: if an editor window already exists and is visible,
        it is raised to the front instead of spawning a second instance.
        No-op (with a warning log) when the AI Effect subsystem failed
        to initialise — the menu entry is suppressed in that case, so
        this only fires if the opener was wired before init completed.
        """
        from .plugins.ai_effect.editor.window import EffectEditorWindow  # noqa: PLC0415

        if self._ai_effect_gateway_registry is None:
            logger.warning("Effect Editor launch ignored: AI Effect subsystem not initialised")
            return None

        panel = getattr(self, "_ai_effect_manager_panel", None)
        if panel is None:
            panel = self._register_ai_effect_manager_panel()
        if panel is not None:
            editor = panel.open_editor()
            if editor is not None:
                self._ai_effect_editor_window = editor
                self._app._show_ai_effect_manager_panel(panel)
                logger.info("Effect Editor tab opened")
                return editor
            return None

        existing = self._ai_effect_editor_window
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return existing

        editor = EffectEditorWindow(
            self._ai_effect_gateway_registry,
            parent=self.main_window,
            register_target_dir=self._ai_effect_register_target_dir(),
        )
        self._ai_effect_editor_window = editor
        # Phase 7-g: bridge the Editor's export signal into the preset
        # library so newly-authored presets show up in the Filter Panel
        # without requiring a restart. ``preset_exported`` is not a
        # hard contract — defensively guard so tests (which substitute
        # fake windows) do not need to declare the signal themselves.
        preset_exported = getattr(editor, "preset_exported", None)
        if preset_exported is not None and hasattr(preset_exported, "connect"):
            preset_exported.connect(self._app._on_effect_preset_exported)
        editor.show()
        logger.info("Effect Editor window opened")
        return editor

    def _on_effect_preset_exported(self, path: Any) -> None:
        """Sync a freshly-exported ``.lsfx`` into the preset library.

        Called from :meth:`EffectEditorWindow.preset_exported`. The
        export path itself is already authoritative on disk; our job is
        to surface it in the in-memory library so the Filter Panel sees
        it immediately. Library errors are downgraded to warnings so a
        sync failure never masks the fact that Export succeeded.
        """
        from pathlib import Path  # noqa: PLC0415

        from .plugins.ai_effect.lsfx_io import LsfxReadError  # noqa: PLC0415
        from .plugins.ai_effect.preset_library import DuplicatePresetError  # noqa: PLC0415

        library = self._ai_effect_preset_library
        if library is None:
            # Loud warning (not debug): a Register click that lands here
            # means the preset's .lsfx is on disk but the in-memory
            # library never knows about it. The user sees a successful
            # Register status but the Manager grid stays empty until
            # restart — and on restart, scan_and_load picks the file
            # up from disk (so it's not actually lost). If you see this
            # warning, the AI Effect subsystem failed to initialise.
            logger.warning(
                "Preset library not available; preset %s on disk but "
                "not registered in-memory until next restart",
                path,
            )
            return
        try:
            preset = library.import_lsfx(Path(path), overwrite=True)
        except (LsfxReadError, DuplicatePresetError, OSError) as exc:
            logger.warning("Could not sync exported preset %s into library: %s", path, exc)
            return
        except Exception:
            logger.exception("Unexpected failure syncing exported preset %s", path)
            return
        # Confirm the file survived the import path — the bak-rename
        # branch in ``library.import_lsfx`` can move an existing file
        # aside, so a quick exists() check catches the rare case where
        # the post-import state isn't what the user expects.
        try:
            on_disk = Path(path).exists()
        except OSError:
            on_disk = False
        logger.info(
            "Synced exported preset %s into library (file_on_disk=%s)",
            preset.preset_id,
            on_disk,
        )
        # If the Preset Manager window is open, poke it so the new /
        # updated card appears immediately. No-op when the window has
        # never been opened or was closed — refresh() on next showEvent
        # will catch up. ``getattr`` because test stand-ins may omit
        # the attribute entirely.
        manager = getattr(self, "_ai_effect_preset_manager_window", None)
        if manager is not None and hasattr(manager, "refresh"):
            try:
                manager.refresh()
            except Exception:
                logger.exception("PresetManager refresh after export failed")

    def open_effect_editor_with_preset(  # noqa: PLR0911 - guard chain is clearer than nesting
        self, preset_id: str
    ) -> None:
        """Open the Effect Editor pre-loaded with the given preset.

        Backend for the Preset Manager card's right-click "Edit..."
        action. Ensures an Editor window exists (raising the existing
        one when present, mirroring :meth:`open_effect_editor`), then
        hands off to :meth:`EffectEditorWindow.apply_imported_preset`
        with an :class:`ImportedPresetState` built from the library
        entry's ``.lsfx`` path.

        Silent no-ops on missing subsystems / builtins without an
        on-disk path / read errors — surfaces through the status bar
        rather than raising so a stray right-click never crashes the
        app.
        """
        library = self._ai_effect_preset_library
        registry = self._ai_effect_gateway_registry
        if library is None or registry is None:
            logger.warning(
                "open_effect_editor_with_preset: AI Effect subsystem not initialised "
                "(preset_id=%s)",
                preset_id,
            )
            return
        try:
            preset = library.get(preset_id)
        except Exception as exc:  # noqa: BLE001 — library.get raises PresetNotFoundError
            logger.warning(
                "open_effect_editor_with_preset: preset not found preset_id=%s err=%s",
                preset_id,
                exc,
            )
            return
        lsfx_path = getattr(preset, "lsfx_path", None)
        if lsfx_path is None:
            logger.warning(
                "open_effect_editor_with_preset: preset %s has no lsfx_path (builtin "
                "without user-dir copy?)",
                preset_id,
            )
            return
        # Ensure an Editor window is live, then load the preset into it.
        # Done in this order so a fresh launch + edit lands on a warm
        # window that has already wired its signals.
        self._app.open_effect_editor()
        editor = self._ai_effect_editor_window
        if editor is None:
            return
        from .plugins.ai_effect.editor.import_preset import (  # noqa: PLC0415
            ImportPresetError,
            load_preset_for_editing,
        )

        try:
            state = load_preset_for_editing(lsfx_path, registry)
        except ImportPresetError as exc:
            logger.warning(
                "open_effect_editor_with_preset: load_preset_for_editing failed " "for %s (%s): %s",
                preset_id,
                lsfx_path,
                exc,
            )
            return
        except Exception:
            logger.exception(
                "open_effect_editor_with_preset: unexpected failure loading %s",
                lsfx_path,
            )
            return
        try:
            editor.apply_imported_preset(state)
        except Exception:
            logger.exception(
                "open_effect_editor_with_preset: apply_imported_preset failed for %s",
                preset_id,
            )
            return
        logger.info(
            "Opened Effect Editor with preset %s loaded for re-edit",
            preset_id,
        )

    def open_preset_manager(self) -> None:
        """Show the docked AI Effects Manager panel.

        Idempotent: a visible existing panel is raised to the front.
        Reduced hosts that cannot register dock panels fall back to the
        legacy standalone window path used by older tests/tools.
        """
        from .plugins.ai_effect.ui.preset_manager_window import (  # noqa: PLC0415
            PresetManagerWindow,
        )

        if self._ai_effect_preset_library is None:
            logger.warning("Preset Manager launch ignored: AI Effect subsystem not initialised")
            return

        panel = getattr(self, "_ai_effect_manager_panel", None)
        if panel is None:
            panel = self._register_ai_effect_manager_panel()
        if panel is not None:
            panel.show_manager()
            self._app._show_ai_effect_manager_panel(panel)
            logger.info("Preset Manager panel opened")
            try:
                empty = len(self._ai_effect_preset_library) == 0
            except Exception:
                empty = False
            if empty:
                self._app._show_no_preset_dialog_safe(panel)
            return

        existing = self._ai_effect_preset_manager_window
        if existing is not None and existing.isVisible():
            existing.raise_()
            existing.activateWindow()
            return
        window = PresetManagerWindow(
            self._ai_effect_preset_library,
            self._app.apply_ai_effect_preset,
            editor_opener=self._app.open_effect_editor,
            editor_loader=self._app.open_effect_editor_with_preset,
            parent=self.main_window,
        )
        self._ai_effect_preset_manager_window = window
        window.show()
        logger.info("Preset Manager window opened")
        # Phase 6-g: empty library is a discoverable dead-end —
        # nudge the user toward Editor / Import without forcing them
        # to dig through the empty grid + sidebar.
        try:
            empty = len(self._ai_effect_preset_library) == 0
        except Exception:
            empty = False
        if empty:
            self._app._show_no_preset_dialog_safe(window)

    def _wire_ai_effect_preset_filter_menu(self) -> None:
        """Push the preset library + applier into the main window.

        Extracted from ``_create_main_window`` so the branch count of
        that async method stays inside the lint threshold (PLR0912).
        ``hasattr`` guards keep the hook safe for a main window built
        from an older codebase that has not yet exposed the setters.
        """
        if hasattr(self.main_window, "set_ai_effect_preset_library"):
            self.main_window.set_ai_effect_preset_library(self._ai_effect_preset_library)
        if hasattr(self.main_window, "set_ai_effect_preset_applier"):
            self.main_window.set_ai_effect_preset_applier(self._app.apply_ai_effect_preset)
        if hasattr(self.main_window, "set_ai_effect_preset_manager_opener"):
            self.main_window.set_ai_effect_preset_manager_opener(self._app.open_preset_manager)
        # Phase 6-f-add-ai-effect-menu: the same applier now also feeds
        # the LayerPanel's "Add AI Effect Layer" submenu so
        # authors can pick an installed preset without leaving the
        # layers panel footer.
        layer_panel = getattr(self.main_window, "layer_panel", None) or getattr(
            self.main_window, "_layer_panel", None
        )
        if layer_panel is not None and hasattr(layer_panel, "set_ai_effect_preset_applier"):
            library = self._ai_effect_preset_library
            layer_panel.set_ai_effect_preset_applier(
                self._app.apply_ai_effect_preset,
                library.list_all if library is not None else None,
            )

    def _wire_filter_panel_ai_signals(self) -> None:
        """Connect LayerPanel ⊘ + FilterPanel generate-requested signals.

        After the AIEffectPanel / PanelSelectionBinder retirement, the
        Effects Panel is the sole editor for AI Effect layers.  This
        method bridges:

        - ``LayerPanel.ai_effect_exclude_blend_toggled`` ->
          ``FilterPanel.apply_exclude_blend_toggle`` (slot ⊘ write-back).
        - ``FilterPanel.ai_generate_requested`` ->
          :meth:`_on_ai_effect_generate_requested` (runtime kickoff).

        Missing pieces (no LayerPanel, no FilterPanel plugin, no
        gen_controller) are tolerated silently so a reduced host or a
        failed AI Effect init does not crash startup.
        """
        if self.plugin_registry is None or self._canvas_service is None:
            return
        layer_panel = getattr(self.main_window, "layer_panel", None) or getattr(
            self.main_window, "_layer_panel", None
        )
        filter_panel = self._find_filter_panel_widget()
        if filter_panel is None:
            logger.debug("Effect panel AI signal wiring: panel unavailable, skipping")
            return

        # LayerPanel slot-row ⊘ click -> FilterPanel slot_refs write-back.
        if layer_panel is not None:
            exclude_signal = getattr(layer_panel, "ai_effect_exclude_blend_toggled", None)
            if exclude_signal is not None:
                exclude_signal.connect(filter_panel.apply_exclude_blend_toggle)
            # LayerPanel slot-row drag-drop bind -> FilterPanel slot ref write-back.
            # Lets the user drag any raster/vector layer onto an AI Effect
            # input slot row (Layers Panel) instead of having to pick from
            # the Effects Panel combo every time.
            slot_bind_signal = getattr(layer_panel, "ai_effect_slot_bind_dropped", None)
            if slot_bind_signal is not None:
                slot_bind_signal.connect(filter_panel.apply_slot_bind)
            # Phase 6-g: Layers-panel per-row Generate button funnels
            # through ``FilterPanel.request_generate`` so seed-mode
            # mutation runs (when the panel happens to be bound to
            # the same layer) before the controller is signalled. The
            # FilterPanel's own ``ai_generate_requested`` then routes
            # to the controller below — single seam, no duplicate
            # wiring to AIEffectGenerationController.
            row_generate_signal = getattr(layer_panel, "ai_effect_generate_requested", None)
            if row_generate_signal is not None and hasattr(filter_panel, "request_generate"):
                row_generate_signal.connect(filter_panel.request_generate)

        # FilterPanel ai_generate_requested -> AIEffectGenerationController.
        # Both the Layers Panel button and any future programmatic
        # generate calls converge here.
        generate_signal = getattr(filter_panel, "ai_generate_requested", None)
        if generate_signal is not None:
            generate_signal.connect(self._app._on_ai_effect_generate_requested)

        logger.info("Filter Panel AI signals wired (⊘ + Generate + row-Generate)")

    def _find_filter_panel_widget(self) -> Any:
        """Locate the FilterPanel widget via the plugin registry.

        Returns ``None`` when the plugin is not loaded or the widget
        has not yet been created (no dock has requested it).
        """
        if self.plugin_registry is None:
            return None
        for plugin in self.plugin_registry.all():
            if getattr(getattr(plugin, "metadata", None), "id", None) != "loomstroke.effect_panel":
                continue
            # FilterPanelPlugin stores the created widget on ``_panel``.
            widget = getattr(plugin, "_panel", None)
            if widget is not None:
                return widget
        return None

    _wire_effect_panel_ai_signals = _wire_filter_panel_ai_signals
    _find_effect_panel_widget = _find_filter_panel_widget

    def _on_ai_effect_generate_requested(self, layer_id: str) -> None:
        """Kick off generation for the layer behind FilterPanel's button.

        Flow (mirrors the retired PanelGenerationBinder):
        1. Resolve the current AI Effect layer + its state.
        2. Run ``HostResolveInputs(layer_id, state)`` to obtain
           ``(gateway, layer_versions, slot_images, source_page_id)``.
           This is where slot images get encoded as PNG bytes and the
           gateway is fetched from the registry — the previous single-
           positional ``execute(..., resolver)`` call was a bug; the
           controller needs the *resolved* tuple as keyword args.
        3. Schedule ``controller.execute(layer_id=..., state=...,
           gateway=..., layer_versions=..., slot_images=..., ...)`` on
           the qasync loop.

        Errors at any step are logged but do not crash the UI.
        """
        if (
            self._ai_effect_gen_controller is None
            or self._ai_effect_gateway_registry is None
            or self._canvas_service is None
        ):
            logger.warning("Generate ignored: AI Effect subsystem unavailable")
            return
        # Phase 6-g: surface "no gateway" upfront instead of letting
        # the resolver fail with an opaque ``UnknownGatewayError``.
        # ``list_registered`` returns the gateway_ids the registry
        # currently exposes; an empty list means the user hasn't
        # connected ComfyUI / NanoBanana yet.
        try:
            registered = self._ai_effect_gateway_registry.list_registered()
        except Exception:
            registered = []
        if not registered:
            logger.warning("Generate ignored: no AI gateway is registered yet")
            self._app._show_no_gateway_dialog_safe()
            return
        finder = getattr(self._canvas_service, "_find_layer_by_id_tree", None)
        if not callable(finder):
            logger.warning("Generate ignored: canvas has no layer finder")
            return
        layer = finder(layer_id)
        if layer is None or getattr(layer, "layer_type", "") != "ai_effect":
            logger.warning("Generate ignored: layer %s is not ai_effect", layer_id)
            return
        state = getattr(layer, "ai_effect", None)
        if state is None:
            logger.warning("Generate ignored: layer %s has no AIEffectState", layer_id)
            return
        from .plugins.ai_effect.resolve_inputs import HostResolveInputs  # noqa: PLC0415

        resolver = HostResolveInputs(
            canvas_service=self._canvas_service,
            gateway_registry=self._ai_effect_gateway_registry,
        )
        self._app._ai_effect_task_scheduler(
            self._app._run_ai_effect_generation(layer_id, state, resolver)
        )

    async def _mcp_submit_generation(self, arguments: Mapping[str, Any]) -> dict[str, Any]:
        """Run an AI Effect generation from the MCP job boundary."""

        from .mcp.types import McpStructuredError, ReasonCode  # noqa: PLC0415
        from .plugins.ai_effect.resolve_inputs import HostResolveInputs  # noqa: PLC0415

        layer_id, controller, registry, canvas, state = self._mcp_generation_context(arguments)
        state_backup = self._mcp_apply_generation_overrides(
            arguments,
            canvas=canvas,
            layer_id=layer_id,
            state=state,
        )
        if state_backup is not None or self._mcp_generation_has_state_update(arguments):
            layer = self._mcp_ai_effect_layer(canvas, layer_id)
            state = getattr(layer, "ai_effect", state)

        try:
            resolver = HostResolveInputs(canvas_service=canvas, gateway_registry=registry)
            try:
                gateway, layer_versions, slot_images, source_page_id = await resolver(
                    layer_id,
                    state,
                )
            except Exception as exc:
                raise McpStructuredError(
                    ReasonCode.BACKEND_FAILED,
                    "AI Effect input resolution failed.",
                    details={"layerId": layer_id, "error": exc.__class__.__name__},
                ) from exc

            force_regenerate = bool(arguments.get("forceRegenerate", False))
            try:
                outcome = await controller.execute(
                    layer_id=layer_id,
                    state=state,
                    gateway=gateway,
                    layer_versions=layer_versions,
                    slot_images=slot_images,
                    source_page_id=source_page_id,
                    force_regenerate=force_regenerate,
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                raise McpStructuredError(
                    ReasonCode.BACKEND_FAILED,
                    "AI Effect generation failed.",
                    details={"layerId": layer_id, "error": exc.__class__.__name__},
                ) from exc

            apply_to_canvas = bool(arguments.get("applyToCanvas", True))
            application = None
            if outcome.status == "success" and apply_to_canvas:
                try:
                    applied = self._app._apply_ai_effect_outputs(layer_id, state, outcome)
                    application = observed_output_application(applied, outcome)
                except Exception:
                    logger.exception("MCP AI Effect outputs application failed for %s", layer_id)
                    application = observed_output_application(False, outcome)
                if application.complete:
                    self._app._maybe_show_first_gen_banner()

            if outcome.error is not None:
                self._app._notify_ai_effect_error(outcome.error)

            payload = self._mcp_generation_payload(
                layer_id,
                outcome,
                apply_to_canvas=apply_to_canvas,
            )
            if application is not None:
                application.update_payload(payload)
            return payload
        finally:
            if state_backup is not None:
                try:
                    self._mcp_restore_generation_overrides(
                        canvas=canvas,
                        layer_id=layer_id,
                        state=state,
                        state_backup=state_backup,
                    )
                except Exception:
                    logger.exception(
                        "MCP AI Effect temporary override restore failed for %s",
                        layer_id,
                    )

    @staticmethod
    def _mcp_generation_has_state_update(arguments: Mapping[str, Any]) -> bool:
        from .mcp.ai_effect_state import has_ai_effect_state_update  # noqa: PLC0415

        return has_ai_effect_state_update(arguments)

    @staticmethod
    def _mcp_apply_generation_overrides(
        arguments: Mapping[str, Any],
        *,
        canvas: Any,
        layer_id: str,
        state: Any,
    ) -> Any:
        from .mcp.ai_effect_state import (  # noqa: PLC0415
            apply_ai_effect_state_update,
            has_ai_effect_state_update,
            make_ai_effect_state_backup,
            restore_ai_effect_state_backup,
        )

        if not has_ai_effect_state_update(arguments):
            return None
        persist_overrides = bool(arguments.get("persistOverrides", True))
        state_backup = None if persist_overrides else make_ai_effect_state_backup(state)
        try:
            apply_ai_effect_state_update(canvas, layer_id, arguments)
        except Exception:
            if state_backup is not None:
                restore_ai_effect_state_backup(canvas, layer_id, state, state_backup)
            raise
        return state_backup

    @staticmethod
    def _mcp_restore_generation_overrides(
        *,
        canvas: Any,
        layer_id: str,
        state: Any,
        state_backup: Any,
    ) -> None:
        from .mcp.ai_effect_state import restore_ai_effect_state_backup  # noqa: PLC0415

        restore_ai_effect_state_backup(canvas, layer_id, state, state_backup)

    def _mcp_generation_context(
        self,
        arguments: Mapping[str, Any],
    ) -> tuple[str, Any, Any, Any, Any]:
        from .mcp.types import McpStructuredError, ReasonCode  # noqa: PLC0415

        layer_id_value = arguments.get("layerId")
        if not isinstance(layer_id_value, str) or not layer_id_value.strip():
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "'layerId' is required.",
                details={"argument": "layerId"},
            )
        layer_id = layer_id_value.strip()
        controller = self._ai_effect_gen_controller
        registry = self._ai_effect_gateway_registry
        canvas = self._canvas_service
        if controller is None or registry is None or canvas is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "AI Effect subsystem is unavailable.",
                details={"controller": controller is not None, "canvas": canvas is not None},
            )
        self._ensure_mcp_generation_gateway(registry)
        layer = self._mcp_ai_effect_layer(canvas, layer_id)
        state = getattr(layer, "ai_effect", None)
        if state is None:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "AI Effect state is unavailable.",
                details={"layerId": layer_id},
            )
        return layer_id, controller, registry, canvas, state

    @staticmethod
    def _ensure_mcp_generation_gateway(registry: Any) -> None:
        from .mcp.types import McpStructuredError, ReasonCode  # noqa: PLC0415

        try:
            registered = registry.list_registered()
        except Exception:
            registered = []
        if not registered:
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "No AI gateway is registered.",
                details={"registeredGateways": []},
            )

    @staticmethod
    def _mcp_ai_effect_layer(canvas: Any, layer_id: str) -> Any:
        from .mcp.types import McpStructuredError, ReasonCode  # noqa: PLC0415

        finder = getattr(canvas, "_find_layer_by_id_tree", None)
        if not callable(finder):
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "Canvas layer lookup is unavailable.",
                details={"layerId": layer_id},
            )
        layer = finder(layer_id)
        if layer is None or getattr(layer, "layer_type", "") != "ai_effect":
            raise McpStructuredError(
                ReasonCode.NOT_FOUND,
                "AI Effect layer was not found.",
                details={"layerId": layer_id},
            )
        return layer

    def _mcp_cancel_generation(self, layer_id: str) -> bool:
        controller = self._ai_effect_gen_controller
        if controller is None:
            return False
        return bool(controller.cancel_layer(layer_id))

    @staticmethod
    def _mcp_generation_payload(
        layer_id: str,
        outcome: Any,
        *,
        apply_to_canvas: bool = True,
    ) -> dict[str, Any]:
        status = str(getattr(outcome, "status", "") or "error")
        job_status = "completed" if status in {"success", "cache_hit"} else status
        if job_status == "error":
            job_status = "failed"
        output_generated = status == "success"
        applied_to_canvas = bool(output_generated and apply_to_canvas)
        payload: dict[str, Any] = {
            "status": job_status,
            "aiEffectStatus": status,
            "layerId": layer_id,
            "paramHash": str(getattr(outcome, "param_hash", "") or ""),
            "retryCount": int(getattr(outcome, "retry_count", 0) or 0),
            "appliedToCanvas": applied_to_canvas,
            "canvasMutation": (
                "applied_outputs"
                if applied_to_canvas
                else ("asset_only" if output_generated else "none")
            ),
        }
        result = getattr(outcome, "result", None)
        if result is not None:
            payload["durationSec"] = float(getattr(result, "duration_sec", 0.0) or 0.0)
            assets: list[dict[str, Any]] = []
            for image in getattr(result, "output_images", []) or []:
                png_bytes = getattr(image, "png_bytes", None)
                if not isinstance(png_bytes, bytes | bytearray):
                    continue
                assets.append(
                    {
                        "internalId": str(getattr(image, "internal_id", "") or ""),
                        "pngBytes": bytes(png_bytes),
                        "dimensions": {
                            "width": int(getattr(image, "width", 0) or 0),
                            "height": int(getattr(image, "height", 0) or 0),
                        },
                    }
                )
            payload["assets"] = assets
        error = getattr(outcome, "error", None)
        if error is not None:
            error_class = getattr(error, "class_", None)
            payload["error"] = {
                "class": getattr(error_class, "name", str(error_class) if error_class else ""),
                "title": str(getattr(error, "title", "") or ""),
                "detail": str(getattr(error, "detail", "") or ""),
                "raw": getattr(error, "raw", None),
            }
        return payload

    async def _run_ai_effect_generation(self, layer_id: str, state: Any, resolver: Any) -> None:
        """Async wrapper that resolves inputs then drives the controller.

        Split from the synchronous ``_on_ai_effect_generate_requested``
        so both the resolver call (which is an ``async __call__``) and
        the controller's ``execute`` run inside one awaitable task.
        """
        controller = self._ai_effect_gen_controller
        if controller is None:
            return
        try:
            gateway, layer_versions, slot_images, source_page_id = await resolver(layer_id, state)
        except Exception:
            logger.exception("Generate dispatch: resolve_inputs failed for %s", layer_id)
            return
        try:
            outcome = await controller.execute(
                layer_id=layer_id,
                state=state,
                gateway=gateway,
                layer_versions=layer_versions,
                slot_images=slot_images,
                source_page_id=source_page_id,
            )
        except Exception:
            logger.exception("Generate dispatch: controller.execute raised for %s", layer_id)
            return
        status = getattr(outcome, "status", "?")
        # On success, apply the generated outputs onto the canvas. The
        # controller used to stop at GenerationRecord / Gallery updates
        # and never touched pixels — the Phase 2-6-2 TODO comment in
        # SimpleCanvasService flagged this gap ("initial generation is
        # expected to add the output layers through a different API").
        # Applied here because the controller deliberately has no
        # canvas reference (keeps the unit tests hermetic).
        if status == "success":
            try:
                self._app._apply_ai_effect_outputs(layer_id, state, outcome)
            except Exception:
                logger.exception("AI Effect outputs application failed for %s", layer_id)
            # Phase 6-g: first-generation banner. ``maybe_show_*`` is
            # gated on ``OnboardingState.onboarding.first_gen_done``
            # so it self-clamps to one-shot — no top-level guard
            # needed here.
            self._app._maybe_show_first_gen_banner()
        err = getattr(outcome, "error", None)
        if err is not None:
            # AIError carries class_ + title + detail + raw. Log all of
            # them so the root cause is never hidden behind a bare
            # "status=error" line (previous dogfood logs could not tell
            # whether it was validation / dependency / backend / etc.).
            err_class = getattr(err, "class_", None)
            err_class_name = getattr(err_class, "name", str(err_class) if err_class else "?")
            logger.error(
                "AI Effect generation finished for %s: status=%s class=%s"
                " title=%r detail=%r raw=%r",
                layer_id,
                status,
                err_class_name,
                getattr(err, "title", ""),
                getattr(err, "detail", ""),
                getattr(err, "raw", None),
            )
            # Phase 6-g: surface the failure to the user instead of
            # only logging. Status-bar message keeps it non-modal so
            # rapid auto-retries / on_change runs don't pile up modal
            # dialogs.
            self._app._notify_ai_effect_error(err)
        else:
            logger.info("AI Effect generation finished for %s: status=%s", layer_id, status)

        # Diagnostic: surface how many slots were resolved and whether
        # the gateway we handed off to is actually registered by the
        # expected id. Helps distinguish "required slot empty" from
        # "gateway not wired" without a second log call.
        logger.debug(
            "AI Effect generation inputs for %s: gateway=%s slots=%d versions=%d",
            layer_id,
            getattr(gateway, "gateway_id", "?"),
            len(slot_images or {}),
            len(layer_versions or {}),
        )

    def _show_no_preset_dialog_safe(self, parent: Any) -> None:
        """Best-effort: surface "no presets" tip when Manager opens
        empty. Soft-imports so a stripped distribution without the
        onboarding helper module degrades to a silent no-op."""
        try:
            from loomstroke.plugins.ai_effect.ui.onboarding_dialogs import (  # noqa: PLC0415
                show_no_preset_dialog,
            )
        except Exception:
            return
        library = self._ai_effect_preset_library
        user_dir = getattr(library, "_user_dir", None) if library is not None else None
        if user_dir is None:
            return
        try:
            show_no_preset_dialog(user_dir, parent=parent)
        except Exception:
            logger.exception("show_no_preset_dialog failed")

    def _show_no_gateway_dialog_safe(self) -> None:
        """Surface "no AI gateway" tip when the user attempts to
        generate before any backend is registered. The first
        symptom users hit was an opaque "Unknown gateway" error in
        the log — this dialog explains the fix without requiring
        log access."""
        try:
            from loomstroke.plugins.ai_effect.ui.onboarding_dialogs import (  # noqa: PLC0415
                show_no_gateway_dialog,
            )
        except Exception:
            return
        try:
            show_no_gateway_dialog(parent=self.main_window)
        except Exception:
            logger.exception("show_no_gateway_dialog failed")

    def _maybe_show_first_gen_banner(self) -> None:
        """One-shot congratulations banner after the first successful
        generation. Gated by ``OnboardingState.onboarding.first_gen_done``
        + the ``ui.show_first_gen_banner`` preference (off → never).
        """
        state = self._ai_effect_state
        prefs = self._ai_effect_preferences
        if state is None:
            return
        # Preference opt-out beats the state flag — power users can
        # turn the banner off without touching state.json.
        ui_prefs = getattr(prefs, "ui", None) if prefs is not None else None
        if ui_prefs is not None and not bool(getattr(ui_prefs, "show_first_gen_banner", True)):
            return
        try:
            from loomstroke.plugins.ai_effect.ui.onboarding_dialogs import (  # noqa: PLC0415
                maybe_show_first_gen_banner,
            )
        except Exception:
            logger.debug("first-gen banner module not importable", exc_info=True)
            return

        def _save(s: Any) -> None:
            path = self._ai_effect_state_path
            if path is None:
                return
            try:
                s.save(path)
            except Exception:
                logger.debug("OnboardingState save failed", exc_info=True)

        try:
            maybe_show_first_gen_banner(state, _save, parent=self.main_window)
        except Exception:
            logger.exception("first-gen banner failed")

    def _notify_ai_effect_error(self, err: Any) -> None:
        """Surface an AIError to the user via the main-window status
        bar. Non-modal on purpose — auto-fire triggers (on_change /
        debounced) can produce errors quickly and stacking modal
        dialogs would block the UI."""
        title = str(getattr(err, "title", "") or "Generation failed")
        detail = str(getattr(err, "detail", "") or "")
        message = f"{title}: {detail}" if detail else title
        main_window = getattr(self, "main_window", None)
        if main_window is None:
            logger.warning("_notify_ai_effect_error: main_window is None; dropped")
            return
        status = getattr(main_window, "statusBar", None)
        if not callable(status):
            logger.warning("_notify_ai_effect_error: main_window has no statusBar(); dropped")
            return
        try:
            status().showMessage(f"AI Effect: {message}", 8000)
            logger.info(
                "_notify_ai_effect_error: showMessage called (len=%d, msg=%r)",
                len(message),
                f"AI Effect: {message}"[:80],
            )
        except Exception:
            logger.warning("Status bar message failed", exc_info=True)

    def _apply_ai_effect_outputs(  # noqa: PLR0912, PLR0915 - output dispatch guard chain
        self, layer_id: str, state: Any, outcome: Any
    ) -> AIEffectOutputApplication:
        """Pipe generated output images onto the canvas.

        Dispatches each ``OutputImage`` to ``set_ai_effect_layer_image``
        (mode ``"self"``) or ``create_ai_effect_output_layer`` (mode
        ``"new_layer_above"``) based on the manifest entry the author
        declared in the Effect Editor.

        Failure modes handled quietly:
        - Missing canvas service -> nothing to apply, warn + return.
        - ``result`` / ``output_images`` absent -> nothing to do.
        - Manifest missing the output -> default to ``"self"`` so a
          mis-authored preset still produces visible output.
        - Canvas call returning False / None -> log and continue so one
          bad output does not block subsequent ones.
        """
        result = getattr(outcome, "result", None)
        images = getattr(result, "output_images", None) or []
        application = AIEffectOutputApplication(expected_count=len(images))
        canvas = self._canvas_service
        if canvas is None:
            logger.warning("Apply AI outputs: canvas service unavailable")
            application.failed_count = len(images)
            return application
        models = getattr(result, "output_models", None) or []
        if not images:
            if models:
                logger.debug(
                    "Apply AI outputs: outcome carried %d 3D model output(s); "
                    "ComfyUI gateway imported them directly",
                    len(models),
                )
                return application
            logger.debug("Apply AI outputs: outcome carried no output images")
            return application

        snapshot = getattr(state, "preset_snapshot", {}) or {}
        manifest = snapshot.get("manifest", {}) if isinstance(snapshot, dict) else {}
        raw_outputs = manifest.get("outputs", []) if isinstance(manifest, dict) else []
        modes: dict[str, str] = {}
        display_names: dict[str, str] = {}
        for entry in raw_outputs:
            if not isinstance(entry, dict):
                continue
            internal_id = entry.get("internal_id")
            if not isinstance(internal_id, str) or not internal_id:
                continue
            mode = entry.get("mode")
            modes[internal_id] = mode if mode in ("self", "new_layer_above") else "self"
            name = entry.get("display_name")
            if isinstance(name, str) and name:
                display_names[internal_id] = name
        # A self override uses one image on this layer and materializes
        # overflow as sibling layers; new_layer_above redirects all images.
        override = getattr(state, "output_mode_override", None)
        if isinstance(override, str) and override in ("self", "new_layer_above"):
            for internal_id in modes:
                modes[internal_id] = override

        last_result = getattr(state, "last_result", None)
        generation_index = len(getattr(last_result, "output_layer_ids", []) or [])
        created_layer_ids: list[str] = []
        self_output_applied = False
        output_names = output_layer_names(canvas, layer_id, state=state)
        for img in images:
            internal_id = getattr(img, "internal_id", "") or ""
            png_bytes = getattr(img, "png_bytes", None)
            if not isinstance(png_bytes, (bytes, bytearray)) or not png_bytes:
                application.failed_count += 1
                continue
            # Override wins even for runtime-detected outputs that the
            # manifest never enumerated — otherwise an "output to self"
            # toggle would silently fall through to the default for any
            # extra output the workflow produces.
            if isinstance(override, str) and override in ("self", "new_layer_above"):
                mode = override
            else:
                mode = modes.get(internal_id, "self")
            if override == "self" and self_output_applied:
                mode = "new_layer_above"
            if mode == "self":
                ok = application.attempt(
                    canvas.set_ai_effect_layer_image, layer_id, bytes(png_bytes)
                )
                self_output_applied = self_output_applied or bool(ok)
                if not ok:
                    logger.warning(
                        "Apply AI outputs: set_ai_effect_layer_image returned False "
                        "for %s (slot=%s)",
                        layer_id,
                        internal_id,
                    )
            else:  # "new_layer_above"
                display_name = (
                    output_names(internal_id)
                    if output_names is not None
                    else display_names.get(internal_id, internal_id or "AI Output")
                )
                new_id = application.attempt(
                    canvas.create_ai_effect_output_layer,
                    layer_id,
                    name=display_name,
                    png_bytes=bytes(png_bytes),
                    output_internal_id=internal_id or None,
                    generation_index=generation_index,
                )
                if new_id:
                    created_layer_ids.append(new_id)
                else:
                    logger.warning(
                        "Apply AI outputs: create_ai_effect_output_layer returned None "
                        "for parent %s (slot=%s)",
                        layer_id,
                        internal_id,
                    )
        # Record the freshly-minted output layer ids on the state so
        # later Undo can tombstone + restore them (matches the
        # ``GenerateAIEffectCommand.new_output_layer_ids`` contract).
        if created_layer_ids and last_result is not None:
            existing = list(getattr(last_result, "output_layer_ids", []) or [])
            existing.extend(created_layer_ids)
            last_result.output_layer_ids = existing
        return application

    def _ai_effect_task_scheduler(self, coro: Any) -> Any:
        """Hand a coroutine to the qasync event loop (Phase 6-d'-2).

        ``PanelGenerationBinder`` expects a scheduler callable; we give
        it ``asyncio.ensure_future`` with a success / failure logger
        hung off the resulting task so rejections (e.g. loop closed)
        surface in the log instead of vanishing into the future.
        """
        try:
            task = asyncio.ensure_future(coro)
        except RuntimeError:
            # No running loop — should not happen in the qasync app,
            # but the Editor-less test harness can reach here.
            logger.exception("AI Effect task scheduling failed (no running loop?)")
            return None

        def _done(t: asyncio.Task[Any]) -> None:
            exc = t.exception() if not t.cancelled() else None
            if exc is not None:
                logger.exception(
                    "AI Effect task raised", exc_info=(type(exc), exc, exc.__traceback__)
                )

        task.add_done_callback(_done)
        return task

    def apply_ai_effect_preset(self, preset_id: str) -> str | None:
        """Instantiate an AI Effect Layer bound to ``preset_id``.

        Phase 7-h-A entry point for the Filter menu "Installed AI
        Presets" submenu. Looks up the preset in the library, builds a
        fresh ``AIEffectState`` via ``AIEffectState.from_preset``, and
        hands it to the canvas service's
        :meth:`add_ai_effect_layer` factory.

        Returns the newly-created layer id on success, or ``None`` when
        the subsystem is not available, the preset id is unknown, or the
        canvas refuses the insertion. All failure paths log but do not
        raise so a menu click never crashes the main window.
        """
        library = self._ai_effect_preset_library
        if library is None:
            logger.warning(
                "apply_ai_effect_preset: AI Effect subsystem not initialised (preset_id=%s)",
                preset_id,
            )
            return None
        canvas_service = getattr(self, "_canvas_service", None)
        if canvas_service is None or not hasattr(canvas_service, "add_ai_effect_layer"):
            logger.warning(
                "apply_ai_effect_preset: canvas service unavailable (preset_id=%s)", preset_id
            )
            return None
        try:
            preset = library.get(preset_id)
        except Exception as exc:  # noqa: BLE001 — library.get raises PresetNotFoundError
            logger.warning(
                "apply_ai_effect_preset: preset not found preset_id=%s err=%s", preset_id, exc
            )
            return None
        from .plugins.ai_effect.data_model import AIEffectState  # noqa: PLC0415

        state = AIEffectState.from_preset(preset)
        # UX symmetry with the output path: generation auto-creates an
        # output layer above the AI Effect layer, so users expect the
        # input side to come pre-wired to something sensible rather than
        # forcing them to hunt for the Filter Panel combo before they can
        # even see a result. Default: bind the first ``input_slots`` entry
        # to the layer that was active at invocation time (mirrors how
        # most image editors apply filters to "the current layer").
        # Silent no-op when there is no bindable active layer — the
        # combo stays "(unbound)" and the user binds manually as before.
        # Call via class so the method stays reachable when the applier is
        # bound to a SimpleNamespace stand-in (test_filter_menu_preset_applier
        # pattern) that has no app-level attributes.
        AIEffectCoordinator._autobind_active_layer_to_first_slot(canvas_service, state, preset)
        from .core.comic_panels.tool_context import panel_tool_context  # noqa: PLC0415

        context = panel_tool_context(canvas_service, getattr(canvas_service, "active_layer", None))
        placement = {"parent_group_id": context.parent_id} if context is not None else {}
        layer_id = canvas_service.add_ai_effect_layer(state, name=preset.name or None, **placement)
        if layer_id is None:
            logger.warning(
                "apply_ai_effect_preset: canvas refused insertion for preset_id=%s", preset_id
            )
            return None
        # Activate the new layer so the FilterPanel switches to its
        # parameter form on apply. Without this the active layer stays
        # on whatever the user had selected when they clicked the
        # Filter menu — the panel's "_selected_layer_id" tracks the
        # active layer via ``layer.selected`` events, so an unselected
        # AI Effect layer surfaces as ``ai_id=None`` and the UI never
        # leaves placeholder state. Best-effort: silently skip when
        # the canvas service does not expose ``set_active_layer`` so a
        # leaner test stand-in does not break preset application.
        set_active = getattr(canvas_service, "set_active_layer", None)
        if callable(set_active):
            try:
                set_active(layer_id)
            except Exception:  # noqa: BLE001 — never block apply on activation
                logger.debug(
                    "apply_ai_effect_preset: set_active_layer failed (non-fatal)",
                    exc_info=True,
                )
        logger.info("Applied AI effect preset %s as layer %s", preset_id, layer_id)
        return cast(str | None, layer_id)

    @staticmethod
    def _autobind_active_layer_to_first_slot(  # noqa: PLR0911 - guard-chain early-returns are clearer than nesting
        canvas_service: Any, state: Any, preset: Any
    ) -> None:
        """Pre-populate ``state.slot_refs`` with the active layer.

        Targets the first ``input_slots`` entry declared by the preset's
        manifest. Skips silently when:

        - the preset declares no input slots
        - the canvas has no active layer
        - the active layer is an ai_effect / group / mask (not composable
          as a raster source — mirrors the filter in
          :meth:`FilterPanel._collect_source_layer_candidates`)

        Does **not** overwrite an existing slot_ref so a future
        ``.lstroke`` restore path that already carried wiring is left
        alone.
        """
        # Local import so the method doesn't rely on module-level import
        # order and so a ``NameError`` here can't get silently swallowed
        # by the surrounding try/except (which is what hid a missing
        # ``SlotRef`` symbol during first implementation).
        from .plugins.ai_effect.data_model import SlotRef  # noqa: PLC0415

        try:
            manifest = getattr(preset, "manifest", {}) or {}
            slots_raw = manifest.get("input_slots", []) if isinstance(manifest, dict) else []
            if not isinstance(slots_raw, list) or not slots_raw:
                return
            first_slot = slots_raw[0]
            if not isinstance(first_slot, dict):
                return
            internal_id = first_slot.get("internal_id")
            if not isinstance(internal_id, str) or not internal_id:
                return
            active = getattr(canvas_service, "active_layer", None)
            if active is None:
                return
            active_type = str(getattr(active, "layer_type", "raster"))
            if active_type not in ("raster", "vector"):
                return
            active_id = getattr(active, "id", None)
            if not isinstance(active_id, str) or not active_id:
                return
            existing = state.slot_refs.get(internal_id) if state.slot_refs else None
            if existing is not None:
                return
            state.slot_refs[internal_id] = SlotRef(layer_id=active_id, exclude_blend=False)
            logger.info(
                "apply_ai_effect_preset: auto-bound active layer %s to slot %r",
                active_id,
                internal_id,
            )
        except Exception:  # noqa: BLE001 — never block preset apply on a binding failure
            logger.debug("auto-bind failed (non-fatal)", exc_info=True)
