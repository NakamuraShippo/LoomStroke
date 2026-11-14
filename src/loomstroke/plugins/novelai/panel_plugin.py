"""Dock registration for the NovelAI panel."""

from __future__ import annotations

import asyncio
import logging

from PySide6.QtWidgets import QWidget

from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.ui import PanelDescriptor, UIPanelPlugin
from loomstroke.ui._dock_safe import is_valid

from .history import record_novelai_image
from .panel import NovelAIPanel
from .service import NovelAIService

logger = logging.getLogger(__name__)


class NovelAIPanelPlugin(UIPanelPlugin):
    metadata = PluginMetadata(
        id="novelai_panel",
        name="NovelAI Panel",
        version="1.0.0",
        description="NovelAI image generation",
        author="LoomStroke Team",
        types=[PluginType.UI_PANEL],
    )

    def __init__(self) -> None:
        super().__init__(self.metadata)
        self._host = None
        self._panel: NovelAIPanel | None = None
        self._service: NovelAIService | None = None
        self._history_store = None
        self._tasks: set[asyncio.Task[None]] = set()

    def descriptor(self) -> PanelDescriptor:
        return PanelDescriptor(
            id="novelai_panel",
            display_name="NovelAI",
            category="ai_tools",
            default_position="right",
            default_size=(360, 540),
        )

    def activate(self, host_api) -> None:  # type: ignore[override]
        self._host = host_api
        get_service = getattr(host_api, "get_service", None)
        self._history_store = get_service("generation_history") if callable(get_service) else None

    def deactivate(self) -> None:  # type: ignore[override]
        for task in self._tasks:
            task.cancel()
        self._tasks.clear()
        if self._service is not None:
            self._service.close()
        self._service = None
        self._history_store = None
        self._panel = None
        self._host = None

    def create_widget(self, parent: QWidget | None = None) -> QWidget:  # type: ignore[override]
        if self._host is None:
            raise RuntimeError("NovelAI panel activated without HostAPI")
        if self._panel is None:
            self._panel = NovelAIPanel(self._host, parent)
            self._service = NovelAIService(self._host, on_image_applied=self._record_to_history)
            self._panel.requested.connect(self._submit)
            self._panel.tagsRequested.connect(self._suggest_tags)
            if self._history_store is not None:
                self._history_store.record_added.connect(self._panel.refresh_variations)
        return self._panel

    def _submit(self, request: dict) -> None:
        if self._service is None or self._panel is None:
            return
        panel = self._panel
        panel.set_busy(True)

        async def runner() -> None:
            try:
                if request.get("variation_job_id") is not None:
                    store = self._history_store
                    record = store.get_record(request["variation_job_id"]) if store else None
                    if record is None or record.service_id != "novelai" or not record.image_path:
                        raise ValueError("The selected Gallery image is no longer available")
                    path = store.resolve_path(record.image_path).resolve()
                    if not path.is_relative_to(store.base_path.resolve()) or not path.is_file():
                        raise ValueError("The selected Gallery image is no longer available")
                    if path.stat().st_size > 64 * 1024 * 1024:
                        raise ValueError("The selected Gallery image is too large")
                    request["source_image_bytes"] = await asyncio.to_thread(path.read_bytes)
                    request["prompt"] = record.prompt
                    request["negative_prompt"] = record.negative_prompt
                    request["model"] = record.model_name
                await self._service.run(request)
                if is_valid(panel):
                    panel.show_status(panel.tr("NovelAI image added to layer"))
            except asyncio.CancelledError:
                if is_valid(panel):
                    panel.show_status(panel.tr("NovelAI generation cancelled"))
                raise
            except Exception as exc:
                logger.warning("NovelAI request failed: %s", type(exc).__name__)
                if is_valid(panel):
                    panel.show_status(str(exc))
            finally:
                if is_valid(panel):
                    panel.set_busy(False)
                self._tasks.discard(asyncio.current_task())

        task = asyncio.create_task(runner())
        self._tasks.add(task)

    async def _record_to_history(self, request: dict, image: bytes, index: int, count: int) -> None:
        store = self._history_store
        if store is None:
            return
        try:
            await record_novelai_image(store, request, image, index=index, count=count)
        except Exception:
            logger.exception("Failed to record NovelAI image %s/%s in Gallery", index, count)

    def _suggest_tags(self, query: str, model: str) -> None:
        if self._service is None or self._panel is None:
            return
        panel = self._panel

        async def runner() -> None:
            try:
                tags = await self._service.suggest_tags(query, model)
                if is_valid(panel):
                    panel.show_status(", ".join(tags) if tags else panel.tr("No tags found"))
            except Exception as exc:
                logger.warning("NovelAI tag lookup failed: %s", type(exc).__name__)
                if is_valid(panel):
                    panel.show_status(str(exc))
            finally:
                self._tasks.discard(asyncio.current_task())

        task = asyncio.create_task(runner())
        self._tasks.add(task)


def create_plugin() -> NovelAIPanelPlugin:
    return NovelAIPanelPlugin()
