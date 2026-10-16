"""Active tool session state and cleanup helpers."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from loomstroke.core.commands import Command
from loomstroke.core.task_registry import ToolTaskRegistry
from loomstroke.host_api import StrokePoint
from loomstroke.plugins.sdk.tool import ToolPlugin

logger = logging.getLogger(__name__)


@dataclass
class ToolSession:
    """Tracks the active tool session for stroke and task cleanup."""

    tool_id: str
    plugin: ToolPlugin
    task_registry: ToolTaskRegistry
    stroke_active: bool = False
    last_point: StrokePoint | None = None


class ToolSessionManager:
    """Owns active stroke/session state and pending command accumulation."""

    def __init__(self) -> None:
        self._session: ToolSession | None = None
        self._pending_commands: list[Command] = []

    @property
    def session(self) -> ToolSession | None:
        return self._session

    @property
    def pending_commands(self) -> list[Command]:
        return self._pending_commands

    def stroke_active(self) -> bool:
        return bool(self._session and self._session.stroke_active)

    def open_session(
        self,
        tool_id: str,
        plugin: ToolPlugin,
        *,
        bind_task_registry: Callable[[ToolPlugin, ToolTaskRegistry | None], None],
    ) -> ToolSession:
        session = ToolSession(
            tool_id=tool_id,
            plugin=plugin,
            task_registry=ToolTaskRegistry(tool_id),
        )
        self._session = session
        bind_task_registry(plugin, session.task_registry)
        return session

    def close_active_session(
        self,
        *,
        reason: str,
        force_end_stroke: Callable[[ToolSession], None],
        release_pointer_capture: Callable[[str], None],
        bind_task_registry: Callable[[ToolPlugin, ToolTaskRegistry | None], None],
    ) -> None:
        session = self._session
        if session is None:
            return
        if session.stroke_active:
            force_end_stroke(session)
        self.cancel_session_tasks(session, reason=reason)
        release_pointer_capture(session.tool_id)
        bind_task_registry(session.plugin, None)
        session.last_point = None
        session.stroke_active = False
        self._session = None

    def force_end_stroke(
        self,
        session: ToolSession,
        *,
        reason: str,
        collect_commands: Callable[[], None],
        flush_commands: Callable[[], None],
        update_tool_undo_state: Callable[[], None],
        release_pointer_capture: Callable[[str], None],
    ) -> None:
        if session.last_point is None:
            session.stroke_active = False
            release_pointer_capture(session.tool_id)
            return
        session.stroke_active = False
        try:
            session.plugin.on_pointer_up(session.last_point)
        except Exception:
            logger.exception("Forced pointer up failed for tool %s (%s)", session.tool_id, reason)
        collect_commands()
        flush_commands()
        update_tool_undo_state()
        release_pointer_capture(session.tool_id)

    def cancel_session_tasks(self, session: ToolSession, *, reason: str) -> None:
        registry = session.task_registry
        try:
            from loomstroke.platform.qt_async_integration import (  # noqa: PLC0415
                get_qt_async_integration,
            )

            integration = get_qt_async_integration()
            loop = integration.loop
        except Exception:
            loop = None
            integration = None
        if loop is not None and loop.is_running():
            try:
                integration.create_task(registry.cancel_all_async())  # type: ignore[union-attr]  # runtime-guarded None access
            except Exception:
                registry.cancel_all()
                logger.debug("Async task cancellation failed (%s)", reason, exc_info=True)
        else:
            registry.cancel_all()

    def has_pending_commands(self) -> bool:
        return bool(self._pending_commands)

    def extend_pending_commands(self, commands: list[Command]) -> None:
        self._pending_commands.extend(commands)

    def clear_pending_commands(self) -> None:
        self._pending_commands.clear()

    def begin_stroke(self, point: StrokePoint) -> bool:
        if self._session is None:
            return False
        self._session.stroke_active = True
        self._session.last_point = point
        return True

    def update_last_point(self, points: list[StrokePoint]) -> bool:
        if self._session is None or not self._session.stroke_active:
            return False
        if points:
            self._session.last_point = points[-1]
        return True

    def end_stroke(self, point: StrokePoint) -> bool:
        if self._session is None or not self._session.stroke_active:
            return False
        self._session.stroke_active = False
        self._session.last_point = point
        return True
