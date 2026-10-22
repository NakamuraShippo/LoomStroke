"""Optional AI wiring around MainWindow's layout restore and panel bootstrap."""

from __future__ import annotations

from typing import Any

from .platform.launch_options import ai_features_enabled


def attach_generation_handlers(app: Any) -> None:
    """Attach generation ports before the UI service receives MainWindow."""
    if not ai_features_enabled():
        return
    app.main_window.set_mcp_generation_handlers(
        app._mcp_submit_generation, app._mcp_cancel_generation
    )
    app._host_api.attach_ai_effect_generation_submitter(app._privacy_gated_generation_submitter)


def register_manager_before_restore(app: Any) -> None:
    """Register the manager dock before saved layout restore can run."""
    if not ai_features_enabled():
        return
    if app._ai_effect_preset_library is not None:
        app._wire_ai_effect_preset_filter_menu()
        app._register_ai_effect_manager_panel()


def connect_after_initialize(app: Any) -> None:
    """Connect materialized panel signals and register gateways before show."""
    if not ai_features_enabled():
        return
    wire_filter_panel = getattr(app, "_wire_filter_panel_ai_signals", None)
    if not callable(wire_filter_panel):
        wire_filter_panel = app._wire_effect_panel_ai_signals
    wire_filter_panel()
    app._wire_ai_effect_comfyui_gateway()
    app._wire_ai_effect_nanobanana_gateway()
    app._wire_ai_effect_gptimage2_gateway()


def schedule_local_runtime(app: Any) -> None:
    """Schedule the optional ComfyUI runtime only after the window is shown."""
    if ai_features_enabled():
        from .plugins.comfyui.local_runtime import schedule  # noqa: PLC0415

        schedule(app)
