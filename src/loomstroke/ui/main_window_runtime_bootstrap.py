"""Runtime bootstrap coordination for :class:`MainWindow`."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..platform import get_enhanced_async_manager
from ..platform.qt_async_integration import get_qt_async_integration
from .layout_persistence import LayoutPersistence

RuntimeFactory = Callable[[], Any]
LayoutPersistenceFactory = Callable[..., Any]


class MainWindowRuntimeBootstrapCoordinator:
    """Initialize runtime service handles owned by ``MainWindow``."""

    def __init__(
        self,
        window: Any,
        *,
        async_manager_factory: RuntimeFactory = get_enhanced_async_manager,
        qt_async_integration_factory: RuntimeFactory = get_qt_async_integration,
        layout_persistence_type: LayoutPersistenceFactory = LayoutPersistence,
    ) -> None:
        self._window = window
        self._async_manager_factory = async_manager_factory
        self._qt_async_integration_factory = qt_async_integration_factory
        self._layout_persistence_type = layout_persistence_type

    def initialize_layout_components(self) -> None:
        """Create async integration and layout persistence handles."""
        window = self._window
        window._async_manager = self._async_manager_factory()
        window._integration = self._qt_async_integration_factory()
        window._layout_persistence = self._layout_persistence_type(
            window._layout_service_getter,
            window._settings,
        )
        window._workflow_layouts = self._layout_persistence_type(
            window._layout_service_getter,
            window._settings,
            namespace="ComfyUI",
        )
