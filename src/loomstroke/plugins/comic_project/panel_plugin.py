"""Comic Project panel plugin registration (CT-17)."""

from __future__ import annotations

import logging
from typing import Any

from PySide6.QtWidgets import QWidget

from loomstroke.core.comic_project.comic_project_service import ComicProjectService
from loomstroke.core.comic_project.comic_project_store import ComicProjectStore
from loomstroke.host_api import HostAPI
from loomstroke.platform.user_dirs import default_user_root
from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.ui import PanelDescriptor, UIPanelPlugin
from loomstroke.ui.translations import translated_literal_domain

from .comic_project_panel import ComicProjectPanel

logger = logging.getLogger(__name__)

__all__ = ["ComicProjectPanelPlugin", "create_plugin"]


def _ts(text: str) -> str:
    return translated_literal_domain("ComicProjectPanel", text, use_fallback=True)


class ComicProjectPanelPlugin(UIPanelPlugin):
    metadata = PluginMetadata(
        id="comic_project_panel",
        name=_ts("Comic Project Panel"),
        version="0.1.0",
        description=_ts("Story, dialogue and production management"),
        author="LoomStroke Team",
        types=[PluginType.UI_PANEL],
    )

    def __init__(self) -> None:
        super().__init__(self.metadata)
        self._host: HostAPI | None = None
        self._panel: ComicProjectPanel | None = None
        self._service: ComicProjectService | None = None

    def descriptor(self) -> PanelDescriptor:
        return PanelDescriptor(
            id="comic_project_panel",
            display_name=_ts("Comic Project"),
            category="tools",
            default_position="right",
            default_size=(720, 480),
            resizable=True,
        )

    def activate(self, host_api: HostAPI) -> None:  # type: ignore[override]
        super().activate(host_api)
        self._host = host_api

    def deactivate(self) -> None:
        self._panel = None
        self._host = None
        super().deactivate()

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        if self._host is None:
            raise RuntimeError("Host API not set")
        if self._panel is None:
            self._panel = ComicProjectPanel(
                self._host,
                self._service_instance(),
                document_id_provider=self._document_id,
                parent=parent,
            )
        return self._panel

    def _service_instance(self) -> ComicProjectService:
        if self._service is None:
            self._service = ComicProjectService(ComicProjectStore(default_user_root()))
        return self._service

    def _document_id(self) -> str | None:
        document: Any = getattr(self._host, "document", None)
        for name in ("current_document_id", "document_id"):
            value = getattr(document, name, None)
            value = value() if callable(value) else value
            if isinstance(value, str) and value:
                return value
        return None


def create_plugin() -> ComicProjectPanelPlugin:
    return ComicProjectPanelPlugin()
