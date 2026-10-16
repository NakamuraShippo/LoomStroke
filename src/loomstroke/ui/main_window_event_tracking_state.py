"""Event-tracking runtime state initialization for MainWindow."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from contextlib import suppress
from pathlib import Path
from typing import Any

from ..platform.user_dirs import default_user_root
from .menu_catalog import MENU_DEFINITIONS

UserRootFactory = Callable[..., Path]


class MainWindowEventTrackingStateCoordinator:
    """Initialize MainWindow event-tracking containers and audit paths."""

    def __init__(
        self,
        window: Any,
        *,
        telemetry_dir: Path | None = None,
        user_root_factory: UserRootFactory = default_user_root,
    ) -> None:
        self._window = window
        self._telemetry_dir = telemetry_dir
        self._user_root_factory = user_root_factory

    def initialize(self) -> None:
        window = self._window
        window._layer_event_handlers = []
        window._layer_event_tokens = []
        window._layer_event_bus_ids = set()
        window._history_event_handlers = []
        window._history_event_tokens = []
        window._startpage_event_handlers = []
        window._startpage_event_tokens = []
        window._panel_event_handlers = []
        window._panel_event_tokens = []
        window._plugin_manager_event_handlers = []
        window._plugin_manager_event_tokens = []
        window._menu_handles = {key: None for key in MENU_DEFINITIONS}
        window._menu_toggle_actions = {}
        window._canvas_context_entries = {}
        window._canvas_context_group_order = (
            "edit",
            "view",
            "canvas",
            "tools",
            "plugins",
            "custom",
        )
        window._last_canvas_context = None

        telemetry_dir = self._telemetry_dir or Path(__file__).resolve().parents[3] / "telemetry"
        with suppress(Exception):
            telemetry_dir.mkdir(parents=True, exist_ok=True)
        window._menu_telemetry_path = telemetry_dir / "menu_visibility.jsonl"

        audit_dir = self._user_root_factory(create=True) / "plugin_audit"
        with suppress(Exception):
            audit_dir.mkdir(parents=True, exist_ok=True)
        window._plugin_audit_path = audit_dir / "plugin_audit.jsonl"
        window._plugin_audit_log = deque(maxlen=128)
        window._menu_visibility_log = deque(maxlen=64)
        window._menu_telemetry_dialog = None
