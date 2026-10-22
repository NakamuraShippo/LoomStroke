"""MainWindow wiring for the live LoomStroke MCP runtime."""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
from collections.abc import Callable, Coroutine, Mapping
from typing import Any

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QMessageBox
from shiboken6 import isValid

from loomstroke.mcp.generation_capabilities import layer_generation_submitter
from loomstroke.mcp.live_runtime import LiveMcpEndpointInfo, LiveMcpRuntimeController
from loomstroke.mcp.types import McpStructuredError, ReasonCode
from loomstroke.platform.launch_options import ai_features_enabled
from loomstroke.platform.settings_schema import (
    MCP_CONFIRMATION_POLICY_KEY,
    MCP_LIVE_HTTP_ALLOWED_ORIGINS_KEY,
    MCP_LIVE_HTTP_ENABLED_KEY,
    MCP_LIVE_HTTP_PORT_KEY,
    MCP_LIVE_HTTP_ROOTS_KEY,
    MCP_LIVE_HTTP_SESSION_TOKEN_KEY,
    MCP_LIVE_HTTP_START_ON_LAUNCH_KEY,
    MCP_SKIP_HARD_CONFIRMATIONS_KEY,
)

logger = logging.getLogger(__name__)

MCP_RUNTIME_SETTING_KEYS = frozenset(
    {
        MCP_LIVE_HTTP_ALLOWED_ORIGINS_KEY,
        MCP_LIVE_HTTP_ENABLED_KEY,
        MCP_LIVE_HTTP_PORT_KEY,
        MCP_LIVE_HTTP_ROOTS_KEY,
        MCP_LIVE_HTTP_SESSION_TOKEN_KEY,
        MCP_LIVE_HTTP_START_ON_LAUNCH_KEY,
        MCP_CONFIRMATION_POLICY_KEY,
        MCP_SKIP_HARD_CONFIRMATIONS_KEY,
    }
)

ControllerFactory = Callable[..., LiveMcpRuntimeController]
CoroutineFactory = Callable[[], Coroutine[Any, Any, Any]]


class MainWindowMcpRuntimeCoordinator:
    """Connect MainWindow lifecycle and settings to ``LiveMcpRuntimeController``."""

    def __init__(
        self,
        window: Any,
        *,
        controller_factory: ControllerFactory = LiveMcpRuntimeController,
    ) -> None:
        self._window = window
        self._controller_factory = controller_factory

    def initialize_state(self) -> None:
        window = self._window
        controller = getattr(window, "_mcp_runtime_controller", None)
        if controller is None:
            controller = self._controller_factory(
                host_api=getattr(window, "_host_api", None),
                tool_manager_provider=self._tool_manager,
                document_operation_handler=self._document_operation,
                document_state_provider=self._document_state,
                editing_services_provider=self._editing_services,
                settings=getattr(window, "_settings", None),
                confirmation_callback=self._confirm_operation,
                generation_submitter=self._submit_generation,
                generation_canceller=self._cancel_generation,
                ai_effect_preset_lister=self._list_ai_effect_presets,
                ai_effect_preset_applier=self._apply_ai_effect_preset,
                status_callback=self._on_status,
            )
            window._mcp_runtime_controller = controller
        if not hasattr(window, "_mcp_generation_submitter"):
            window._mcp_generation_submitter = None
        if not hasattr(window, "_mcp_generation_canceller"):
            window._mcp_generation_canceller = None
        window._mcp_runtime_tasks = set()
        window._mcp_runtime_endpoint_info = controller.endpoint_info()

    async def start_on_launch(self) -> None:
        if not ai_features_enabled():
            return
        controller = self._controller()
        info = await controller.start_if_configured()
        if info is not None:
            self._on_status(info)
            logger.info("LoomStroke MCP live HTTP started on %s", info.url)

    def handle_setting_side_effect(self, key: str, value: Any) -> bool:
        if key not in MCP_RUNTIME_SETTING_KEYS or not ai_features_enabled():
            return key in MCP_RUNTIME_SETTING_KEYS
        controller = self._controller()
        if key == MCP_LIVE_HTTP_ENABLED_KEY:
            if bool(value):
                self._schedule("start", controller.start)
            else:
                self._schedule("stop", controller.stop)
            return True
        if key == MCP_LIVE_HTTP_START_ON_LAUNCH_KEY:
            if bool(value) and controller.live_http_enabled:
                self._schedule("start", controller.start)
            return True
        if key in {MCP_CONFIRMATION_POLICY_KEY, MCP_SKIP_HARD_CONFIRMATIONS_KEY}:
            return True
        if key == MCP_LIVE_HTTP_SESSION_TOKEN_KEY:
            self._on_status(controller.sync_session_token_from_settings())
            return True
        if controller.is_running:
            self._schedule("restart", controller.restart)
        return True

    def schedule_shutdown(self) -> None:
        controller = self._controller()
        if controller.is_running:
            self._schedule("stop", controller.stop)

    async def shutdown(self) -> None:
        tasks = set(getattr(self._window, "_mcp_runtime_tasks", set()))
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        controller = self._controller()
        await controller.stop()
        self._on_status(controller.endpoint_info())

    def endpoint_info(self) -> LiveMcpEndpointInfo:
        return self._controller().endpoint_info()

    def refresh_session_token(self) -> LiveMcpEndpointInfo:
        return self._controller().rotate_session_token()

    def _controller(self) -> LiveMcpRuntimeController:
        controller = getattr(self._window, "_mcp_runtime_controller", None)
        if controller is None:
            self.initialize_state()
            controller = self._window._mcp_runtime_controller
        return controller

    def _schedule(self, action: str, factory: CoroutineFactory) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("Cannot %s LoomStroke MCP runtime without a running event loop", action)
            return
        task = loop.create_task(factory())
        tasks = getattr(self._window, "_mcp_runtime_tasks", None)
        if not isinstance(tasks, set):
            tasks = set()
            self._window._mcp_runtime_tasks = tasks
        tasks.add(task)
        task.add_done_callback(lambda completed: self._on_task_done(action, completed))

    def _on_task_done(self, action: str, task: asyncio.Task[Any]) -> None:
        tasks = getattr(self._window, "_mcp_runtime_tasks", None)
        if isinstance(tasks, set):
            tasks.discard(task)
        if task.cancelled():
            return
        try:
            result = task.result()
        except Exception:
            logger.exception("LoomStroke MCP runtime %s failed", action)
            return
        if isinstance(result, LiveMcpEndpointInfo):
            self._on_status(result)

    def _on_status(self, info: LiveMcpEndpointInfo) -> None:
        self._window._mcp_runtime_endpoint_info = info
        logger.debug("LoomStroke MCP runtime status: %s", info.to_dict())

    async def _confirm_operation(self, operation: str, details: Mapping[str, Any]) -> bool:
        callback = getattr(self._window, "_mcp_confirmation_callback", None)
        if callable(callback):
            result = callback(operation, details)
            if inspect.isawaitable(result):
                result = await result
            return bool(result)

        detail_text = json.dumps(dict(details), ensure_ascii=False, indent=2, default=str)
        if len(detail_text) > 2000:
            detail_text = detail_text[:1997] + "..."
        title = QCoreApplication.translate("MainWindowMcpRuntime", "LoomStroke MCP")
        message_template = QCoreApplication.translate(
            "MainWindowMcpRuntime",
            "Allow this MCP operation?\n\nOperation: {operation}\n\n{details}",
        )
        message = message_template.format(operation=operation, details=detail_text)
        dialog = QMessageBox(
            QMessageBox.Icon.Question,
            title,
            message,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            self._window,
        )
        dialog.setDefaultButton(QMessageBox.StandardButton.No)
        dialog.setEscapeButton(QMessageBox.StandardButton.No)
        answer: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

        def finished(response: int) -> None:
            if not answer.done():
                answer.set_result(response == QMessageBox.StandardButton.Yes)

        def destroyed() -> None:
            if not answer.done():
                answer.set_result(False)

        dialog.finished.connect(finished)
        dialog.destroyed.connect(destroyed)
        try:
            # open() preserves window modality without a nested Qt event loop.
            dialog.open()
            return await answer
        finally:
            if isValid(dialog):
                dialog.finished.disconnect(finished)
                dialog.destroyed.disconnect(destroyed)
                dialog.close()
                dialog.deleteLater()

    def _tool_manager(self) -> Any:
        return getattr(getattr(self._window, "_host_api", None), "_tool_manager", None)

    def _editing_services(self) -> Mapping[str, Any]:
        from .main_window_mcp_services import editing_services  # noqa: PLC0415

        return editing_services(self._window)

    async def _document_operation(
        self, operation: str, arguments: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        from .main_window_mcp_documents import MainWindowMcpDocumentController  # noqa: PLC0415

        return await MainWindowMcpDocumentController(self._window).handle(operation, arguments)

    def _document_state(self) -> Mapping[str, Any]:
        from .main_window_mcp_documents import MainWindowMcpDocumentController  # noqa: PLC0415

        return MainWindowMcpDocumentController(self._window).state()

    def _mcp_generation_capabilities(self) -> dict[str, bool]:
        return {"available": callable(getattr(self._window, "_mcp_generation_submitter", None))}

    @layer_generation_submitter
    async def _submit_generation(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        submitter = getattr(self._window, "_mcp_generation_submitter", None)
        if not callable(submitter):
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "AI Effect generation submitter is not available.",
                details={"mode": "live_http"},
            )
        result = submitter(arguments)
        if inspect.isawaitable(result):
            result = await result
        if isinstance(result, Mapping):
            return result
        return {"status": "completed", "result": result}

    def _cancel_generation(self, layer_id: str) -> bool:
        canceller = getattr(self._window, "_mcp_generation_canceller", None)
        if not callable(canceller):
            return False
        return bool(canceller(layer_id))

    def _list_ai_effect_presets(self) -> list[Any]:
        library = getattr(self._window, "_ai_effect_preset_library", None)
        if library is None or not callable(getattr(library, "list_all", None)):
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "AI Effect preset library is not available.",
                details={"mode": "live_http"},
            )
        return list(library.list_all())

    def _apply_ai_effect_preset(self, preset_id: str) -> str | None:
        applier = getattr(self._window, "_ai_effect_preset_applier", None)
        if not callable(applier):
            raise McpStructuredError(
                ReasonCode.CAPABILITY_UNAVAILABLE,
                "AI Effect preset applier is not available.",
                details={"mode": "live_http"},
            )
        result = applier(preset_id)
        return str(result) if result else None


__all__ = ["MCP_RUNTIME_SETTING_KEYS", "MainWindowMcpRuntimeCoordinator"]
