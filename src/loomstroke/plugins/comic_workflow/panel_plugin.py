"""Comic Workflow panel plugin registration (CT-22)."""

from __future__ import annotations

import logging

from PySide6.QtWidgets import QWidget

from loomstroke.core.comic_project.comic_project_service import ComicProjectService
from loomstroke.core.comic_project.comic_project_store import ComicProjectStore
from loomstroke.host_api import HostAPI
from loomstroke.platform.user_dirs import default_user_root
from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.ui import PanelDescriptor, UIPanelPlugin
from loomstroke.ui.comic_workflow_service import ComicWorkflowService
from loomstroke.ui.translations import translated_literal_domain

from .comic_workflow_panel import ComicWorkflowPanel

logger = logging.getLogger(__name__)

__all__ = ["ComicWorkflowPanelPlugin", "create_plugin"]


def _ts(text: str) -> str:
    return translated_literal_domain("ComicWorkflowPanel", text, use_fallback=True)


class ComicWorkflowPanelPlugin(UIPanelPlugin):
    metadata = PluginMetadata(
        id="comic_workflow_panel",
        name=_ts("Comic Workflow Panel"),
        version="0.1.0",
        description=_ts("Book plan, page plan and editable apply for AI comic production"),
        author="LoomStroke Team",
        types=[PluginType.UI_PANEL],
    )

    def __init__(self) -> None:
        super().__init__(self.metadata)
        self._host: HostAPI | None = None
        self._panel: ComicWorkflowPanel | None = None
        self._service: ComicWorkflowService | None = None

    def descriptor(self) -> PanelDescriptor:
        return PanelDescriptor(
            id="comic_workflow_panel",
            display_name=_ts("Comic Workflow"),
            category="tools",
            default_position="right",
            default_size=(560, 480),
            resizable=True,
        )

    def activate(self, host_api: HostAPI) -> None:  # type: ignore[override]
        super().activate(host_api)
        self._host = host_api

    def deactivate(self) -> None:
        self._panel = None
        self._service = None
        self._host = None
        super().deactivate()

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        if self._host is None:
            raise RuntimeError("Host API not set")
        if self._service is None:
            projects = ComicProjectService(ComicProjectStore(default_user_root()))
            self._service = ComicWorkflowService(self._host, projects)
        if self._panel is None:
            self._panel = ComicWorkflowPanel(self._service, parent=parent)
        return self._panel


def create_plugin() -> ComicWorkflowPanelPlugin:
    return ComicWorkflowPanelPlugin()
