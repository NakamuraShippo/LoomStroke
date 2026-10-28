"""
Main application window.

Per Master.md PR#4 specifications, provides main window skeleton
with docking management and proper async integration.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable, Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Protocol, TypedDict, TypeVar, cast

from PySide6.QtCore import (
    QChildEvent,
    QCoreApplication,
    QEvent,
    QObject,
    QPoint,
    QSize,
    Qt,
    QTimer,
    Signal,
    Slot,
)
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QCursor,
    QKeySequence,
    QMoveEvent,
    QPalette,
    QPixmap,
    QResizeEvent,
    QShowEvent,
)
from PySide6.QtWidgets import (
    QDialog,
    QDockWidget,
    QFileDialog,
    QMainWindow,
    QMenu,
    QMenuBar,
    QMessageBox,
    QProgressDialog,
    QToolBar,
    QWidget,
)

from ..host_api import (
    DocumentService,
    HostAPI,
    StrokePoint,
    UILayoutService,
    UILayoutSnapshot,
)
from ..platform import (
    ShortcutManager,
    ShortcutServiceAdapter,
)
from ..plugins.sdk import PluginRegistry
from ._dock_safe import is_valid
from .canvas.canvas_widget import CanvasWidget  # noqa: F401
from .dialogs.canvas_resize_dialog import CanvasResizeDialog
from .dialogs.image_properties_dialog import ImagePropertiesDialog
from .dialogs.menu_telemetry_dialog import MenuTelemetryRecord
from .dialogs.new_document_dialog import NewDocumentDialog
from .dialogs.plugin_compatibility_dialog import PluginCompatibilityDialog
from .dialogs.plugin_manager_dialog import PluginManagerDialog
from .dialogs.preferences_dialog import PreferencesDialog
from .layout_persistence import load_bundled_initial_layout
from .main_window_action_shortcuts import MainWindowActionShortcutCoordinator
from .main_window_ai_preset_menu import MainWindowAIPresetMenuCoordinator
from .main_window_app_integration import MainWindowAppIntegrationCoordinator
from .main_window_canvas_bootstrap import MainWindowCanvasBootstrapCoordinator
from .main_window_canvas_context_menu import (
    CanvasContextEntry,
    MainWindowCanvasContextMenuCoordinator,
)
from .main_window_comfyui_panel import MainWindowComfyUIPanelCoordinator
from .main_window_context_toolbar import MainWindowContextToolbarCoordinator
from .main_window_core_events import MainWindowCoreEventCoordinator
from .main_window_default_panels import MainWindowDefaultPanelsCoordinator
from .main_window_dock_layout import MainWindowDockLayoutController
from .main_window_document_workflow import MainWindowDocumentWorkflowCoordinator
from .main_window_edit_shape_actions import MainWindowEditShapeActionsCoordinator
from .main_window_event_tracking_state import MainWindowEventTrackingStateCoordinator
from .main_window_filter_actions import MainWindowFilterActionsCoordinator
from .main_window_gallery_panel import MainWindowGalleryPanelCoordinator
from .main_window_help_actions import MainWindowHelpActionCoordinator
from .main_window_history_actions import MainWindowHistoryActionCoordinator
from .main_window_host_menu_root import MainWindowHostMenuRootCoordinator
from .main_window_image_view_actions import MainWindowImageViewActionsCoordinator
from .main_window_language import MainWindowLanguageCoordinator
from .main_window_layer_actions import MainWindowLayerActionsCoordinator
from .main_window_layer_events import MainWindowLayerEventCoordinator
from .main_window_layout_state import MainWindowLayoutStateCoordinator
from .main_window_mcp_runtime import MainWindowMcpRuntimeCoordinator
from .main_window_menu_builder import MainWindowMenuBuilderCoordinator
from .main_window_menu_telemetry import MainWindowMenuTelemetryCoordinator
from .main_window_panel_registry import MainWindowPanelRegistryCoordinator
from .main_window_plugin_lifecycle import MainWindowPluginLifecycleCoordinator
from .main_window_plugin_manager import MainWindowPluginManagerCoordinator
from .main_window_plugin_menu_contributions import (
    MainWindowPluginMenuContributionCoordinator,
)
from .main_window_preferences_dialog import MainWindowPreferencesDialogCoordinator
from .main_window_preview_sync import MainWindowPreviewSyncCoordinator
from .main_window_qt_events import MainWindowQtEventShellCoordinator
from .main_window_runtime_bootstrap import MainWindowRuntimeBootstrapCoordinator
from .main_window_screen_mode import MainWindowScreenModeController
from .main_window_settings_applier import MainWindowSettingsApplier
from .main_window_settings_translation_bootstrap import (
    MainWindowSettingsTranslationBootstrapCoordinator,
)
from .main_window_shutdown import MainWindowShutdownCoordinator
from .main_window_tool_chrome import MainWindowToolChromeCoordinator
from .main_window_tool_plugin_bootstrap import MainWindowToolPluginBootstrapCoordinator
from .main_window_translation_menu import MainWindowTranslationMenuShellCoordinator
from .main_window_window_menu import MainWindowWindowMenuCoordinator
from .main_window_window_setup import MainWindowWindowSetupCoordinator
from .panel_ids import PANEL_TITLE_SOURCE_DEFAULTS
from .startpage_models import StartPageCanvasSpec

if TYPE_CHECKING:
    from ..platform.settings_manager import SettingsManager
    from .canvas.ruler_frame import RulerCanvasFrame
    from .menu_catalog import MenuDefinition

try:
    from qasync import asyncSlot as _async_slot
except ImportError as exc:
    raise ImportError("qasync is required") from exc

_AsyncCallableT = TypeVar("_AsyncCallableT", bound=Callable[..., Awaitable[Any]])


class EventBusProtocol(Protocol):
    def emit(self, event_name: str, payload: Any = ...) -> None: ...


def asyncSlot(*args: Any, **kwargs: Any) -> Callable[[_AsyncCallableT], _AsyncCallableT]:
    """Typed wrapper for qasync.asyncSlot to satisfy mypy."""

    return cast(Callable[[_AsyncCallableT], _AsyncCallableT], _async_slot(*args, **kwargs))


def _panel_outline_color(widget: QWidget) -> str:
    palette = widget.palette()
    base = palette.color(QPalette.ColorRole.Window)
    line = palette.color(QPalette.ColorRole.Midlight)
    if abs(line.lightness() - base.lightness()) < 12:
        line = base.lighter(170) if base.lightness() < 128 else base.darker(125)
    return line.name()


def _dock_separator_style(widget: QWidget) -> str:
    color = _panel_outline_color(widget)
    return f"QMainWindow::separator {{background-color: {color};width: 1px;height: 1px;}}"


logger = logging.getLogger(__name__)
plugin_audit_logger = logging.getLogger("loomstroke.audit.plugins")

_RENDER_DIAGNOSTICS_TRUTHY = {"1", "true", "yes", "on"}

_DOCK_TRACKING_PROP = "sr_dock_tracking"
_DOCK_UID_PROP = "sr_dock_uid"
_DOCK_FLOAT_SIZE_PROP = "sr_last_floating_size"
_DOCK_FLOAT_TIMER_PROP = "sr_floating_size_timer"
_DOCK_DRAGGING_PROP = "sr_floating_dragging"
_DOCK_DRAGGING_RELEASE_PROP = "sr_floating_drag_release"
_DOCK_RESTORING_SIZE_PROP = "sr_restoring_float_size"
_DOCK_PREDOCK_SIZE_PROP = "sr_predock_float_size"
_DOCK_DOCKED_SIZE_PROP = "sr_last_docked_size"
_DOCK_DOCKED_AREA_PROP = "sr_last_docked_area"
_DOCK_DEFAULT_AREA_PROP = "sr_default_area"
_DOCK_DEFAULT_VISIBLE_PROP = "sr_default_visible"
_DOCK_SPACER_PROP = "sr_dock_spacer"
_DOCK_SPACER_FOR_PROP = "sr_spacer_for_uid"
_PANEL_MIN_WIDTH = 100
_PANEL_MIN_HEIGHT = 50
_PANEL_MIN_SIZE = QSize(_PANEL_MIN_WIDTH, _PANEL_MIN_HEIGHT)


SlotNoArg = Callable[[], None]
SlotChecked = Callable[[bool], None]
ActionSlot = SlotNoArg | SlotChecked


class PanelAuditPanel(TypedDict):
    """Metadata about a registered panel for audit output."""

    id: str
    title: str
    dock_exists: bool
    is_visible: bool
    has_action: bool


class PanelAuditCanvas(TypedDict):
    """Canvas state summary for audit output."""

    exists: bool
    type: str | None
    visible: bool


class PanelAuditResult(TypedDict):
    """Structured payload returned by run_panel_audit."""

    registered_panels: list[PanelAuditPanel]
    visible_panels: list[str]
    special_panels: list[str]
    issues: list[str]
    canvas: PanelAuditCanvas


def _panel_registry_for(window: Any) -> MainWindowPanelRegistryCoordinator:
    coordinator = getattr(window, "_panel_registry_coordinator", None)
    if not isinstance(coordinator, MainWindowPanelRegistryCoordinator):
        coordinator = MainWindowPanelRegistryCoordinator(window)
        window._panel_registry_coordinator = coordinator
    return coordinator


def _dock_layout_for(window: Any) -> MainWindowDockLayoutController:
    controller = getattr(window, "_dock_layout_controller", None)
    if not isinstance(controller, MainWindowDockLayoutController):
        controller = MainWindowDockLayoutController(
            window,
            single_shot=QTimer.singleShot,
            load_initial_layout=load_bundled_initial_layout,
        )
        window._dock_layout_controller = controller
    return controller


def _layout_state_for(window: Any) -> MainWindowLayoutStateCoordinator:
    coordinator = getattr(window, "_layout_state_coordinator", None)
    if not isinstance(coordinator, MainWindowLayoutStateCoordinator):
        coordinator = MainWindowLayoutStateCoordinator(
            window,
            single_shot=QTimer.singleShot,
        )
        window._layout_state_coordinator = coordinator
    return coordinator


def _layer_events_for(window: Any) -> MainWindowLayerEventCoordinator:
    coordinator = getattr(window, "_layer_event_coordinator", None)
    if not isinstance(coordinator, MainWindowLayerEventCoordinator):
        coordinator = MainWindowLayerEventCoordinator(window)
        window._layer_event_coordinator = coordinator
    return coordinator


def _screen_mode_for(window: Any) -> MainWindowScreenModeController:
    controller = getattr(window, "_screen_mode_controller", None)
    if not isinstance(controller, MainWindowScreenModeController):
        controller = MainWindowScreenModeController(window)
        window._screen_mode_controller = controller
    return controller


def _plugin_manager_for(window: Any) -> MainWindowPluginManagerCoordinator:
    coordinator = getattr(window, "_plugin_manager_coordinator", None)
    if not isinstance(coordinator, MainWindowPluginManagerCoordinator):
        coordinator = MainWindowPluginManagerCoordinator(
            window,
            plugin_manager_dialog=lambda: PluginManagerDialog,
            compatibility_dialog=lambda: PluginCompatibilityDialog,
            message_box=lambda: QMessageBox,
            dialog_type=lambda: QDialog,
        )
        window._plugin_manager_coordinator = coordinator
    return coordinator


def _layer_actions_for(window: Any) -> MainWindowLayerActionsCoordinator:
    coordinator = getattr(window, "_layer_actions_coordinator", None)
    if not isinstance(coordinator, MainWindowLayerActionsCoordinator):
        coordinator = MainWindowLayerActionsCoordinator(window, message_box=lambda: QMessageBox)
        window._layer_actions_coordinator = coordinator
    return coordinator


def _document_workflow_for(window: Any) -> MainWindowDocumentWorkflowCoordinator:
    coordinator = getattr(window, "_document_workflow_coordinator", None)
    if not isinstance(coordinator, MainWindowDocumentWorkflowCoordinator):
        coordinator = MainWindowDocumentWorkflowCoordinator(
            window,
            new_document_dialog=lambda: NewDocumentDialog,
            file_dialog=lambda: QFileDialog,
            message_box=lambda: QMessageBox,
            progress_dialog=lambda: QProgressDialog,
            dialog_type=lambda: QDialog,
        )
        window._document_workflow_coordinator = coordinator
    return coordinator


def _soft_proof_controller_for(window: Any) -> Any:
    from .main_window_soft_proof import MainWindowSoftProofController  # noqa: PLC0415

    controller = getattr(window, "_soft_proof_controller", None)
    if not isinstance(controller, MainWindowSoftProofController):
        controller = MainWindowSoftProofController(window)
        window._soft_proof_controller = controller
    return controller


def _screentone_action_for(window: Any) -> Any:
    from .main_window_screentone_action import MainWindowScreentoneAction  # noqa: PLC0415

    action = getattr(window, "_screentone_action", None)
    if not isinstance(action, MainWindowScreentoneAction):
        action = MainWindowScreentoneAction(window)
        window._screentone_action = action
    return action


def _image_size_action_for(window: Any) -> Any:
    from .main_window_image_size_action import MainWindowImageSizeAction  # noqa: PLC0415

    action = getattr(window, "_image_size_action", None)
    if not isinstance(action, MainWindowImageSizeAction):
        action = MainWindowImageSizeAction(window)
        window._image_size_action = action
    return action


def _font_diagnostics_action_for(window: Any) -> Any:
    from .main_window_font_diagnostics_action import (  # noqa: PLC0415
        MainWindowFontDiagnosticsAction,
    )

    action = getattr(window, "_font_diagnostics_action", None)
    if not isinstance(action, MainWindowFontDiagnosticsAction):
        action = MainWindowFontDiagnosticsAction(window)
        window._font_diagnostics_action = action
    return action


def _place_action_for(window: Any) -> Any:
    from .main_window_place_action import MainWindowPlaceAction  # noqa: PLC0415

    action = getattr(window, "_place_action", None)
    if not isinstance(action, MainWindowPlaceAction):
        action = MainWindowPlaceAction(window)
        window._place_action = action
    return action


def _document_mode_action_for(window: Any) -> Any:
    from .main_window_document_mode_action import (  # noqa: PLC0415
        MainWindowDocumentModeAction,
    )

    action = getattr(window, "_document_mode_action", None)
    if not isinstance(action, MainWindowDocumentModeAction):
        action = MainWindowDocumentModeAction(window)
        window._document_mode_action = action
    return action


def _package_action_for(window: Any) -> Any:
    from .main_window_package_action import MainWindowPackageAction  # noqa: PLC0415

    action = getattr(window, "_package_action", None)
    if not isinstance(action, MainWindowPackageAction):
        action = MainWindowPackageAction(window)
        window._package_action = action
    return action


def _digital_package_action_for(window: Any) -> Any:
    from .main_window_digital_package_action import (  # noqa: PLC0415
        MainWindowDigitalPackageExportAction,
    )

    action = getattr(window, "_digital_package_action", None)
    if not isinstance(action, MainWindowDigitalPackageExportAction):
        action = MainWindowDigitalPackageExportAction(
            window,
            document_metadata=lambda: _document_workflow_for(
                window
            )._document_metadata_for_export(),
        )
        window._digital_package_action = action
    return action


def _preflight_action_for(window: Any) -> Any:
    from .main_window_preflight_action import (  # noqa: PLC0415
        MainWindowPreflightAction,
    )

    action = getattr(window, "_preflight_action", None)
    if not isinstance(action, MainWindowPreflightAction):
        action = MainWindowPreflightAction(
            window,
            document_metadata=lambda: _document_workflow_for(
                window
            )._document_metadata_for_export(),
        )
        window._preflight_action = action
    return action


def _webtoon_export_action_for(window: Any) -> Any:
    from .main_window_webtoon_export_action import (  # noqa: PLC0415
        MainWindowWebtoonExportAction,
    )

    action = getattr(window, "_webtoon_export_action", None)
    if not isinstance(action, MainWindowWebtoonExportAction):
        action = MainWindowWebtoonExportAction(
            window,
            document_metadata=lambda: _document_workflow_for(
                window
            )._document_metadata_for_export(),
        )
        window._webtoon_export_action = action
    return action


def _print_export_action_for(window: Any) -> Any:
    from .main_window_print_export_action import (  # noqa: PLC0415
        MainWindowPrintExportAction,
    )

    action = getattr(window, "_print_export_action", None)
    if not isinstance(action, MainWindowPrintExportAction):
        action = MainWindowPrintExportAction(
            window,
            document_metadata=lambda: _document_workflow_for(
                window
            )._document_metadata_for_export(),
        )
        window._print_export_action = action
    return action


def _image_view_actions_for(window: Any) -> MainWindowImageViewActionsCoordinator:
    coordinator = getattr(window, "_image_view_actions_coordinator", None)
    if not isinstance(coordinator, MainWindowImageViewActionsCoordinator):
        coordinator = MainWindowImageViewActionsCoordinator(
            window,
            canvas_resize_dialog=lambda: CanvasResizeDialog,
            image_properties_dialog=lambda: ImagePropertiesDialog,
            message_box=lambda: QMessageBox,
            dialog_type=lambda: QDialog,
        )
        window._image_view_actions_coordinator = coordinator
    return coordinator


def _filter_actions_for(window: Any) -> MainWindowFilterActionsCoordinator:
    coordinator = getattr(window, "_filter_actions_coordinator", None)
    if not isinstance(coordinator, MainWindowFilterActionsCoordinator):
        coordinator = MainWindowFilterActionsCoordinator(window, message_box=lambda: QMessageBox)
        window._filter_actions_coordinator = coordinator
    return coordinator


def _application_clipboard() -> Any:
    from PySide6.QtWidgets import QApplication  # noqa: PLC0415

    return QApplication.clipboard()


def _edit_shape_actions_for(window: Any) -> MainWindowEditShapeActionsCoordinator:
    coordinator = getattr(window, "_edit_shape_actions_coordinator", None)
    if not isinstance(coordinator, MainWindowEditShapeActionsCoordinator):
        coordinator = MainWindowEditShapeActionsCoordinator(
            window,
            message_box=lambda: QMessageBox,
            application_clipboard=_application_clipboard,
        )
        window._edit_shape_actions_coordinator = coordinator
    return coordinator


def _action_shortcuts_for(window: Any) -> MainWindowActionShortcutCoordinator:
    coordinator = getattr(window, "_action_shortcuts_coordinator", None)
    if not isinstance(coordinator, MainWindowActionShortcutCoordinator):
        coordinator = MainWindowActionShortcutCoordinator(window)
        window._action_shortcuts_coordinator = coordinator
    return coordinator


def _app_integration_for(window: Any) -> MainWindowAppIntegrationCoordinator:
    coordinator = getattr(window, "_app_integration_coordinator", None)
    if not isinstance(coordinator, MainWindowAppIntegrationCoordinator):
        coordinator = MainWindowAppIntegrationCoordinator(window)
        window._app_integration_coordinator = coordinator
    return coordinator


def _host_menu_root_for(window: Any) -> MainWindowHostMenuRootCoordinator:
    coordinator = getattr(window, "_host_menu_root_coordinator", None)
    if not isinstance(coordinator, MainWindowHostMenuRootCoordinator):
        coordinator = MainWindowHostMenuRootCoordinator(window)
        window._host_menu_root_coordinator = coordinator
    return coordinator


def _settings_translation_bootstrap_for(
    window: Any,
) -> MainWindowSettingsTranslationBootstrapCoordinator:
    coordinator = getattr(window, "_settings_translation_bootstrap_coordinator", None)
    if not isinstance(coordinator, MainWindowSettingsTranslationBootstrapCoordinator):
        coordinator = MainWindowSettingsTranslationBootstrapCoordinator(window)
        window._settings_translation_bootstrap_coordinator = coordinator
    return coordinator


def _canvas_context_menu_for(window: Any) -> MainWindowCanvasContextMenuCoordinator:
    coordinator = getattr(window, "_canvas_context_menu_coordinator", None)
    if not isinstance(coordinator, MainWindowCanvasContextMenuCoordinator):
        coordinator = MainWindowCanvasContextMenuCoordinator(window)
        window._canvas_context_menu_coordinator = coordinator
    return coordinator


def _ai_preset_menu_for(window: Any) -> MainWindowAIPresetMenuCoordinator:
    coordinator = getattr(window, "_ai_preset_menu_coordinator", None)
    if not isinstance(coordinator, MainWindowAIPresetMenuCoordinator):
        coordinator = MainWindowAIPresetMenuCoordinator(window)
        window._ai_preset_menu_coordinator = coordinator
    return coordinator


def _plugin_menu_contributions_for(window: Any) -> MainWindowPluginMenuContributionCoordinator:
    coordinator = getattr(window, "_plugin_menu_contribution_coordinator", None)
    if not isinstance(coordinator, MainWindowPluginMenuContributionCoordinator):
        coordinator = MainWindowPluginMenuContributionCoordinator(window)
        window._plugin_menu_contribution_coordinator = coordinator
    return coordinator


def _core_events_for(window: Any) -> MainWindowCoreEventCoordinator:
    coordinator = getattr(window, "_core_event_coordinator", None)
    if not isinstance(coordinator, MainWindowCoreEventCoordinator):
        coordinator = MainWindowCoreEventCoordinator(window)
        window._core_event_coordinator = coordinator
    return coordinator


def _window_menu_for(window: Any) -> MainWindowWindowMenuCoordinator:
    coordinator = getattr(window, "_window_menu_coordinator", None)
    if not isinstance(coordinator, MainWindowWindowMenuCoordinator):
        coordinator = MainWindowWindowMenuCoordinator(window)
        window._window_menu_coordinator = coordinator
    return coordinator


def _comfyui_panel_for(window: Any) -> MainWindowComfyUIPanelCoordinator:
    coordinator = getattr(window, "_comfyui_panel_coordinator", None)
    if not isinstance(coordinator, MainWindowComfyUIPanelCoordinator):
        coordinator = MainWindowComfyUIPanelCoordinator(window)
        window._comfyui_panel_coordinator = coordinator
    return coordinator


def _gallery_panel_for(window: Any) -> MainWindowGalleryPanelCoordinator:
    coordinator = getattr(window, "_gallery_panel_coordinator", None)
    if not isinstance(coordinator, MainWindowGalleryPanelCoordinator):
        coordinator = MainWindowGalleryPanelCoordinator(
            window,
            panel_min_size=_PANEL_MIN_SIZE,
            message_box=QMessageBox,
        )
        window._gallery_panel_coordinator = coordinator
    return coordinator


def _default_panels_for(window: Any) -> MainWindowDefaultPanelsCoordinator:
    coordinator = getattr(window, "_default_panels_coordinator", None)
    if not isinstance(coordinator, MainWindowDefaultPanelsCoordinator):
        coordinator = MainWindowDefaultPanelsCoordinator(
            window,
            panel_min_width=_PANEL_MIN_WIDTH,
            single_shot=QTimer.singleShot,
        )
        window._default_panels_coordinator = coordinator
    return coordinator


def _help_actions_for(window: Any) -> MainWindowHelpActionCoordinator:
    coordinator = getattr(window, "_help_action_coordinator", None)
    if not isinstance(coordinator, MainWindowHelpActionCoordinator):
        coordinator = MainWindowHelpActionCoordinator(window)
        window._help_action_coordinator = coordinator
    return coordinator


def _menu_builder_for(window: Any) -> MainWindowMenuBuilderCoordinator:
    coordinator = getattr(window, "_menu_builder_coordinator", None)
    if not isinstance(coordinator, MainWindowMenuBuilderCoordinator):
        coordinator = MainWindowMenuBuilderCoordinator(window)
        window._menu_builder_coordinator = coordinator
    return coordinator


def _tool_chrome_for(window: Any) -> MainWindowToolChromeCoordinator:
    coordinator = getattr(window, "_tool_chrome_coordinator", None)
    if not isinstance(coordinator, MainWindowToolChromeCoordinator):
        coordinator = MainWindowToolChromeCoordinator(
            window,
            floating_toolbar_state_key=MainWindow._FLOATING_TOOLBAR_STATE_KEY,
        )
        window._tool_chrome_coordinator = coordinator
    return coordinator


def _tool_plugin_bootstrap_for(window: Any) -> MainWindowToolPluginBootstrapCoordinator:
    coordinator = getattr(window, "_tool_plugin_bootstrap_coordinator", None)
    if not isinstance(coordinator, MainWindowToolPluginBootstrapCoordinator):
        coordinator = MainWindowToolPluginBootstrapCoordinator(window)
        window._tool_plugin_bootstrap_coordinator = coordinator
    return coordinator


def _plugin_lifecycle_for(window: Any) -> MainWindowPluginLifecycleCoordinator:
    coordinator = getattr(window, "_plugin_lifecycle_coordinator", None)
    if not isinstance(coordinator, MainWindowPluginLifecycleCoordinator):
        coordinator = MainWindowPluginLifecycleCoordinator(window)
        window._plugin_lifecycle_coordinator = coordinator
    return coordinator


def _shutdown_for(window: Any) -> MainWindowShutdownCoordinator:
    coordinator = getattr(window, "_shutdown_coordinator", None)
    if not isinstance(coordinator, MainWindowShutdownCoordinator):
        coordinator = MainWindowShutdownCoordinator(
            window,
            document_workflow=lambda: _document_workflow_for(window),
            message_box=lambda: QMessageBox,
            application_instance=QCoreApplication.instance,
            dock_widget_type=lambda: QDockWidget,
            is_valid_widget=is_valid,
            log_close=lambda: logger.info("MainWindow closing"),
        )
        window._shutdown_coordinator = coordinator
    return coordinator


def _canvas_bootstrap_for(window: Any) -> MainWindowCanvasBootstrapCoordinator:
    coordinator = getattr(window, "_canvas_bootstrap_coordinator", None)
    if not isinstance(coordinator, MainWindowCanvasBootstrapCoordinator):
        coordinator = MainWindowCanvasBootstrapCoordinator(window)
        window._canvas_bootstrap_coordinator = coordinator
    return coordinator


def _context_toolbar_for(window: Any) -> MainWindowContextToolbarCoordinator:
    coordinator = getattr(window, "_context_toolbar_coordinator", None)
    if not isinstance(coordinator, MainWindowContextToolbarCoordinator):
        coordinator = MainWindowContextToolbarCoordinator(window)
        window._context_toolbar_coordinator = coordinator
    return coordinator


def _history_actions_for(window: Any) -> MainWindowHistoryActionCoordinator:
    coordinator = getattr(window, "_history_action_coordinator", None)
    if not isinstance(coordinator, MainWindowHistoryActionCoordinator):
        coordinator = MainWindowHistoryActionCoordinator(window)
        window._history_action_coordinator = coordinator
    return coordinator


def _preferences_dialog_for(window: Any) -> MainWindowPreferencesDialogCoordinator:
    coordinator = getattr(window, "_preferences_dialog_coordinator", None)
    if not isinstance(coordinator, MainWindowPreferencesDialogCoordinator):
        coordinator = MainWindowPreferencesDialogCoordinator(
            window,
            dialog_type=lambda: PreferencesDialog,
        )
        window._preferences_dialog_coordinator = coordinator
    return coordinator


def _language_for(window: Any) -> MainWindowLanguageCoordinator:
    coordinator = getattr(window, "_language_coordinator", None)
    if not isinstance(coordinator, MainWindowLanguageCoordinator):
        coordinator = MainWindowLanguageCoordinator(window)
        window._language_coordinator = coordinator
    return coordinator


def _translation_menu_for(window: Any) -> MainWindowTranslationMenuShellCoordinator:
    coordinator = getattr(window, "_translation_menu_coordinator", None)
    if not isinstance(coordinator, MainWindowTranslationMenuShellCoordinator):
        coordinator = MainWindowTranslationMenuShellCoordinator(window)
        window._translation_menu_coordinator = coordinator
    return coordinator


def _window_setup_for(window: Any) -> MainWindowWindowSetupCoordinator:
    coordinator = getattr(window, "_window_setup_coordinator", None)
    if not isinstance(coordinator, MainWindowWindowSetupCoordinator):
        coordinator = MainWindowWindowSetupCoordinator(window)
        window._window_setup_coordinator = coordinator
    return coordinator


def _qt_event_shell_for(window: Any) -> MainWindowQtEventShellCoordinator:
    coordinator = getattr(window, "_qt_event_shell_coordinator", None)
    if not isinstance(coordinator, MainWindowQtEventShellCoordinator):
        coordinator = MainWindowQtEventShellCoordinator(window, single_shot=QTimer.singleShot)
        window._qt_event_shell_coordinator = coordinator
    return coordinator


def _event_tracking_state_for(window: Any) -> MainWindowEventTrackingStateCoordinator:
    coordinator = getattr(window, "_event_tracking_state_coordinator", None)
    if not isinstance(coordinator, MainWindowEventTrackingStateCoordinator):
        coordinator = MainWindowEventTrackingStateCoordinator(window)
        window._event_tracking_state_coordinator = coordinator
    return coordinator


def _runtime_bootstrap_for(window: Any) -> MainWindowRuntimeBootstrapCoordinator:
    coordinator = getattr(window, "_runtime_bootstrap_coordinator", None)
    if not isinstance(coordinator, MainWindowRuntimeBootstrapCoordinator):
        coordinator = MainWindowRuntimeBootstrapCoordinator(window)
        window._runtime_bootstrap_coordinator = coordinator
    return coordinator


def _mcp_runtime_for(window: Any) -> MainWindowMcpRuntimeCoordinator:
    coordinator = getattr(window, "_mcp_runtime_coordinator", None)
    if not isinstance(coordinator, MainWindowMcpRuntimeCoordinator):
        coordinator = MainWindowMcpRuntimeCoordinator(window)
        window._mcp_runtime_coordinator = coordinator
    return coordinator


def _menu_telemetry_for(window: Any) -> MainWindowMenuTelemetryCoordinator:
    coordinator = getattr(window, "_menu_telemetry_coordinator", None)
    if not isinstance(coordinator, MainWindowMenuTelemetryCoordinator):
        coordinator = MainWindowMenuTelemetryCoordinator(window)
        window._menu_telemetry_coordinator = coordinator
    return coordinator


class PluginAuditRecord(TypedDict):
    """Structured payload for plugin manager audit logs."""

    timestamp: str
    action: str
    actor: str
    plugin_id: str | None
    source: str | None
    result: str
    reason: str | None
    details: dict[str, Any] | None


class MainWindow(QMainWindow):
    """
    Main application window with async support.

    Manages docking, toolbars, menus, and panel coordination.
    """

    # Signals
    window_ready = Signal()
    window_closing = Signal()
    tool_changed = Signal(str)  # tool_id
    panel_toggled = Signal(str, bool)  # panel_id, visible
    _TOOLBAR_TITLE_SOURCES: ClassVar[dict[str, str]] = {
        "properties": "Tool Properties",
        "main": "Main",
        "tools": "Tools",
    }
    _FLOATING_TOOLBAR_STATE_KEY = "ui/floating_tool_toolbar"
    _PANEL_TITLE_SOURCE_DEFAULTS: ClassVar[dict[str, tuple[str, str]]] = dict(
        PANEL_TITLE_SOURCE_DEFAULTS
    )
    canvas: Any | None
    tool_manager: Any | None
    _canvas_frame: RulerCanvasFrame | None
    _event_bus: EventBusProtocol
    _plugin_registry: PluginRegistry | None
    _settings: SettingsManager
    _settings_applier: MainWindowSettingsApplier

    def __init__(
        self,
        host: HostAPI,
        registry: PluginRegistry,
        *args: Any,
        shortcut_manager: ShortcutManager | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._initialize_host_and_menu_root(host, registry)
        self._initialize_shortcut_and_service_components(shortcut_manager)
        self._initialize_mcp_runtime()
        self._initialize_layout_components()
        self._initialize_panel_state()
        self._initialize_event_tracking_state()

        # Setup UI
        self._setup_window()
        self._build_menubar()  # Use new comprehensive menu builder
        self._register_default_canvas_context_actions()
        self._setup_toolbars()
        self._setup_statusbar()
        self._setup_docking()

        # Load settings
        self._load_window_state()
        self._load_recent_files()

        # Connect signals
        self._connect_signals()

        # Connect EventBus handlers for panel management
        self._connect_panel_events()

        # Setup default UI components (canvas and panels)
        self._setup_default_ui()
        self._schedule_layout_restore()

        logger.info("MainWindow initialized")

    def _initialize_host_and_menu_root(self, host: HostAPI, registry: PluginRegistry) -> None:
        _host_menu_root_for(self).initialize(host, registry)

    def _initialize_shortcut_and_service_components(
        self, shortcut_manager: ShortcutManager | None
    ) -> None:
        settings_translation = _settings_translation_bootstrap_for(self)
        settings_translation.initialize_state()
        _action_shortcuts_for(self).initialize_shortcut_components(
            shortcut_manager,
            shortcut_manager_type=ShortcutManager,
            shortcut_service_adapter_type=ShortcutServiceAdapter,
        )
        settings_translation.apply_startup_settings()

    def _initialize_layout_components(self) -> None:
        _runtime_bootstrap_for(self).initialize_layout_components()

    def _initialize_mcp_runtime(self) -> None:
        _mcp_runtime_for(self).initialize_state()

    def _initialize_panel_state(self) -> None:
        _tool_chrome_for(self).initialize_state()
        _core_events_for(self).initialize_state()
        _document_workflow_for(self).initialize_document_state()
        _document_workflow_for(self).initialize_close_save_state()
        _filter_actions_for(self).initialize_state()
        _comfyui_panel_for(self).initialize_state()
        _default_panels_for(self).initialize_state()

        _menu_builder_for(self).initialize_state()
        _ai_preset_menu_for(self).initialize_state()
        _panel_registry_for(self).initialize_state()
        _dock_layout_for(self).initialize_state()
        _layout_state_for(self).initialize_state()
        _history_actions_for(self).initialize_state()
        _canvas_bootstrap_for(self).initialize_state()

        _screen_mode_for(self).initialize_state()

    def _initialize_event_tracking_state(self) -> None:
        _event_tracking_state_for(self).initialize()

    def _setup_window(self) -> None:
        """Set up window properties."""
        _window_setup_for(self).setup_window()

    def _ts(self, text: str) -> str:
        """Mark a string for translation while keeping the source literal."""
        return _translation_menu_for(self).ts(text)

    def _translation_token(self, text: str) -> str:
        return _translation_menu_for(self).translation_token(text)

    def _apply_window_title_translation(self, text: str) -> None:
        _translation_menu_for(self).apply_window_title_translation(text)

    def _update_window_title(self) -> None:
        _translation_menu_for(self).update_window_title()

    # --- Menu building utilities ---
    def _mk_action(
        self,
        text: str,
        slot: ActionSlot | None = None,
        shortcut: str | None = None,
        tip: str | None = None,
        checkable: bool = False,
        checked: bool = False,
        *,
        action_id: str | None = None,
        shortcut_category: str = "General",
        shortcut_description: str | None = None,
        shortcut_owner: str | None = "LoomStroke Core",
    ) -> QAction:
        """Create a menu action bound to the shortcut manager when possible."""

        return _action_shortcuts_for(self).make_action(
            text,
            slot,
            shortcut,
            tip,
            checkable,
            checked,
            action_id=action_id,
            shortcut_category=shortcut_category,
            shortcut_description=shortcut_description,
            shortcut_owner=shortcut_owner,
        )

    def _set_action_label(self, action: QAction, action_id: str, text: str) -> None:
        """Store the translated base label and reapply dynamic badge state."""

        _action_shortcuts_for(self).set_action_label(action, action_id, text)

    def _decorate_action_label(self, text: str, action_id: str) -> str:
        return _action_shortcuts_for(self).decorate_action_label(text, action_id)

    def _apply_action_shortcut_metadata(
        self,
        action: QAction,
        action_id: str,
        sequence: str | None,
        default: str | None,
    ) -> None:
        _action_shortcuts_for(self).apply_action_shortcut_metadata(
            action,
            action_id,
            sequence,
            default,
        )

    @staticmethod
    def _sequence_from_text(sequence: str | None) -> QKeySequence:
        """Build a QKeySequence from portable text, returning empty when unset."""

        return MainWindowActionShortcutCoordinator.sequence_from_text(sequence)

    def _ensure_shortcut_binding(self, action_id: str, handler: Callable[[], None]) -> None:
        _action_shortcuts_for(self).ensure_shortcut_binding(action_id, handler)

    def _remove_shortcut_binding(self, action_id: str) -> None:
        _action_shortcuts_for(self).remove_shortcut_binding(action_id)

    def _sync_tool_shortcut_bindings(self) -> None:
        _action_shortcuts_for(self).sync_tool_shortcut_bindings()

    def _update_action_tooltip(
        self,
        action: QAction,
        action_id: str,
        sequence: str | None,
        default: str | None,
    ) -> None:
        _action_shortcuts_for(self).update_action_tooltip(action, action_id, sequence, default)

    # --- Canvas context menu registration ---
    def register_canvas_context_action(
        self,
        action_id: str | None,
        *,
        label: str | None = None,
        handler: ActionSlot | None = None,
        shortcut: str | None = None,
        shortcut_category: str = "Canvas",
        shortcut_description: str | None = None,
        group: str = "custom",
        order: int = 100,
        enabled: Callable[[], bool] | None = None,
    ) -> None:
        _canvas_context_menu_for(self).register_action(
            action_id,
            label=label,
            handler=handler,
            shortcut=shortcut,
            shortcut_category=shortcut_category,
            shortcut_description=shortcut_description,
            group=group,
            order=order,
            enabled=enabled,
        )

    def unregister_canvas_context_action(self, key: str) -> None:
        _canvas_context_menu_for(self).unregister_action(key)

    def _register_shortcut_definition(
        self,
        action_id: str,
        *,
        label: str,
        default: str | None,
        category: str,
        description: str | None,
    ) -> None:
        _action_shortcuts_for(self).register_shortcut_definition(
            action_id,
            label=label,
            default=default,
            category=category,
            description=description,
        )

    def _register_default_canvas_context_actions(self) -> None:
        _canvas_context_menu_for(self).register_default_actions()

    def _on_canvas_context_menu_requested(self, payload: Any) -> None:
        _canvas_context_menu_for(self).on_requested(payload)

    def _show_canvas_context_menu(self, global_pos: QPoint) -> None:
        _canvas_context_menu_for(self).show_menu(global_pos)

    def _canvas_context_sort_key(self, entry: CanvasContextEntry) -> tuple[int, int, str]:
        return _canvas_context_menu_for(self).sort_key(entry)

    def _resolve_canvas_context_action(
        self,
        entry: CanvasContextEntry,
    ) -> QAction | None:
        return _canvas_context_menu_for(self).resolve_action(entry)

    def _set_last_hovered_shortcut(self, action_id: str) -> None:
        _preferences_dialog_for(self).set_last_hovered_shortcut(action_id)

    def _open_hover_shortcut_preferences(self) -> None:
        _preferences_dialog_for(self).open_hover_shortcut_preferences()

    def _on_preferences(self) -> None:
        _preferences_dialog_for(self).on_preferences()

    def _open_shortcut_preferences(self, action_id: str | None = None) -> None:
        _preferences_dialog_for(self).open_shortcut_preferences(action_id)

    def _update_history_actions(
        self,
        *,
        can_undo: bool | None = None,
        can_redo: bool | None = None,
    ) -> None:
        _history_actions_for(self).update_history_actions(
            can_undo=can_undo,
            can_redo=can_redo,
        )

    def _menu(self, bar: QMenuBar, title: str) -> QMenu:
        """Add a menu to the menu bar."""
        return _translation_menu_for(self).menu(bar, title)

    def _submenu(self, parent: QMenu, title: str) -> QMenu:
        """Add a submenu that participates in runtime translation."""

        return _translation_menu_for(self).submenu(parent, title)

    def _refresh_translations(self) -> None:
        _translation_menu_for(self).refresh_translations()

    def changeEvent(self, event: QEvent) -> None:
        super().changeEvent(event)
        _qt_event_shell_for(self).handle_change_event(event)

    def moveEvent(self, event: QMoveEvent) -> None:  # pragma: no cover - Qt event passthrough
        super().moveEvent(event)
        _qt_event_shell_for(self).handle_move_or_resize_event()

    def resizeEvent(self, event: QResizeEvent) -> None:  # pragma: no cover - Qt event passthrough
        super().resizeEvent(event)
        _qt_event_shell_for(self).handle_move_or_resize_event()

    def childEvent(self, event: QChildEvent) -> None:
        super().childEvent(event)
        _qt_event_shell_for(self).handle_child_event(event)

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        dock = _qt_event_shell_for(self).dock_filter_target(obj)
        if dock is None:
            return super().eventFilter(cast("QObject", obj), event)
        _qt_event_shell_for(self).handle_dock_filter_event(dock, event)
        return super().eventFilter(dock, event)

    def _is_left_button(self, event: QEvent) -> bool:
        return _dock_layout_for(self).is_left_button(event)

    def _sync_floating_chrome_positions(self) -> None:
        _tool_chrome_for(self).sync_floating_chrome_positions()

    def _handle_dock_filter_event(self, dock: QDockWidget, event: QEvent) -> None:
        _dock_layout_for(self).handle_dock_filter_event(dock, event)

    def _handle_nonclient_press(self, dock: QDockWidget, event: QEvent) -> None:
        _dock_layout_for(self).handle_nonclient_press(dock, event)

    def _handle_nonclient_release(self, dock: QDockWidget, event: QEvent) -> None:
        _dock_layout_for(self).handle_nonclient_release(dock, event)

    def _handle_button_release(self, dock: QDockWidget, event: QEvent) -> None:
        _dock_layout_for(self).handle_button_release(dock, event)

    def _handle_floating_move(self, dock: QDockWidget) -> None:
        _dock_layout_for(self).handle_floating_move(dock)

    def _handle_floating_resize(self, dock: QDockWidget) -> None:
        _dock_layout_for(self).handle_floating_resize(dock)

    @staticmethod
    def _strip_mnemonic(text: str) -> str:
        """Return a user-visible label without Qt mnemonic markers."""

        return text.replace("&&", "&").replace("&", "").strip()

    def _menu_enabled(self, menu_id: str) -> bool:
        """Return True if the given taxonomy menu should be visible."""
        return _menu_builder_for(self).menu_enabled(menu_id)

    def _build_optional_menu(self, bar: QMenuBar, menu_id: str) -> QMenu | None:
        return _menu_builder_for(self).build_optional_menu(bar, menu_id)

    def _build_view_menu(self, menu: QMenu) -> None:
        _menu_builder_for(self).build_view_menu(menu)

    def _build_layer_menu(self, menu: QMenu) -> None:
        _menu_builder_for(self).build_layer_menu(menu)

    def _build_filter_menu(self, menu: QMenu) -> None:
        _menu_builder_for(self).build_filter_menu(menu)

    def _populate_ai_preset_manager_entry(self, menu: QMenu) -> None:
        """Append an "AI Effect Presets..." action if the opener is wired.

        The opener is set by App._wire_ai_effect_preset_filter_menu once
        the AI Effect subsystem initialises.  When the subsystem is
        unavailable the entry stays hidden so a click cannot reach an
        uninitialised Preset Manager.
        """
        _ai_preset_menu_for(self).populate_manager_entry(menu)

    def _build_tools_menu(self, menu: QMenu) -> None:
        _menu_builder_for(self).build_tools_menu(menu)

    def set_ai_effect_preset_library(self, library: Any) -> None:
        """Register the preset library used by the "Installed AI Presets"
        Filter submenu (Phase 7-h-A). Triggers a menu rebuild so the
        submenu appears immediately when wiring lands post-construction.
        """
        _ai_preset_menu_for(self).set_library(library)

    def set_ai_effect_preset_applier(self, applier: Any) -> None:
        """Register the callback that materialises a preset as a
        canvas AI Effect layer (Phase 7-h-A). Triggered by clicks on
        "Installed AI Presets" submenu items."""
        _ai_preset_menu_for(self).set_applier(applier)

    def set_ai_effect_preset_manager_opener(self, opener: Any) -> None:
        """Register the callback that opens the AI Effects Manager
        window (Phase 6-e). Triggers a menu rebuild so the AI menu
        entry becomes available without requiring a restart."""
        _ai_preset_menu_for(self).set_manager_opener(opener)

    def set_mcp_generation_handlers(self, submitter: Any, canceller: Any) -> None:
        """Register AI Effect generation hooks used by the live MCP runtime."""

        self._mcp_generation_submitter = submitter
        self._mcp_generation_canceller = canceller

    def mcp_runtime_endpoint_info(self) -> Any:
        """Return the current live MCP endpoint state for Preferences."""

        return _mcp_runtime_for(self).endpoint_info()

    def mcp_runtime_refresh_session_token(self) -> Any:
        """Refresh the live MCP session token from Preferences."""

        return _mcp_runtime_for(self).refresh_session_token()

    def _populate_ai_presets_submenu(self, menu: QMenu) -> None:
        """Attach the "Installed AI Presets" submenu to the Filter menu.

        No-op unless both the preset library and the applier callback
        are wired. The submenu contents are populated lazily on
        ``aboutToShow`` so preset library changes (e.g. after an export
        through the Effect Editor) show up without a restart.
        """
        _ai_preset_menu_for(self).populate_presets_submenu(menu)

    def _rebuild_ai_presets_submenu(self, submenu: QMenu) -> None:
        """Refresh the submenu from ``library.list_all()``. Called by
        the ``aboutToShow`` signal so the list is always current."""
        _ai_preset_menu_for(self).rebuild_presets_submenu(submenu)

    def _on_ai_preset_triggered(self, preset_id: str) -> None:
        """Filter menu item handler — routes the preset through the
        applier callback and surfaces a failure in the status bar."""
        _ai_preset_menu_for(self).on_preset_triggered(preset_id)

    def _build_plugins_menu(self, menu: QMenu) -> None:
        _menu_builder_for(self).build_plugins_menu(menu)

    def _connect_action_slot(self, action: QAction, slot: ActionSlot) -> None:
        """Connect a QAction.triggered signal to a slot with varied arity."""

        _translation_menu_for(self).connect_action_slot(action, slot)

    @staticmethod
    def _slot_accepts_checked(slot: ActionSlot) -> bool:
        """Return True if slot expects a checked flag argument."""

        return MainWindowTranslationMenuShellCoordinator.slot_accepts_checked(slot)

    def _on_shortcut_changed(self, action_id: str, sequence: str) -> None:
        _action_shortcuts_for(self).on_shortcut_changed(action_id, sequence)

    # --- Comprehensive menu bar builder ---
    def _build_menubar(self) -> None:
        """Build comprehensive menu bar."""
        _menu_builder_for(self).build_menubar()

    def _build_file_menu(self, bar: QMenuBar) -> None:
        _menu_builder_for(self).build_file_menu(bar)

    def _build_window_and_view_menus(
        self,
        bar: QMenuBar,
    ) -> None:
        _menu_builder_for(self).build_window_and_view_menus(bar)

    def _build_edit_menu(self, bar: QMenuBar) -> None:
        _menu_builder_for(self).build_edit_menu(bar)

    def _build_image_menu(self, bar: QMenuBar) -> None:
        """Build the Image menu (Flip / Rotate — Phase A Wave 1)."""
        _menu_builder_for(self).build_image_menu(bar)

    def _build_help_menu(self, bar: QMenuBar) -> None:
        _menu_builder_for(self).build_help_menu(bar)

    def _setup_toolbars(self) -> None:
        """Set up toolbars."""
        _tool_chrome_for(self).setup_toolbars()

    def _setup_statusbar(self) -> None:
        """Set up status bar."""
        _tool_chrome_for(self).setup_statusbar()

    def _setup_docking(self) -> None:
        """Set up docking areas."""
        _tool_chrome_for(self).setup_docking()

    def _connect_signals(self) -> None:
        """Connect internal signals."""
        _core_events_for(self).connect_signals()

    def _connect_panel_events(self) -> None:
        """Connect EventBus handlers for panel management."""
        _core_events_for(self).connect_panel_events()

    def _connect_startpage_requests(self) -> None:
        """Connect StartPage-related EventBus requests."""
        _core_events_for(self).connect_startpage_requests()

    def _subscribe_layer_events(self, bus: Any) -> None:
        _core_events_for(self).subscribe_layer_events(bus)

    def _on_panel_register(self, payload: Mapping[str, Any]) -> None:
        """Handle panel registration via EventBus."""
        _panel_registry_for(self).on_panel_register(payload)

    def _on_panel_toggle(self, payload: Mapping[str, Any]) -> None:
        """Handle panel toggle via EventBus."""
        _panel_registry_for(self).on_panel_toggle(payload)

    def _on_panel_rename(self, payload: Mapping[str, Any]) -> None:
        """Handle panel rename via EventBus."""
        _panel_registry_for(self).on_panel_rename(payload)

    def _resolve_panel_title_source(
        self,
        panel_id: str,
        title: Any,
        context: str | None = None,
    ) -> tuple[str, str]:
        return _panel_registry_for(self).resolve_panel_title_source(panel_id, title, context)

    def _translate_panel_title(self, source: str, context: str) -> str:
        return _panel_registry_for(self).translate_panel_title(source, context)

    def _refresh_panel_titles(self) -> None:
        _panel_registry_for(self).refresh_panel_titles()

    def _load_window_state(self) -> None:
        """Load window state from settings."""
        _layout_state_for(self).load_window_state()

    def _save_window_state(self) -> None:
        """Save window state to settings."""
        _layout_state_for(self).save_window_state()

    def _schedule_layout_restore(self) -> None:
        """Attempt to restore the persisted layout once the service is ready."""
        _layout_state_for(self).schedule_layout_restore()

    def _attempt_layout_restore(self) -> None:
        _layout_state_for(self).attempt_layout_restore()

    def _apply_layout_snapshot(
        self,
        snapshot: UILayoutSnapshot,
        *,
        clear_missing: bool,
        allow_restore_state: bool = True,
        force_panel_layout: bool = False,
        service: UILayoutService | None = None,
    ) -> None:
        _layout_state_for(self).apply_layout_snapshot(
            snapshot,
            clear_missing=clear_missing,
            allow_restore_state=allow_restore_state,
            force_panel_layout=force_panel_layout,
            service=service,
        )

    def _layout_service_getter(self) -> UILayoutService | None:
        return _layout_state_for(self).layout_service_getter()

    def _load_recent_files(self) -> None:
        """Load recent files from settings."""
        _document_workflow_for(self).load_recent_files()

    def add_panel(
        self,
        panel_id: str,
        widget: QWidget,
        title: str,
        area: Qt.DockWidgetArea = Qt.DockWidgetArea.LeftDockWidgetArea,
    ) -> QDockWidget:
        """Add a dockable panel."""
        return _panel_registry_for(self).add_panel(panel_id, widget, title, area)

    def _apply_panel_outline(self, dock: QDockWidget) -> None:
        color = _panel_outline_color(self)
        outline_rules = [
            f"QDockWidget::title {{ border: 1px solid {color}; border-bottom: 0px; }}",
            f"QDockWidget::pane {{ border: 1px solid {color}; }}",
        ]
        existing = dock.styleSheet().strip()
        for rule in outline_rules:
            if rule in existing:
                continue
            existing = f"{existing}\n{rule}" if existing else rule
        dock.setStyleSheet(existing)

    def _apply_dock_separator_style(self) -> None:
        separator_rule = _dock_separator_style(self)
        existing = self.styleSheet().strip()
        if separator_rule in existing:
            return
        if existing:
            self.setStyleSheet(f"{existing}\n{separator_rule}")
        else:
            self.setStyleSheet(separator_rule)

    def _dock_label(self, dock: QDockWidget) -> str:
        return _dock_layout_for(self).dock_label(dock)

    def _dock_area_name(self, area: Qt.DockWidgetArea) -> str:
        return _dock_layout_for(self).dock_area_name(area)

    def _log_dock(self, dock: QDockWidget, message: str) -> None:
        _dock_layout_for(self).log_dock(dock, message)

    # -- Dock size persistence -------------------------------------------------

    _DOCK_SIZES_KEY = "ui/dock_sizes"

    def _dock_persist_key(self, dock: QDockWidget) -> str | None:
        """Return a stable key for persisting dock sizes, or None if unnamed."""
        return _dock_layout_for(self).dock_persist_key(dock)

    def _load_dock_sizes(self) -> None:
        """Load saved dock sizes from settings into caches."""
        _dock_layout_for(self).load_dock_sizes()

    def _save_dock_sizes(self) -> None:
        """Persist current dock sizes to settings."""
        _dock_layout_for(self).save_dock_sizes()

    def _mark_dock_sizes_dirty(self) -> None:
        """Schedule a deferred save of dock sizes."""
        _dock_layout_for(self).mark_dock_sizes_dirty()

    def _flush_dock_sizes(self) -> None:
        _dock_layout_for(self).flush_dock_sizes()

    # -- Spacer helpers --------------------------------------------------------

    def _is_spacer_dock(self, dock: QDockWidget) -> bool:
        return _dock_layout_for(self).is_spacer_dock(dock)

    def _dock_uid(self, dock: QDockWidget) -> str:
        return _dock_layout_for(self).dock_uid(dock)

    def _floating_cache_key(self, dock: QDockWidget) -> str:
        return _dock_layout_for(self).floating_cache_key(dock)

    def _last_floating_size(self, dock: QDockWidget) -> QSize | None:
        return _dock_layout_for(self).last_floating_size(dock)

    def _record_floating_size(self, dock: QDockWidget) -> None:
        _dock_layout_for(self).record_floating_size(dock)

    def _force_record_floating_size(self, dock: QDockWidget) -> None:
        _dock_layout_for(self).force_record_floating_size(dock)

    def _snapshot_docked_size(self, dock: QDockWidget) -> None:
        """Save the dock's current size and area while it is docked."""
        _dock_layout_for(self).snapshot_docked_size(dock)

    def _apply_docked_size(self, dock: QDockWidget) -> None:
        """Restore previously saved docked size via resizeDocks."""
        _dock_layout_for(self).apply_docked_size(dock)

    def _schedule_drag_finalize(self, dock: QDockWidget) -> None:
        _dock_layout_for(self).schedule_drag_finalize(dock)

    def _finalize_drag_release(self, dock: QDockWidget) -> None:
        _dock_layout_for(self).finalize_drag_release(dock)

    def _start_floating_size_timer(self, dock: QDockWidget) -> None:
        _dock_layout_for(self).start_floating_size_timer(dock)

    def _stop_floating_size_timer(self, dock: QDockWidget) -> None:
        _dock_layout_for(self).stop_floating_size_timer(dock)

    def _apply_floating_size_retry(self, dock: QDockWidget, size: QSize) -> None:
        _dock_layout_for(self).apply_floating_size_retry(dock, size)

    def _ensure_dock_tracking(self, dock: QDockWidget) -> None:
        _dock_layout_for(self).ensure_dock_tracking(dock)

    def _show_dock_context_menu(self, dock: QDockWidget, pos: Any) -> None:
        """Show context menu for stuck dock recovery."""
        _dock_layout_for(self).show_dock_context_menu(dock, pos)

    def _force_float(self, dock: QDockWidget) -> None:
        """Force a dock widget to floating state (recovery action)."""
        _dock_layout_for(self).force_float(dock)

    def _force_dock(self, dock: QDockWidget) -> None:
        """Force a floating dock widget back to docked state (recovery action)."""
        _dock_layout_for(self).force_dock(dock)

    def _on_dock_top_level_changed(self, dock: QDockWidget, floating: bool) -> None:
        _dock_layout_for(self).on_dock_top_level_changed(dock, floating)

    def _on_dock_visibility_changed(self, dock: QDockWidget, visible: bool) -> None:
        _dock_layout_for(self).on_dock_visibility_changed(dock, visible)

    def _apply_floating_size(self, dock: QDockWidget, size: QSize) -> None:
        _dock_layout_for(self).apply_floating_size(dock, size)

    def _apply_empty_area_sizing(self, dock: QDockWidget) -> None:
        _dock_layout_for(self).apply_empty_area_sizing(dock)

    def _visible_real_docks_in_area(
        self,
        area: Qt.DockWidgetArea,
        *,
        exclude: QDockWidget | None = None,
    ) -> list[QDockWidget]:
        return _dock_layout_for(self).visible_real_docks_in_area(area, exclude=exclude)

    def _find_spacer_for_uid(self, uid: str) -> QDockWidget | None:
        return _dock_layout_for(self).find_spacer_for_uid(uid)

    def _remove_spacer_for_uid(self, uid: str) -> None:
        _dock_layout_for(self).remove_spacer_for_uid(uid)

    def _remove_spacers_in_area(self, area: Qt.DockWidgetArea) -> None:
        _dock_layout_for(self).remove_spacers_in_area(area)

    def _remove_spacer_dock(self, dock: QDockWidget, *, reason: str) -> None:
        _dock_layout_for(self).remove_spacer_dock(dock, reason=reason)

    def _spacer_docks(self) -> list[QDockWidget]:
        return _dock_layout_for(self).spacer_docks()

    def _real_dock_for_uid(self, uid: str) -> QDockWidget | None:
        return _dock_layout_for(self).real_dock_for_uid(uid)

    def _prune_stale_spacer_docks(self) -> bool:
        """Remove spacer docks whose owning real dock no longer occupies the area."""
        return _dock_layout_for(self).prune_stale_spacer_docks()

    def clear_transient_dock_spacers(self) -> bool:
        """Drop internal spacer docks before persisting or exporting layouts."""
        return _dock_layout_for(self).clear_transient_dock_spacers()

    def _hidden_real_docks_in_area(self, area: Qt.DockWidgetArea) -> list[QDockWidget]:
        return _dock_layout_for(self).hidden_real_docks_in_area(area)

    def _dock_area_from_saved_property(self, dock: QDockWidget) -> Qt.DockWidgetArea:
        return _dock_layout_for(self).dock_area_from_saved_property(dock)

    def _dock_area_from_default_property(self, dock: QDockWidget) -> Qt.DockWidgetArea:
        return _dock_layout_for(self).dock_area_from_default_property(dock)

    def _ensure_dock_attached_for_show(self, dock: QDockWidget) -> None:
        _dock_layout_for(self).ensure_dock_attached_for_show(dock)

    def _enable_dock_collapse(self) -> None:
        _dock_layout_for(self).enable_dock_collapse()

    def _schedule_collapse_empty_dock_areas(self) -> None:
        """Schedule a deferred collapse check, coalescing multiple requests."""
        _dock_layout_for(self).schedule_collapse_empty_dock_areas()

    @Slot()
    def _clear_collapse_cooldown(self) -> None:
        _dock_layout_for(self).clear_collapse_cooldown()

    def _collapse_empty_dock_areas(self) -> None:
        """Redistribute space in dock areas after docks are floated.

        Handles two cases:
        - **Partially empty**: some docks remain — use ``resizeDocks`` to
          make them fill the vacated space.
        - **Fully empty**: no visible docks — flush the orphan area and
          request a layout pass so Qt collapses it to zero size.
        """
        _dock_layout_for(self).collapse_empty_dock_areas()

    def _create_spacer_dock(self, uid: str, area: Qt.DockWidgetArea) -> QDockWidget:
        return _dock_layout_for(self).create_spacer_dock(uid, area)

    def _bind_panel_owner(self, panel_id: str, owner_plugin_id: str | None) -> None:
        _panel_registry_for(self).bind_panel_owner(panel_id, owner_plugin_id)

    def _unbind_panel_owner(self, panel_id: str) -> None:
        _panel_registry_for(self).unbind_panel_owner(panel_id)

    def _owner_has_panels(self, owner_plugin_id: str) -> bool:
        return _panel_registry_for(self).owner_has_panels(owner_plugin_id)

    def has_panel(self, panel_id: str) -> bool:
        """Return whether the panel is currently registered."""
        return _panel_registry_for(self).has_panel(panel_id)

    def set_panel_visible(self, panel_id: str, visible: bool) -> bool:
        """Set visibility for a registered panel."""
        return _panel_registry_for(self).set_panel_visible(panel_id, visible)

    def unregister_panel(self, panel_id: str) -> bool:
        """Unregister a panel and remove its menu action + dock."""
        return _panel_registry_for(self).unregister_panel(panel_id)

    def unregister_panels_by_owner(self, owner_plugin_id: str) -> int:
        """Unregister all panels owned by a plugin."""
        return _panel_registry_for(self).unregister_panels_by_owner(owner_plugin_id)

    def _panel_area_from_descriptor(self, position: str) -> Qt.DockWidgetArea:
        return _panel_registry_for(self).panel_area_from_descriptor(position)

    def register_dynamic_panel(
        self,
        panel_id: str,
        title: Any,
        widget: Any,
        *,
        area: str | Qt.DockWidgetArea = "right",
        default_visible: bool = True,
        owner_plugin_id: str | None = None,
        closable: bool = True,
        resizable: bool = True,
        floating: bool = False,
        default_size: tuple[int, int] | None = None,
        title_context: str | None = None,
    ) -> bool:
        """Register a panel from plugin metadata in a uniform way."""
        return _panel_registry_for(self).register_dynamic_panel(
            panel_id,
            title,
            widget,
            area=area,
            default_visible=default_visible,
            owner_plugin_id=owner_plugin_id,
            closable=closable,
            resizable=resizable,
            floating=floating,
            default_size=default_size,
            title_context=title_context,
        )

    def _build_dynamic_dock(self, panel_id: str, title: str, widget: Any) -> QDockWidget | None:
        return _panel_registry_for(self).build_dynamic_dock(panel_id, title, widget)

    def register_panel(
        self,
        panel_id: str,
        title: str,
        dock: QDockWidget,
        *,
        default_visible: bool = True,
        owner_plugin_id: str | None = None,
        title_context: str | None = None,
    ) -> None:
        """
        Register a panel (QDockWidget) with Window menu for individual toggle.

        Args:
            panel_id: Unique panel ID
            title: Menu display name
            dock: The QDockWidget to register
            default_visible: Initial visibility state
        """
        _panel_registry_for(self).register_panel(
            panel_id,
            title,
            dock,
            default_visible=default_visible,
            owner_plugin_id=owner_plugin_id,
            title_context=title_context,
        )

    def _reapply_pending_layout_tab_groups(self) -> None:
        service = self._layout_service_getter()
        reapply = getattr(service, "reapply_layout_tab_groups", None)
        if callable(reapply):
            QTimer.singleShot(0, reapply)

    def _apply_panel_min_size(self, dock: QDockWidget | None) -> None:
        """Apply the common minimum size for docked/floating panels."""
        _dock_layout_for(self).apply_panel_min_size(dock)

    def iter_panels(self) -> Iterable[tuple[str, QDockWidget]]:
        """Return an iterable of registered panel dock widgets."""
        return _panel_registry_for(self).iter_panels()

    def get_panel_dock(self, panel_id: str) -> QDockWidget | None:
        """Return the dock widget associated with a panel identifier."""
        return _panel_registry_for(self).get_panel_dock(panel_id)

    def _apply_color_panel_min_width(
        self,
        dock: QDockWidget | None,
        *,
        fallback: int | None = None,
    ) -> None:
        _dock_layout_for(self).apply_color_panel_min_width(dock, fallback=fallback)

    def _sync_color_panel_min_width(self) -> None:
        _dock_layout_for(self).sync_color_panel_min_width()

    def freeze_layout_settings(self, freeze: bool) -> None:
        """Temporarily suspend automatic layout persistence."""
        _layout_state_for(self).freeze_layout_settings(freeze)

    def _ensure_plugin_activated(self, plugin: Any) -> bool:
        """Ensure plugin lifecycle is ready before creating dock widgets."""
        return _plugin_lifecycle_for(self).ensure_plugin_activated(plugin)

    def _sync_ui_panel_plugins(self) -> None:
        _panel_registry_for(self).sync_ui_panel_plugins()

    def _sync_ui_panel_plugin(self, plugin: Any) -> bool:
        return _panel_registry_for(self).sync_ui_panel_plugin(plugin)

    def _collect_panel_descriptor(
        self,
        plugin: Any,
        owner_plugin_id: str,
    ) -> tuple[Any | None, Callable[..., Any] | None, Any]:
        return _panel_registry_for(self).collect_panel_descriptor(plugin, owner_plugin_id)

    def _collect_default_size(self, descriptor: Any) -> tuple[int, int] | None:
        return _panel_registry_for(self).collect_default_size(descriptor)

    def _register_plugin_panel(
        self,
        descriptor: Any,
        create_widget: Callable[[MainWindow], QWidget],
        plugin: Any,
        panel_id: str,
        owner_plugin_id: str,
        *,
        area_override: str | Qt.DockWidgetArea | None = None,
        title_context: str | None = None,
        use_descriptor_default_size: bool = True,
    ) -> bool:
        return _panel_registry_for(self).register_plugin_panel(
            descriptor,
            create_widget,
            plugin,
            panel_id,
            owner_plugin_id,
            area_override=area_override,
            title_context=title_context,
            use_descriptor_default_size=use_descriptor_default_size,
        )

    def _setup_ui_panel_plugin_if_available(
        self,
        plugin_id: str,
        *,
        area_override: str | Qt.DockWidgetArea | None = None,
        title_context: str | None = None,
        use_descriptor_default_size: bool = False,
        before_register: Callable[[], None] | None = None,
        after_register: Callable[[], None] | None = None,
        success_message: str | None = None,
    ) -> bool:
        return _panel_registry_for(self).setup_ui_panel_plugin_if_available(
            plugin_id,
            area_override=area_override,
            title_context=title_context,
            use_descriptor_default_size=use_descriptor_default_size,
            before_register=before_register,
            after_register=after_register,
            success_message=success_message,
        )

    def _setup_ui_panel_plugin(
        self,
        plugin: Any,
        owner_plugin_id: str,
        *,
        area_override: str | Qt.DockWidgetArea | None = None,
        title_context: str | None = None,
        use_descriptor_default_size: bool = False,
        before_register: Callable[[], None] | None = None,
        after_register: Callable[[], None] | None = None,
        success_message: str | None = None,
    ) -> bool:
        return _panel_registry_for(self).setup_ui_panel_plugin(
            plugin,
            owner_plugin_id,
            area_override=area_override,
            title_context=title_context,
            use_descriptor_default_size=use_descriptor_default_size,
            before_register=before_register,
            after_register=after_register,
            success_message=success_message,
        )

    def _unsync_ui_panel_plugin(self, plugin_id: str) -> int:
        return _panel_registry_for(self).unsync_ui_panel_plugin(plugin_id)

    def _toggle_dock_from_menu(self, panel_id: str, on: bool) -> None:
        """Toggle dock visibility from menu action."""
        _panel_registry_for(self).toggle_dock_from_menu(panel_id, on)

    def _sync_action_from_dock(self, panel_id: str, visible: bool) -> None:
        """Sync menu action state from dock visibility change."""
        _panel_registry_for(self).sync_action_from_dock(panel_id, visible)

    def _toggle_toolbar_from_menu(self, toolbar_id: str, on: bool) -> None:
        """Toggle toolbar visibility from the Window menu."""
        _window_menu_for(self).toggle_toolbar_from_menu(toolbar_id, on)

    def _is_toolbar_visible_for_menu(self, toolbar_id: str, toolbar: QToolBar) -> bool:
        return _window_menu_for(self).is_toolbar_visible_for_menu(toolbar_id, toolbar)

    def _sync_action_from_toolbar(self, toolbar_id: str, visible: bool) -> None:
        """Sync Window menu toolbar action state from toolbar visibility."""
        _window_menu_for(self).sync_action_from_toolbar(toolbar_id, visible)

    def _raise_visible_floating_tool_toolbar(self) -> None:
        _tool_chrome_for(self).raise_visible_floating_tool_toolbar()

    def _request_native_canvas_refresh(
        self,
        *,
        force_full_recompose: bool = False,
    ) -> None:
        _tool_chrome_for(self).request_native_canvas_refresh(
            force_full_recompose=force_full_recompose
        )

    def _request_native_canvas_refresh_deferred(
        self,
        *,
        force_full_recompose: bool = False,
    ) -> None:
        _tool_chrome_for(self).request_native_canvas_refresh_deferred(
            force_full_recompose=force_full_recompose
        )

    def _show_floating_tool_toolbar_at_default(self) -> None:
        _tool_chrome_for(self).show_floating_tool_toolbar_at_default()

    def _restore_initial_floating_tool_toolbar_state(self) -> None:
        _tool_chrome_for(self).restore_initial_floating_tool_toolbar_state()

    def _save_floating_tool_toolbar_state(self) -> None:
        _tool_chrome_for(self).save_floating_tool_toolbar_state()

    def _restore_floating_tool_toolbar_state(self) -> bool:
        return _tool_chrome_for(self).restore_floating_tool_toolbar_state()

    def _load_floating_tool_toolbar_state(self) -> dict[str, Any] | None:
        return _tool_chrome_for(self).load_floating_tool_toolbar_state()

    def _restore_floating_tool_toolbar_state_from(self, state: dict[str, Any]) -> bool:
        return _tool_chrome_for(self).restore_floating_tool_toolbar_state_from(state)

    def set_central_widget(self, widget: QWidget) -> None:
        """
        Set central widget (usually canvas).

        Args:
            widget: Central widget
        """
        _app_integration_for(self).set_central_widget(widget)

    def set_plugin_registry(self, registry: PluginRegistry) -> None:
        """
        Set plugin registry.

        Args:
            registry: Plugin registry
        """
        _app_integration_for(self).set_plugin_registry(registry)

    def _register_tools_from_registry(
        self,
        tool_manager: Any,
        registry: PluginRegistry,
    ) -> None:
        _tool_plugin_bootstrap_for(self).register_tools_from_registry(tool_manager, registry)

    def _register_tools_from_plugin(self, tool_manager: Any, plugin: Any) -> None:
        _tool_plugin_bootstrap_for(self).register_tools_from_plugin(tool_manager, plugin)

    def _resolve_plugin_tools(self, plugin: Any) -> list[Any]:
        return _tool_plugin_bootstrap_for(self).resolve_plugin_tools(plugin)

    def _register_single_tool(self, tool_manager: Any, tool: Any) -> None:
        _tool_plugin_bootstrap_for(self).register_single_tool(tool_manager, tool)

    @property
    def plugin_registry(self) -> PluginRegistry | None:
        """Get plugin registry."""
        return self._plugin_registry

    def set_host_api(self, host: HostAPI) -> None:
        """
        Set the host API for menu integration.

        Args:
            host: HostAPI instance
        """
        _app_integration_for(self).set_host_api(host)

    def _sync_tool_properties_panel_tool_manager(self) -> None:
        _tool_plugin_bootstrap_for(self).sync_tool_properties_panel_tool_manager()

    @asyncSlot()
    async def initialize_async(self) -> None:
        """Async initialization."""
        await _app_integration_for(self).initialize_async()
        await _mcp_runtime_for(self).start_on_launch()

    # ---------- Dynamic menu population ----------
    def _populate_recent_menu(self, menu: QMenu) -> None:
        """Populate recent files menu."""
        _menu_builder_for(self).populate_recent_menu(menu)

    def _populate_panels_menu(self, menu: QMenu) -> None:
        """Populate panels menu from plugins (kept for View > Panels submenu)."""
        _menu_builder_for(self).populate_panels_menu(menu)

    def _populate_filter_menu(self, menu: QMenu) -> None:
        """Populate filter menu from plugins."""
        _menu_builder_for(self).populate_filter_menu(menu)

    def _populate_tools_menu(self, menu: QMenu) -> None:
        """Populate tools menu from plugins."""
        _menu_builder_for(self).populate_tools_menu(menu)

    def _populate_plugin_extra_menus(self, menu: QMenu) -> None:
        """Populate plugin-contributed menus."""
        _plugin_menu_contributions_for(self).populate_plugin_extra_menus(menu)

    # ---------- Menu event handlers ----------
    def _on_new_request(self, _: Any) -> None:
        _document_workflow_for(self).on_new_request(_)

    def _on_open_request(self, payload: Any) -> None:
        _document_workflow_for(self).on_open_request(payload)

    def _on_close_request(self, _: Any) -> None:
        _document_workflow_for(self).on_close_request(_)

    def _on_create_document_request(self, payload: Any) -> None:
        _document_workflow_for(self).on_create_document_request(payload)

    def _on_new(self) -> None:
        _document_workflow_for(self).on_new()

    def _on_close(self) -> None:
        _document_workflow_for(self).on_close()

    def _on_open(self) -> None:
        _document_workflow_for(self).on_open()

    def _open_new_document_dialog(self) -> None:
        _document_workflow_for(self).open_new_document_dialog()

    def _open_path(self, path: str, *, emit_event: bool = True) -> bool:
        return _document_workflow_for(self).open_path(path, emit_event=emit_event)

    def _on_import(self) -> None:
        _document_workflow_for(self).on_import()

    def _open_psd_as_document(self, path: Path) -> bool:
        return _document_workflow_for(self).open_psd_as_document(path)

    def _open_image_as_document(self, path: Path) -> bool:
        return _document_workflow_for(self).open_image_as_document(path)

    def _import_image_file(self, path: Path) -> bool:
        return _document_workflow_for(self).import_image_file(path)

    def _open_recent_file(self, path: str) -> None:
        _document_workflow_for(self).open_recent_file(path)

    def _apply_new_document(self, spec: StartPageCanvasSpec) -> None:
        _document_workflow_for(self).apply_new_document(spec)

    def _close_document(self) -> None:
        _document_workflow_for(self).close_document()

    def _set_document_present(self, present: bool) -> None:
        _document_workflow_for(self).set_document_present(present)

    def _get_document_service(self) -> DocumentService | None:
        return _document_workflow_for(self).get_document_service()

    def _load_document(self, path: Path) -> bool:
        return _document_workflow_for(self).load_document(path)

    def mark_document_recovered_from_autosave(
        self,
        *,
        original_path: Path | None,
        recovery_path: Path,
    ) -> None:
        _document_workflow_for(self).mark_document_recovered_from_autosave(
            original_path=original_path,
            recovery_path=recovery_path,
        )

    def _on_save(self) -> None:
        _document_workflow_for(self).on_save()

    def _on_save_as(self) -> None:
        _document_workflow_for(self).on_save_as()

    def _on_export(self) -> None:
        _document_workflow_for(self).on_export()

    def _on_export_pages(self) -> None:
        _document_workflow_for(self).on_export_pages()

    def _on_export_for_print(self) -> None:
        _print_export_action_for(self).run()

    def _on_export_webtoon(self) -> None:
        _webtoon_export_action_for(self).run()

    def _on_manga_preflight(self) -> None:
        _preflight_action_for(self).run()

    def _on_export_digital_package(self) -> None:
        _digital_package_action_for(self).run()

    def _on_font_diagnostics(self) -> None:
        _font_diagnostics_action_for(self).run()

    def _on_place_image(self) -> None:
        _place_action_for(self).run()

    def _on_package_project(self) -> None:
        _package_action_for(self).run()

    def _on_document_mode(self) -> None:
        _document_mode_action_for(self).run()

    def _prompt_save_path(self) -> Path | None:
        return _document_workflow_for(self).prompt_save_path()

    def _perform_save(self, target: Path) -> bool:
        return _document_workflow_for(self).perform_save(target)

    def _start_save_async(self, target: Path) -> None:
        _document_workflow_for(self).start_save_async(target)

    async def _run_save_async(
        self,
        service: DocumentService,
        target: Path,
    ) -> bool:
        return await _document_workflow_for(self).run_save_async(service, target)

    def _try_start_close_save_async(self, target: Path) -> bool:
        return _document_workflow_for(self).try_start_close_save_async(target)

    async def _run_close_with_save_async(
        self,
        service: DocumentService,
        target: Path,
        progress: QProgressDialog,
    ) -> None:
        await _document_workflow_for(self).run_close_with_save_async(service, target, progress)

    async def async_cleanup(self) -> None:
        """Release MainWindow-owned runtime resources during app shutdown."""

        canvas = getattr(self, "canvas", None)
        if isinstance(canvas, QObject) and not is_valid(canvas):
            return
        cleanup = getattr(canvas, "async_cleanup", None)
        if not callable(cleanup):
            return
        result = cleanup()
        if inspect.isawaitable(result):
            await result

    def _prompt_export_path(self) -> tuple[Path | None, str | None]:
        return _document_workflow_for(self).prompt_export_path()

    def _export_document(self, path: Path, fmt_hint: str | None) -> bool:
        return _document_workflow_for(self).export_document(path, fmt_hint)

    def _export_svg(self, path: Path) -> bool:
        return _document_workflow_for(self).export_svg(path)

    def _show_svg_export_omission_warning(self, name: str, count: int) -> None:
        _document_workflow_for(self).show_svg_export_omission_warning(name, count)

    @staticmethod
    def _svg_vector_layer_export_issue(layer: Any) -> str | None:
        return MainWindowDocumentWorkflowCoordinator.svg_vector_layer_export_issue(layer)

    def _export_psd(self, path: Path) -> bool:
        return _document_workflow_for(self).export_psd(path)

    def _do_export_psd_impl(
        self,
        path: Path,
        canvas: Any,
        BlendMode: type,  # noqa: N803
        FileIO: type,  # noqa: N803
    ) -> bool:
        return _document_workflow_for(self).do_export_psd_impl(path, canvas, BlendMode, FileIO)

    def _resolve_export_format(self, path: Path, fmt_hint: str | None) -> str:
        return MainWindowDocumentWorkflowCoordinator.resolve_export_format(path, fmt_hint)

    def _get_composited_pixmap(self) -> QPixmap | None:
        return _document_workflow_for(self).get_composited_pixmap()

    def _on_undo(self) -> None:
        _history_actions_for(self).on_undo()

    def _on_redo(self) -> None:
        _history_actions_for(self).on_redo()

    def _dispatch_selection_action(self, action_key: str) -> None:
        _edit_shape_actions_for(self).dispatch_selection_action(action_key)

    def _on_cut(self) -> None:
        _edit_shape_actions_for(self).on_cut()

    def _on_copy(self) -> None:
        _edit_shape_actions_for(self).on_copy()

    def _sync_to_system_clipboard(self) -> None:
        """Copy the internal selection clipboard image to the system clipboard."""
        _edit_shape_actions_for(self).sync_to_system_clipboard()

    def _on_paste(self) -> None:
        _edit_shape_actions_for(self).on_paste()

    def _on_paste_from_system_clipboard(self) -> None:
        _edit_shape_actions_for(self).on_paste_from_system_clipboard()

    def _on_select_all(self) -> None:
        _edit_shape_actions_for(self).on_select_all()

    def _on_deselect(self) -> None:
        _edit_shape_actions_for(self).on_deselect()

    def _on_select_all_vectors(self) -> None:
        _edit_shape_actions_for(self).on_select_all_vectors()

    def _shape_manager_plugin(self) -> Any | None:
        return _edit_shape_actions_for(self).shape_manager_plugin()

    def _get_shape_manager_panel(self) -> QWidget | None:
        return _edit_shape_actions_for(self).get_shape_manager_panel()

    def _has_vector_selection(self) -> bool:
        return _edit_shape_actions_for(self).has_vector_selection()

    def _can_register_shape_from_selection(self) -> bool:
        return _edit_shape_actions_for(self).can_register_shape_from_selection()

    def _can_convert_shape_to_vector(self) -> bool:
        return _edit_shape_actions_for(self).can_convert_shape_to_vector()

    def _on_register_shape_from_selection(self) -> None:
        _edit_shape_actions_for(self).on_register_shape_from_selection()

    def _on_register_shape_merge_from_selection(self) -> None:
        _edit_shape_actions_for(self).on_register_shape_merge_from_selection()

    def _on_convert_shape_to_vector(self) -> None:
        _edit_shape_actions_for(self).on_convert_shape_to_vector()

    def _can_vector_fill_to_selection(self) -> bool:
        """Check if the selected vector object has closed (fillable) paths."""
        return _edit_shape_actions_for(self).can_vector_fill_to_selection()

    @staticmethod
    def _get_first_selected_vector_id(selection: dict[str, Any]) -> str | None:
        """Extract the first vector object ID from a vector selection dict."""
        return MainWindowEditShapeActionsCoordinator.get_first_selected_vector_id(selection)

    def _on_vector_fill_to_selection(self) -> None:
        """Convert the selected vector object's fill area to a raster selection."""
        _edit_shape_actions_for(self).on_vector_fill_to_selection()

    def _on_canvas_resize(self) -> None:
        _image_view_actions_for(self).on_canvas_resize()

    def _on_image_flip_horizontal(self) -> None:
        """Flip the entire canvas horizontally (Image menu)."""
        _image_view_actions_for(self).on_image_flip_horizontal()

    def _on_image_flip_vertical(self) -> None:
        """Flip the entire canvas vertically (Image menu)."""
        _image_view_actions_for(self).on_image_flip_vertical()

    def _apply_image_flip(self, *, horizontal: bool, vertical: bool) -> None:
        _image_view_actions_for(self).apply_image_flip(horizontal=horizontal, vertical=vertical)

    def _on_image_rotate_cw(self) -> None:
        """Rotate the entire canvas 90° clockwise (Image menu)."""
        _image_view_actions_for(self).on_image_rotate_cw()

    def _on_image_rotate_ccw(self) -> None:
        """Rotate the entire canvas 90° counter-clockwise (Image menu)."""
        _image_view_actions_for(self).on_image_rotate_ccw()

    def _on_image_rotate_180(self) -> None:
        """Rotate the entire canvas 180° (Image menu)."""
        _image_view_actions_for(self).on_image_rotate_180()

    def _apply_image_rotate(self, angle: int) -> None:
        _image_view_actions_for(self).apply_image_rotate(angle)

    def _on_comic_book_settings(self) -> None:
        _image_view_actions_for(self).on_comic_book_settings()

    def _on_image_properties(self) -> None:
        """Show the Image Properties dialog (Phase A Wave 1 #15)."""
        _image_view_actions_for(self).on_image_properties()

    def _on_image_size(self) -> None:
        """Show the Image Size dialog and resample the canvas (CT-13)."""
        _image_size_action_for(self).run()

    def _on_layer_new_screentone(self) -> None:
        """Create a screentone carrier layer + effect from the selection (CT-02)."""
        _screentone_action_for(self).run()

    def _current_canvas_size(self) -> tuple[int, int]:
        return _image_view_actions_for(self).current_canvas_size()

    def _on_select_invert(self) -> None:
        self._event_bus.emit("select.invert", {})

    def _apply_canvas_zoom(self, factor: float) -> None:
        """Apply a relative zoom change to the active canvas."""
        _image_view_actions_for(self).apply_canvas_zoom(factor)

    def _set_canvas_zoom(self, zoom: float) -> None:
        """Set the active canvas zoom to an absolute value."""
        _image_view_actions_for(self).set_canvas_zoom(zoom)

    def _set_canvas_grid_visibility(self, on: bool) -> None:
        """Set the active canvas grid visibility."""
        _image_view_actions_for(self).set_canvas_grid_visibility(on)

    def _set_canvas_grid_size(self, size: int) -> None:
        """Set the active canvas grid size (interval)."""
        _image_view_actions_for(self).set_canvas_grid_size(size)

    def _sync_view_toggle_actions(self) -> None:
        """Synchronize view-related menu action checked states with current canvas/state."""
        _image_view_actions_for(self).sync_view_toggle_actions()

    def _create_context_toolbar_manager(self) -> Any:
        """Create and wire the ContextToolbarManager."""
        return _context_toolbar_for(self).create_context_toolbar_manager()

    def _on_tool_cursor_activated(self, tool_id: str) -> None:
        _context_toolbar_for(self).on_tool_cursor_activated(tool_id)

    def _on_tool_cursor_deactivated(self, tool_id: str) -> None:
        _context_toolbar_for(self).on_tool_cursor_deactivated(tool_id)

    def _apply_canvas_tool_cursor(self, cursor_name: str | None) -> None:
        _context_toolbar_for(self).apply_canvas_tool_cursor(cursor_name)

    @staticmethod
    def _cursor_from_tool_descriptor(cursor_name: str | None) -> QCursor | None:
        return MainWindowContextToolbarCoordinator.cursor_from_tool_descriptor(cursor_name)

    def _restore_view_preferences(self) -> None:
        """Restore persisted view toggle preferences."""
        _image_view_actions_for(self).restore_view_preferences()

    def _on_zoom_in(self) -> None:
        _image_view_actions_for(self).on_zoom_in()

    def _on_zoom_out(self) -> None:
        _image_view_actions_for(self).on_zoom_out()

    def _on_fit_spread(self) -> None:
        _image_view_actions_for(self).on_fit_spread()

    def _on_zoom_reset(self) -> None:
        _image_view_actions_for(self).on_zoom_reset()

    def _on_toggle_grid(self, on: bool) -> None:
        _image_view_actions_for(self).on_toggle_grid(on)

    def _on_toggle_soft_proof(self, on: bool) -> None:
        _soft_proof_controller_for(self).on_toggle(on)

    def _on_choose_proof_profile(self) -> None:
        _soft_proof_controller_for(self).choose_profile()

    def _on_toggle_rulers(self, on: bool) -> None:
        try:
            self._settings.set("view/rulers_visible", bool(on))
        except Exception:
            logger.debug("Failed to persist ruler visibility", exc_info=True)
        if self._canvas_frame is not None:
            self._canvas_frame.set_rulers_visible(bool(on))
        self._event_bus.emit("view.rulers", {"on": on})

    def _get_canvas_layers(self) -> list[Any]:
        return _layer_actions_for(self).get_canvas_layers()

    def _get_active_canvas_layer(self) -> tuple[Any | None, int | None]:
        return _layer_actions_for(self).get_active_canvas_layer()

    def _find_layer_by_id(self, layers: list[Any], layer_id: str) -> Any | None:
        return MainWindowLayerActionsCoordinator.find_layer_by_id(layers, layer_id)

    def _refresh_layer_panel(self) -> None:
        _layer_actions_for(self).refresh_layer_panel()

    def _recompose_canvas(self, canvas: Any) -> None:
        MainWindowLayerActionsCoordinator.recompose_canvas(canvas)

    def _on_layer_new(self) -> None:
        _layer_actions_for(self).on_layer_new()

    def _copy_layer_visual_state(self, source_layer: Any, dest_layer: Any) -> None:
        _layer_actions_for(self).copy_layer_visual_state(source_layer, dest_layer)

    def _on_layer_dup(self) -> None:
        _layer_actions_for(self).on_layer_dup()

    def _on_layer_del(self) -> None:
        _layer_actions_for(self).on_layer_del()

    def _on_layer_merge_down(self) -> None:
        _layer_actions_for(self).on_layer_merge_down()

    def _on_layer_merge_visible(self) -> None:
        _layer_actions_for(self).on_layer_merge_visible()

    def _on_selection_to_mask(self) -> None:
        _layer_actions_for(self).on_selection_to_mask()

    def _on_layer_flatten_all(self) -> None:
        _layer_actions_for(self).on_layer_flatten_all()

    def _move_active_canvas_layer_to(self, target_index: int) -> None:
        _layer_actions_for(self).move_active_canvas_layer_to(target_index)

    def _on_layer_arrange_to_front(self) -> None:
        _layer_actions_for(self).on_layer_arrange_to_front()

    def _on_layer_arrange_forward(self) -> None:
        _layer_actions_for(self).on_layer_arrange_forward()

    def _on_layer_arrange_backward(self) -> None:
        _layer_actions_for(self).on_layer_arrange_backward()

    def _on_layer_arrange_to_back(self) -> None:
        _layer_actions_for(self).on_layer_arrange_to_back()

    def _on_layer_group(self) -> None:
        _layer_actions_for(self).on_layer_group()

    def _on_layer_rasterize(self) -> None:
        _layer_actions_for(self).on_layer_rasterize()

    def _on_last_filter(self) -> None:
        """Apply last used filter."""
        _filter_actions_for(self).on_last_filter()

    def _apply_filter(self, plugin: Any) -> None:
        """Apply a filter plugin."""
        _filter_actions_for(self).apply_filter(plugin)

    def _activate_tool(self, desc: Any) -> None:
        self._activate_tool_id(desc.id)

    def _activate_tool_id(self, tool_id: str) -> None:
        manager = getattr(self, "tool_manager", None)
        if manager is not None and hasattr(manager, "activate_tool"):
            try:
                manager.activate_tool(tool_id)
                return
            except Exception:
                logger.debug("Tool activation via manager failed", exc_info=True)
        self._event_bus.emit("tool.activate", {"tool_id": tool_id, "id": tool_id})

    def _open_tool_settings(self) -> None:
        self._event_bus.emit("ui.tools.settings", {})

    def _open_plugin_manager(self) -> None:
        _plugin_manager_for(self).open_plugin_manager()

    def _on_plugin_manager_event(self, payload: Mapping[str, Any] | None) -> None:
        """Handle Plugin Manager actions (open/import/update/enable/disable/remove/rollback)."""
        _plugin_manager_for(self).on_plugin_manager_event(payload)

    def _append_plugin_audit_record(
        self,
        action: str,
        plugin_id: str | None,
        source: str | None,
        result: str,
        reason: str | None = None,
        *,
        actor: str = "ui.plugins.manager",
        details: Mapping[str, Any] | None = None,
    ) -> None:
        _plugin_manager_for(self).append_plugin_audit_record(
            action,
            plugin_id,
            source,
            result,
            reason,
            actor=actor,
            details=details,
        )

    @staticmethod
    def _normalize_audit_details(
        details: Mapping[str, Any] | None,
    ) -> dict[str, Any] | None:
        return MainWindowPluginManagerCoordinator.normalize_audit_details(details)

    def _extract_plugin_manager_action(self, payload: Mapping[str, Any]) -> str:
        return MainWindowPluginManagerCoordinator.extract_plugin_manager_action(payload)

    def _extract_plugin_manager_plugin_id(
        self,
        payload: Mapping[str, Any],
        plugin: Any,
    ) -> str | None:
        return MainWindowPluginManagerCoordinator.extract_plugin_manager_plugin_id(
            payload,
            plugin,
        )

    def _handle_plugin_manager_import(
        self,
        payload: Mapping[str, Any],
        registry: PluginRegistry,
    ) -> None:
        _plugin_manager_for(self).handle_plugin_manager_import(payload, registry)

    def _handle_plugin_manager_update(
        self,
        plugin_id: str | None,
        payload: Mapping[str, Any],
        registry: PluginRegistry,
    ) -> None:
        _plugin_manager_for(self).handle_plugin_manager_update(plugin_id, payload, registry)

    def _extract_plugin_manager_source(self, payload: Mapping[str, Any]) -> str | None:
        return MainWindowPluginManagerCoordinator.extract_plugin_manager_source(payload)

    def _handle_plugin_manager_enable(
        self,
        plugin_id: str | None,
        plugin: Any,
        registry: PluginRegistry,
    ) -> None:
        _plugin_manager_for(self).handle_plugin_manager_enable(plugin_id, plugin, registry)

    def _handle_plugin_manager_disable(
        self,
        plugin_id: str | None,
        registry: PluginRegistry,
    ) -> None:
        _plugin_manager_for(self).handle_plugin_manager_disable(plugin_id, registry)

    def _handle_plugin_manager_remove(
        self,
        plugin_id: str | None,
        registry: PluginRegistry,
    ) -> None:
        _plugin_manager_for(self).handle_plugin_manager_remove(plugin_id, registry)

    def _handle_plugin_manager_rollback(
        self,
        plugin_id: str | None,
        registry: PluginRegistry,
    ) -> None:
        _plugin_manager_for(self).handle_plugin_manager_rollback(plugin_id, registry)

    def _toggle_fullscreen(self, on: bool) -> None:
        self.setWindowState(self.windowState() ^ Qt.WindowState.WindowFullScreen)

    def _toggle_screen_mode(self, on: bool | None = None) -> None:
        """Toggle Screen Mode — hide/show all dock panels, toolbars, and the
        status bar to maximise the canvas area (Phase A Wave 2 #21).

        Args:
            on: Explicit target state.  When ``None``, the current state is
                flipped.
        """
        _screen_mode_for(self).toggle(on)

    def _enter_screen_mode(self) -> None:
        """Record current visibility and hide dock/toolbar/statusbar."""
        _screen_mode_for(self).enter()

    def _exit_screen_mode(self) -> None:
        """Restore the visibility captured in :meth:`_enter_screen_mode`."""
        _screen_mode_for(self).exit()

    def _open_docs(self) -> None:
        _help_actions_for(self).open_docs()

    def _open_log_folder(self) -> None:
        _help_actions_for(self).open_log_folder()

    def _show_menu_visibility_log(self) -> None:
        _help_actions_for(self).show_menu_visibility_log()

    def _check_updates(self) -> None:
        _help_actions_for(self).check_updates()

    def _about(self) -> None:
        _help_actions_for(self).about()

    def _text_logo_filename(self) -> str:
        return _help_actions_for(self).text_logo_filename()

    def _noop(self) -> None:
        pass  # Placeholder for unimplemented features

    def _debug_menubar_state(self) -> None:
        """Debug menu bar state for troubleshooting."""
        mb = self.menuBar()
        logger.info("=== MenuBar Debug Info ===")
        logger.info(f"MenuBar exists: {mb is not None}")
        if mb:
            logger.info(f"MenuBar visible: {mb.isVisible()}")
            logger.info(f"MenuBar enabled: {mb.isEnabled()}")
            logger.info(f"MenuBar parent: {mb.parent()}")
            logger.info(f"MenuBar actions count: {len(mb.actions())}")
            logger.info(f"MenuBar geometry: {mb.geometry()}")
            logger.info(f"MenuBar native: {mb.isNativeMenuBar()}")
            # List all menus
            for action in mb.actions():
                if action.menu():
                    logger.info(f"  Menu: {action.text()}")
        logger.info("=========================")

    def _add_recent_file(self, path: str) -> None:
        """Add a file to recent files list."""
        _document_workflow_for(self).add_recent_file(path)

    def _setup_default_ui(self) -> None:
        """Set up default UI components (canvas and panels)."""
        # Set up canvas
        self._setup_canvas()

        # Set up default panels
        self._setup_default_panels()

    def _setup_canvas(self) -> None:
        """Set up the central canvas widget."""
        _canvas_bootstrap_for(self).setup_canvas()

    def _on_stroke_started(self, point: StrokePoint) -> None:
        """Handle stroke start from canvas."""
        _canvas_bootstrap_for(self).on_stroke_started(point)

    def _on_stroke_updated(self, points: Iterable[StrokePoint]) -> None:
        """Handle stroke update from canvas."""
        _canvas_bootstrap_for(self).on_stroke_updated(points)

    def _on_stroke_ended(self, point: StrokePoint) -> None:
        """Handle stroke end from canvas."""
        _canvas_bootstrap_for(self).on_stroke_ended(point)

    def _migrate_deprecated_gpu_settings(self) -> None:
        """Remove deprecated GPU toggle keys from the user settings file.

        Phase 3 of the GPU unification work removed the CPU compositing
        fallback. The following keys no longer have any effect and are
        silently deleted with a warning so the settings file stays in
        sync with the runtime behaviour:

        - ``performance/use_gpu``
        - ``render/use_gpu``
        """
        _canvas_bootstrap_for(self).migrate_deprecated_gpu_settings()

    def _setup_default_panels(self) -> None:
        """Set up default dock panels."""
        _default_panels_for(self).setup_default_panels()

    def _setup_layer_panel(self) -> None:
        _default_panels_for(self).setup_layer_panel()

    def _setup_preview_panel(self) -> None:
        _default_panels_for(self).setup_preview_panel()

    def _setup_tool_list_panel(self) -> None:
        _default_panels_for(self).setup_tool_list_panel()

    def _setup_additional_panels(self) -> None:
        """Set up additional panels if implementations are available."""
        _default_panels_for(self).setup_additional_panels()

    def _setup_page_panel_if_available(self) -> None:
        _default_panels_for(self).setup_page_panel_if_available()

    def _setup_color_panel_if_available(self) -> None:
        _default_panels_for(self).setup_color_panel_if_available()

    def _setup_text_font_panel_if_available(self) -> None:
        _default_panels_for(self).setup_text_font_panel_if_available()

    def _setup_shape_manager_panel_if_available(self) -> None:
        _default_panels_for(self).setup_shape_manager_panel_if_available()

    def _setup_prompt_editor_panel_if_available(self) -> None:
        _default_panels_for(self).setup_prompt_editor_panel_if_available()

    def _setup_nano_banana_panel_if_available(self) -> None:
        _default_panels_for(self).setup_nano_banana_panel_if_available()

    def _setup_gptimage2_panel_if_available(self) -> None:
        _default_panels_for(self).setup_gptimage2_panel_if_available()

    def _setup_production_control_panel_if_available(self) -> None:
        _default_panels_for(self).setup_production_control_panel_if_available()

    def _setup_comfyui_panel_if_available(self) -> None:
        _comfyui_panel_for(self).setup_if_available()

    def _setup_gallery_panel_if_available(self) -> None:
        """Set up the shared generation history gallery panel."""
        _gallery_panel_for(self).setup_if_available()

    def _gallery_regenerate(self, history_store: Any, job_id: str) -> None:
        """Handle Gallery Regenerate button."""
        _gallery_panel_for(self).regenerate(history_store, job_id)

    def _gallery_restore_to_panel(self, history_store: Any, job_id: str) -> None:
        """Handle Gallery Copy to Nodes button."""
        _gallery_panel_for(self).restore_to_panel(history_store, job_id)

    def _create_fallback_comfyui_panel(self) -> None:
        """Create ComfyUI panel using direct import as fallback."""
        _comfyui_panel_for(self).create_fallback_panel()

    def _create_comfyui_placeholder(self) -> None:
        """Create minimal ComfyUI placeholder panel."""
        _comfyui_panel_for(self).create_placeholder()

    def _on_preview_zoom_changed(self, zoom: float) -> None:
        """Apply preview zoom changes to the canvas viewport."""
        self._preview_sync().on_preview_zoom_changed(zoom)

    def _on_preview_fit_requested(self) -> None:
        """Fit the canvas to the current view area."""
        self._preview_sync().on_preview_fit_requested()

    def _on_preview_reset_zoom_requested(self) -> None:
        """Set canvas zoom to 100%."""
        self._preview_sync().on_preview_reset_zoom_requested()

    def _on_preview_drag_requested(self, delta_x: float, delta_y: float) -> None:
        """Pan the canvas by dragging the preview."""
        self._preview_sync().on_preview_drag_requested(delta_x, delta_y)

    def _on_preview_image_displayed(self, _width: int, _height: int) -> None:
        """Synchronize preview viewport indicator after image updates."""
        self._preview_sync().on_preview_image_displayed(_width, _height)

    def _on_canvas_viewport_changed(self, _state: dict[str, Any]) -> None:
        self._sync_preview_controls_from_canvas(_state)
        if self._canvas_frame is not None:
            self._canvas_frame.update_rulers()
        self._sync_preview_viewport_indicator()

    def _on_canvas_canvas_resized(self, _width: int, _height: int) -> None:
        if self._canvas_frame is not None:
            self._canvas_frame.update_rulers()
        self._sync_preview_viewport_indicator()

    def _preview_sync(self) -> MainWindowPreviewSyncCoordinator:
        coordinator = getattr(self, "_preview_sync_coordinator", None)
        if not isinstance(coordinator, MainWindowPreviewSyncCoordinator):
            coordinator = MainWindowPreviewSyncCoordinator(self, single_shot=QTimer.singleShot)
            self._preview_sync_coordinator = coordinator
        return coordinator

    def _on_canvas_composited_changed(self, pixmap: QPixmap) -> None:
        """Keep preview synchronized with canvas composite updates."""
        self._preview_sync().on_canvas_composited_changed(pixmap)

    def _sync_preview_controls_from_canvas(self, state: dict[str, Any] | None = None) -> None:
        """Synchronize preview zoom controls from canvas viewport."""
        self._preview_sync().sync_preview_controls_from_canvas(state)

    def _flush_pending_preview_sync(self) -> None:
        """Apply the latest pending preview image after stroke completion."""
        self._preview_sync().flush_pending_preview_sync()

    def _has_pending_thumbnail_flush(self) -> bool:
        return self._preview_sync().has_pending_thumbnail_flush()

    def _schedule_pending_thumbnail_flush(self) -> None:
        self._preview_sync().schedule_pending_thumbnail_flush()

    def _flush_pending_thumbnail_sync(self) -> None:
        self._preview_sync().flush_pending_thumbnail_sync()

    def _defer_layer_thumbnail_refresh(self, layer_id: str) -> None:
        self._preview_sync().defer_layer_thumbnail_refresh(layer_id)

    def _refresh_layer_thumbnail(self, layer_id: str) -> bool:
        return self._preview_sync().refresh_layer_thumbnail(layer_id)

    def _flush_pending_layer_thumbnail_sync(self) -> None:
        self._preview_sync().flush_pending_layer_thumbnail_sync()

    def _render_diagnostics_enabled(self) -> bool:
        return self._preview_sync().render_diagnostics_enabled()

    def _record_canvas_performance_diagnostic(self, name: str, elapsed_ms: float) -> None:
        self._preview_sync().record_canvas_performance_diagnostic(name, elapsed_ms)

    def get_canvas_performance_diagnostics(self) -> dict[str, Any]:
        """Return dev/test-only canvas plus preview/page thumbnail diagnostics."""
        return self._preview_sync().get_canvas_performance_diagnostics()

    def _sync_preview_from_canvas(self, pixmap: QPixmap | None = None) -> None:
        """Update preview with the current composited canvas image."""
        self._preview_sync().sync_preview_from_canvas(pixmap)

    def _update_active_page_thumbnail_from_preview(self, pixmap: QPixmap) -> None:
        self._preview_sync().update_active_page_thumbnail_from_preview(pixmap)

    def _encode_page_thumbnail_png(self, pixmap: QPixmap) -> bytes | None:
        return self._preview_sync().encode_page_thumbnail_png(pixmap)

    def _sync_preview_viewport_indicator(self) -> None:
        self._preview_sync().sync_preview_viewport_indicator()

    def _update_comfyui_dock_title(self, connected: bool | None = None) -> None:
        _comfyui_panel_for(self).update_dock_title(connected)

    def _apply_comfyui_dock_status_style(self, connected: bool | None) -> None:
        _comfyui_panel_for(self).apply_dock_status_style(connected)

    def _on_comfyui_connection_status_changed(self, connected: bool) -> None:
        _comfyui_panel_for(self).on_connection_status_changed(connected)

    def _bind_comfyui_panel(self, panel: Any) -> None:
        _comfyui_panel_for(self).bind_panel(panel)

    def _on_comfyui_workflow_changed(self, _workflow: object) -> None:
        _comfyui_panel_for(self).on_workflow_changed(_workflow)

    def _get_comfyui_workflow_label(self) -> str | None:
        return _comfyui_panel_for(self).get_workflow_label()

    def _save_active_comfyui_layout(self) -> None:
        _comfyui_panel_for(self).save_active_layout()

    def _apply_comfyui_layout(self, label: str) -> bool:
        return _comfyui_panel_for(self).apply_layout(label)

    def run_panel_audit(self) -> PanelAuditResult:
        """Audit panel registration and visibility state."""
        return cast("PanelAuditResult", _panel_registry_for(self).run_panel_audit())

    def show_panel_audit_dialog(self) -> None:
        """Show panel audit results in a dialog."""
        _panel_registry_for(self).show_panel_audit_dialog()

    def _on_tool_changed_event(self, event: Any) -> None:
        """Handle tool change event from event bus."""
        _core_events_for(self).on_tool_changed_event(event)

    def _on_document_changed_event(self, event: Any) -> None:
        """Handle document change event."""
        _document_workflow_for(self).on_document_changed_event(event)

    def _on_document_saved_event(self, payload: Any) -> None:
        _document_workflow_for(self).on_document_saved_event(payload)

    def _on_document_save_outcome_event(self, payload: Any) -> None:
        _document_workflow_for(self).on_document_save_outcome_event(payload)

    def _on_document_loaded_event(self, payload: Any) -> None:
        _document_workflow_for(self).on_document_loaded_event(payload)

    def _on_document_created_event(self, payload: Any) -> None:
        _document_workflow_for(self).on_document_created_event(payload)

    def _on_document_closed_event(self, _: Any) -> None:
        _document_workflow_for(self).on_document_closed_event(_)

    def _on_document_save_failed_event(self, payload: Any) -> None:
        _document_workflow_for(self).on_document_save_failed_event(payload)

    def _extract_document_path(self, payload: Any) -> Path | None:
        return MainWindowDocumentWorkflowCoordinator.extract_document_path(payload)

    def _on_history_changed_event(self, payload: Any) -> None:
        """Update undo/redo actions when history stacks change."""
        _core_events_for(self).on_history_changed_event(payload)

    def _on_tool_undo_state_changed(self, available: bool) -> None:
        _core_events_for(self).on_tool_undo_state_changed(available)

    def _on_history_reset_event(self, payload: Any) -> None:
        """Show a brief status message when history is cleared."""
        _core_events_for(self).on_history_reset_event(payload)

    def _build_window_menu(self) -> None:
        """Build Window menu with panel management."""
        _window_menu_for(self).build_window_menu()

    def _add_panel_menu_action(self, panel_id: str, action: QAction) -> None:
        _window_menu_for(self).add_panel_action(panel_id, action)

    def _create_menu_toggle_action(self, definition: MenuDefinition) -> QAction:
        return _window_menu_for(self).create_menu_toggle_action(definition)

    def _on_optional_menu_toggle(self, definition: MenuDefinition, enabled: bool) -> None:
        _window_menu_for(self).on_optional_menu_toggle(definition, enabled)

    def _reset_layout(self) -> None:
        """Reset window layout to the registered default dock arrangement."""
        _dock_layout_for(self).reset_layout()

    def _reset_to_bundled_initial_layout(self) -> bool:
        return _dock_layout_for(self).reset_to_bundled_initial_layout()

    def _persist_reset_layout(self) -> None:
        try:
            self._save_window_state()
        except Exception:
            logger.warning("Failed to persist reset layout", exc_info=True)

    def _reset_to_registered_default_layout(self) -> None:
        _dock_layout_for(self).reset_to_registered_default_layout()

    def showEvent(self, event: QShowEvent) -> None:
        """Verify menu bar visibility on window show."""
        super().showEvent(event)
        _qt_event_shell_for(self).handle_show_event()

    def closeEvent(self, event: QCloseEvent) -> None:
        """Handle window close event with unsaved-changes confirmation.

        §12-C #6 Phase D (2026-04-11): when the user selects *Save* on
        the unsaved-changes prompt and the async save path is
        available, the window is kept open (``event.ignore()``) while
        a background ``save_async`` task runs with a modal progress
        dialog (:meth:`_try_start_close_save_async`). On success the
        window is closed via :meth:`close` which re-fires this
        handler — this time ``_closing_in_progress`` is ``True`` so
        the prompt is skipped and cleanup proceeds. On failure the
        dirty flag is kept set and the flag is cleared so the user
        can retry. When the async path is unavailable (third-party
        DocumentService / async manager not ready) the handler falls
        back to the synchronous :meth:`_perform_save`.
        """
        _shutdown_for(self).close_event(event, base_close_event=super().closeEvent)

    def _disconnect_event_bus_subscriptions(self) -> None:
        """Disconnect EventBus subscriptions owned by this MainWindow."""
        _core_events_for(self).disconnect_event_bus_subscriptions()

    def _handle_mcp_runtime_setting_changed(self, key: str, value: Any) -> None:
        _mcp_runtime_for(self).handle_setting_side_effect(key, value)

    def _schedule_mcp_runtime_shutdown(self) -> None:
        _mcp_runtime_for(self).schedule_shutdown()

    async def shutdown_mcp_runtime(self) -> None:
        await _mcp_runtime_for(self).shutdown()

    # Layer panel handlers
    def _on_layer_visibility_changed(self, index: int, visible: bool) -> None:
        """Handle layer visibility change from panel."""
        _layer_events_for(self).on_layer_visibility_changed(index, visible)

    def _on_layer_locked_changed(self, index: int, locked: bool) -> None:
        """Handle layer lock change from panel."""
        _layer_events_for(self).on_layer_locked_changed(index, locked)

    def _on_layer_opacity_changed(self, index: int, opacity: float) -> None:
        """Handle layer opacity change from panel."""
        _layer_events_for(self).on_layer_opacity_changed(index, opacity)

    def _on_layer_selected(self, index: int) -> None:
        """Handle layer selection from panel."""
        _layer_events_for(self).on_layer_selected(index)

    # ---- ID-based handlers (Phase 9b) ----

    def _on_layer_visibility_changed_by_id(self, layer_id: str, visible: bool) -> None:
        _layer_events_for(self).on_layer_visibility_changed_by_id(layer_id, visible)

    def _on_layer_locked_changed_by_id(self, layer_id: str, locked: bool) -> None:
        _layer_events_for(self).on_layer_locked_changed_by_id(layer_id, locked)

    def _on_layer_alpha_lock_changed_by_id(self, layer_id: str, alpha_locked: bool) -> None:
        _layer_events_for(self).on_layer_alpha_lock_changed_by_id(layer_id, alpha_locked)

    def _on_layer_opacity_changed_by_id(self, layer_id: str, opacity: float) -> None:
        _layer_events_for(self).on_layer_opacity_changed_by_id(layer_id, opacity)

    def _on_layer_selected_by_id(self, layer_id: str) -> None:
        _layer_events_for(self).on_layer_selected_by_id(layer_id)

    def _on_layer_deleted_by_id(self, layer_id: str) -> None:
        _layer_events_for(self).on_layer_deleted_by_id(layer_id)

    def _on_layer_added(self) -> None:
        """Handle layer added signal from layer panel."""
        _layer_events_for(self).on_layer_added()

    def _on_mask_layer_added(self) -> None:
        """Handle mask layer added signal — adds a layer mask to the selected layer."""
        _layer_events_for(self).on_mask_layer_added()

    def _on_layer_selected_from_bus(self, payload: Mapping[str, Any] | Any) -> None:
        """Sync panel selection without full rebuild (preserves scroll)."""
        _layer_events_for(self).on_layer_selected_from_bus(payload)

    def _on_layer_event_from_bus(self, payload: Mapping[str, Any] | Any) -> None:
        """Refresh layer panel when any external layer event occurs.

        Deduplicates events with the same timestamp to avoid redundant
        refreshes when _publish_layer_event publishes to multiple buses.

        For paint/erase content changes only the affected layer's thumbnail is
        updated (no full tree rebuild) to avoid blocking the main thread for
        hundreds of milliseconds between brush strokes.
        """
        _layer_events_for(self).on_layer_event_from_bus(payload)

    def _on_layer_deleted(self, ui_index: int) -> None:
        """Handle layer deleted signal from layer panel.

        Args:
            ui_index: Index from UI (top layer = 0)
        """
        _layer_events_for(self).on_layer_deleted(ui_index)

    def _on_layer_reordered(self, ui_from: int, ui_to: int) -> None:
        """Handle layer reorder gestures from the layer panel."""
        _layer_events_for(self).on_layer_reordered(ui_from, ui_to)

    def _apply_preference_changes(self, changes: dict[str, Any]) -> None:
        self._settings_applier.apply_preference_changes(changes)

    def _handle_setting_side_effects(
        self,
        key: str,
        value: Any,
        *,
        source: str | None = None,
    ) -> None:
        """Delegate immediate settings effects while preserving MainWindow surface.

        Routing kept for compatibility checks:
        - performance/vector_overlay_enabled -> _apply_vector_overlay_enabled
        - performance/vector_overlay_max_dim -> _apply_vector_overlay_max_dim
        - performance/vector_overlay_debug_timing -> _apply_vector_overlay_debug_timing
        """
        self._settings_applier.handle_setting_side_effects(key, value, source=source)

    def _apply_recompose_fps(self, value: Any) -> None:
        """Apply recompose FPS limit to canvas service."""
        self._settings_applier.apply_recompose_fps(value)

    def _apply_vector_overlay_enabled(self, value: Any) -> None:
        """SSO Phase 1: CanvasWidget の overlay 有効/無効を切り替える."""
        # set_overlay_enabled is applied by MainWindowSettingsApplier.
        self._settings_applier.apply_vector_overlay_enabled(value)

    def _apply_vector_overlay_max_dim(self, value: Any) -> None:
        """SSO Phase 1: effective_dpr cap 値を切り替える (2048/4096/8192)."""
        # set_overlay_max_dim is applied by MainWindowSettingsApplier.
        self._settings_applier.apply_vector_overlay_max_dim(value)

    def _apply_vector_overlay_debug_timing(self, value: Any) -> None:
        """SSO Phase 1b: PD-3 計測の ON/OFF (Q-3 実測用)."""
        # set_overlay_debug_timing is applied by MainWindowSettingsApplier.
        self._settings_applier.apply_vector_overlay_debug_timing(value)

    def _apply_language_setting(self) -> None:
        _language_for(self).apply_language_setting()

    def _apply_theme_setting(self, value: Any) -> None:
        self._settings_applier.apply_theme_setting(value)

    def _set_language(self, language: str) -> None:
        _language_for(self).set_language(language)

    def _emit_language_changed(self, language: str) -> None:
        _language_for(self).emit_language_changed(language)

    @staticmethod
    def _menu_id_from_setting(key: str) -> str | None:
        return MainWindowSettingsApplier.menu_id_from_setting(key)

    def _emit_menu_visibility_changed(
        self,
        menu_id: str,
        enabled: bool,
        source: str | None,
    ) -> None:
        _menu_telemetry_for(self).emit_menu_visibility_changed(menu_id, enabled, source)

    def _record_menu_visibility(self, menu_id: str, enabled: bool, source: str | None) -> None:
        _menu_telemetry_for(self).record_menu_visibility(menu_id, enabled, source)

    def _menu_telemetry_records_snapshot(self) -> list[MenuTelemetryRecord]:
        return _menu_telemetry_for(self).records_snapshot()

    def _load_menu_telemetry_from_disk(self) -> list[MenuTelemetryRecord]:
        return _menu_telemetry_for(self).load_from_disk()

    def _on_menu_telemetry_dialog_closed(self, _: int) -> None:
        _menu_telemetry_for(self).on_dialog_closed(_)

    def _apply_canvas_setting(self, key: str, value: Any) -> None:
        """Apply canvas-specific settings to the active canvas service."""
        self._settings_applier.apply_canvas_setting(key, value)

    def _on_layer_blend_mode_changed(self, payload: Mapping[str, Any]) -> None:
        """Handle layer blend mode change from layer panel.

        Args:
            payload: Event payload containing 'mode' and either 'layer_id' or 'index'
        """
        _layer_events_for(self).on_layer_blend_mode_changed(payload)
