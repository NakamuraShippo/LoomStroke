"""Settings side-effect coordinator for :mod:`loomstroke.ui.main_window`."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from PySide6.QtWidgets import QApplication

from ..platform.settings_manager import SettingsManager
from ..platform.settings_schema import (
    MCP_CONFIRMATION_POLICY_KEY,
    MCP_LIVE_HTTP_ALLOWED_ORIGINS_KEY,
    MCP_LIVE_HTTP_ENABLED_KEY,
    MCP_LIVE_HTTP_PORT_KEY,
    MCP_LIVE_HTTP_ROOTS_KEY,
    MCP_LIVE_HTTP_SESSION_TOKEN_KEY,
    MCP_LIVE_HTTP_START_ON_LAUNCH_KEY,
)
from .canvas.render_scene import (
    set_direct_vector_rendering_enabled,
    set_view_resolution_compositing_enabled,
)
from .theme import apply_loomstroke_theme

logger = logging.getLogger(__name__)
_SENSITIVE_SETTING_KEY_PARTS = frozenset(
    {
        "api_key",
        "apikey",
        "auth_token",
        "credential",
        "password",
        "secret",
        "session_token",
        "token",
    }
)

_MCP_RUNTIME_SETTING_KEYS = frozenset(
    {
        MCP_LIVE_HTTP_ALLOWED_ORIGINS_KEY,
        MCP_LIVE_HTTP_ENABLED_KEY,
        MCP_LIVE_HTTP_PORT_KEY,
        MCP_LIVE_HTTP_ROOTS_KEY,
        MCP_LIVE_HTTP_SESSION_TOKEN_KEY,
        MCP_LIVE_HTTP_START_ON_LAUNCH_KEY,
        MCP_CONFIRMATION_POLICY_KEY,
    }
)


class MainWindowSettingsApplier:
    """Apply runtime effects for settings while keeping MainWindow as facade."""

    def __init__(self, window: Any, settings: SettingsManager) -> None:
        self._window = window
        self._settings = settings

    def apply_preference_changes(self, changes: dict[str, Any]) -> None:
        for key, value in changes.items():
            current = self._settings.get(key)
            if current == value:
                continue
            try:
                self._settings.set(key, value)
            except Exception as exc:
                logger.error("Failed to update setting %s: %s", key, exc)
                continue
            self.handle_setting_side_effects(key, value, source="preferences")

    def handle_setting_side_effects(
        self,
        key: str,
        value: Any,
        *,
        source: str | None = None,
    ) -> None:
        """Apply immediate effects for preference changes."""
        logger.debug("Preference changed: %s=%s", key, _redact_setting_value(key, value))

        self._persist_host_setting(key, value)

        if key.startswith("canvas/"):
            self.apply_canvas_setting(key, value)
            return

        if key in _MCP_RUNTIME_SETTING_KEYS:
            self.apply_mcp_runtime_setting(key, value)
            return

        handlers: dict[str, Callable[[Any], None]] = {
            "view/grid_size": self._apply_grid_size,
            "ui/language": self._apply_language,
            "ui/theme": self.apply_theme_setting,
            "performance/recompose_fps": self.apply_recompose_fps,
            "performance/vector_overlay_enabled": self.apply_vector_overlay_enabled,
            "performance/vector_overlay_max_dim": self.apply_vector_overlay_max_dim,
            "performance/vector_overlay_debug_timing": self.apply_vector_overlay_debug_timing,
            "performance/direct_vector_rendering_enabled": (
                self.apply_direct_vector_rendering_enabled
            ),
            "performance/view_resolution_compositing_enabled": (
                self.apply_view_resolution_compositing_enabled
            ),
            "performance/spread_inactive_page_quality": (self.apply_spread_inactive_page_quality),
        }
        handler = handlers.get(key)
        if handler is not None:
            handler(value)
            return

        if key.startswith("ui/menus/"):
            self._apply_menu_setting(key, value, source)

    def apply_mcp_runtime_setting(self, key: str, value: Any) -> None:
        handler = getattr(self._window, "_handle_mcp_runtime_setting_changed", None)
        if callable(handler):
            handler(key, value)

    def _persist_host_setting(self, key: str, value: Any) -> None:
        host_api = getattr(self._window, "_host_api", None)
        if not host_api:
            return
        settings_service = getattr(host_api, "settings", None)
        if not settings_service or not hasattr(settings_service, "set"):
            return
        try:
            settings_service.set(key, value)
        except Exception as exc:
            logger.error("Failed to persist setting %s: %s", key, exc)

    def _apply_grid_size(self, value: Any) -> None:
        set_canvas_grid_size = getattr(self._window, "_set_canvas_grid_size", None)
        if callable(set_canvas_grid_size):
            set_canvas_grid_size(value)

    def _apply_language(self, value: Any) -> None:
        lang_value = value if isinstance(value, str) else "system"
        set_language = getattr(self._window, "_set_language", None)
        if callable(set_language):
            set_language(lang_value)

    def _apply_menu_setting(self, key: str, value: Any, source: str | None) -> None:
        menu_id = self.menu_id_from_setting(key)
        if not menu_id:
            return
        emit_menu_visibility_changed = getattr(
            self._window,
            "_emit_menu_visibility_changed",
            None,
        )
        if callable(emit_menu_visibility_changed):
            emit_menu_visibility_changed(menu_id, bool(value), source)
        if hasattr(self._window, "_menu_handles"):
            build_menubar = getattr(self._window, "_build_menubar", None)
            if callable(build_menubar):
                build_menubar()

    @staticmethod
    def menu_id_from_setting(key: str) -> str | None:
        parts = key.split("/")
        if len(parts) == 4 and parts[0] == "ui" and parts[1] == "menus" and parts[-1] == "enabled":
            return parts[2]
        return None

    def apply_recompose_fps(self, value: Any) -> None:
        """Apply recompose FPS limit to canvas service."""
        try:
            fps = max(1, int(value))
        except (TypeError, ValueError):
            return
        canvas = getattr(getattr(self._window, "_host_api", None), "canvas", None)
        if canvas and hasattr(canvas, "_recompose_min_interval_s"):
            canvas._recompose_min_interval_s = 1.0 / float(fps)
            logger.info("Recompose FPS limit set to %d", fps)

    def apply_vector_overlay_enabled(self, value: Any) -> None:
        """SSO Phase 1: CanvasWidget の overlay 有効/無効を切り替える."""
        canvas_widget = getattr(self._window, "canvas", None)
        if canvas_widget is None or not hasattr(canvas_widget, "set_overlay_enabled"):
            return
        canvas_widget.set_overlay_enabled(bool(value))
        logger.info("Vector overlay (SSO) enabled=%s", bool(value))

    def apply_vector_overlay_max_dim(self, value: Any) -> None:
        """SSO Phase 1: effective_dpr cap 値を切り替える (2048/4096/8192)."""
        canvas_widget = getattr(self._window, "canvas", None)
        if canvas_widget is None or not hasattr(canvas_widget, "set_overlay_max_dim"):
            return
        try:
            max_dim = int(value)
        except (TypeError, ValueError):
            return
        canvas_widget.set_overlay_max_dim(max_dim)
        logger.info("Vector overlay max_dim set to %d", max_dim)

    def apply_direct_vector_rendering_enabled(self, value: Any) -> None:
        """Direct WGPU vector rendering の有効/無効を切り替える."""
        set_direct_vector_rendering_enabled(bool(value))
        canvas = getattr(getattr(self._window, "_host_api", None), "canvas", None)
        recompose = getattr(canvas, "_recompose", None)
        if callable(recompose):
            try:
                recompose()
            except Exception:
                logger.debug("direct vector rendering recompose failed", exc_info=True)
        logger.info("Direct vector rendering enabled=%s", bool(value))

    def apply_view_resolution_compositing_enabled(self, value: Any) -> None:
        """View 解像度合成の有効/無効を切り替える."""
        set_view_resolution_compositing_enabled(bool(value))
        canvas = getattr(getattr(self._window, "_host_api", None), "canvas", None)
        recompose = getattr(canvas, "_recompose", None)
        if callable(recompose):
            try:
                recompose()
            except Exception:
                logger.debug("view compositing recompose failed", exc_info=True)
        logger.info("View-resolution compositing enabled=%s", bool(value))

    def apply_spread_inactive_page_quality(self, value: Any) -> None:
        """見開きの相手ページの合成品質を切り替える (CT-06 B4-c5).

        controller は再構築のたびに設定を読み直すので、ここでは無効化して
        再描画を促すだけでよい。次に present するフレームで新しい係数の
        composite が作られる。
        """
        controller = getattr(self._window, "_spread_view_controller", None)
        invalidate = getattr(controller, "invalidate", None)
        if callable(invalidate):
            invalidate()
        canvas_widget = getattr(self._window, "canvas", None)
        refresh = getattr(canvas_widget, "request_native_surface_refresh", None)
        if callable(refresh):
            try:
                refresh(force_full_recompose=True)
            except Exception:
                logger.debug("spread quality refresh failed", exc_info=True)
        logger.info("Spread inactive page quality=%s", value)

    def apply_vector_overlay_debug_timing(self, value: Any) -> None:
        """SSO Phase 1b: PD-3 計測の ON/OFF (Q-3 実測用)."""
        canvas_widget = getattr(self._window, "canvas", None)
        if canvas_widget is None or not hasattr(canvas_widget, "set_overlay_debug_timing"):
            return
        canvas_widget.set_overlay_debug_timing(bool(value))
        logger.info("Vector overlay debug_timing=%s", bool(value))

    def apply_theme_setting(self, value: Any) -> None:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        theme = value if isinstance(value, str) else "dark"

        apply_loomstroke_theme(app, theme)
        startpage = getattr(self._window, "_startpage_panel", None)
        refresh_theme_assets = getattr(startpage, "refresh_theme_assets", None)
        if callable(refresh_theme_assets):
            refresh_theme_assets()

    def apply_canvas_setting(self, key: str, value: Any) -> None:
        """Apply canvas-specific settings to the active canvas service."""
        host_api = getattr(self._window, "_host_api", None)
        if not host_api:
            return

        canvas = getattr(host_api, "canvas", None)
        if not canvas or not hasattr(canvas, "update_canvas_config"):
            return

        config: dict[str, int] = {}
        try:
            if key == "canvas/width":
                config["width"] = int(value)
            elif key == "canvas/height":
                config["height"] = int(value)
            elif key == "canvas/dpi":
                config["dpi"] = int(value)
            elif key == "canvas/tile_size":
                config["tile_size"] = int(value)
            elif key == "canvas/max_layers":
                config["max_layers"] = int(value)
            else:
                return
        except (TypeError, ValueError):
            logger.warning("Ignoring invalid canvas setting %s=%r", key, value)
            return

        try:
            canvas.update_canvas_config(**config)
        except Exception as exc:
            logger.error("Failed to apply canvas setting %s=%r: %s", key, value, exc)


def _redact_setting_value(key: str, value: Any) -> Any:
    normalized = key.lower().replace("-", "_")
    if any(part in normalized for part in _SENSITIVE_SETTING_KEY_PARTS):
        return "<redacted>" if value else ""
    return value
