"""Gallery special panel lifecycle coordination for MainWindow."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from PySide6.QtCore import QSize, Qt
from PySide6.QtWidgets import QDockWidget

logger = logging.getLogger(__name__)


class MainWindowGalleryPanelCoordinator:
    """Coordinate the shared generation history Gallery dock."""

    def __init__(
        self,
        window: Any,
        *,
        panel_min_size: QSize,
        message_box: Any,
    ) -> None:
        self._window = window
        self._panel_min_size = panel_min_size
        self._message_box = message_box

    def setup_if_available(self) -> None:
        """Set up the shared generation history gallery panel."""
        window = self._window
        try:
            if window.has_panel("ui.gallery"):
                return
            if window._host_api is None:
                return
            history_store = window._host_api.get_service("generation_history")
            if history_store is None:
                logger.debug("generation_history service not available; skipping Gallery panel")
                return

            from .panels.gallery_panel import GalleryPanel  # noqa: PLC0415

            gallery_widget = GalleryPanel(
                history_store=history_store,
                service_filter=None,
                host_api=window._host_api,
                parent=window,
            )
            gallery_widget.regenerate_requested.connect(
                lambda job_id: window._gallery_regenerate(history_store, job_id)
            )
            gallery_widget.restore_requested.connect(
                lambda job_id: window._gallery_restore_to_panel(history_store, job_id)
            )

            gallery_dock = QDockWidget("Gallery", window)
            gallery_dock.setWidget(gallery_widget)
            gallery_dock.setMinimumSize(self._panel_min_size)
            window.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, gallery_dock)
            window.register_panel(
                "ui.gallery",
                "Gallery",
                gallery_dock,
                default_visible=False,
            )
            logger.info("Gallery panel added successfully")
        except Exception as exc:
            logger.error("Failed to add Gallery panel: %s", exc, exc_info=True)

    def regenerate(self, history_store: Any, job_id: str) -> None:
        """Handle Gallery Regenerate button."""
        record = history_store.get_record(job_id)
        if not record:
            return
        provider = history_store.get_provider(record.service_id)
        if not provider:
            self._message_box.warning(
                self._window,
                self._window.tr("Warning"),
                self._window.tr("No provider available for regeneration"),
            )
            return

        async def _do_regen() -> None:
            try:
                new_id = await provider.regenerate(record)
                if new_id:
                    logger.info("Regenerated job %s -> %s", job_id[:8], new_id[:8])
                else:
                    logger.warning("Regeneration returned no job id for %s", job_id[:8])
            except Exception:
                logger.exception("Regeneration failed for %s", job_id[:8])

        try:
            loop = asyncio.get_event_loop()
            loop.create_task(_do_regen())  # noqa: RUF006
        except RuntimeError:
            logger.warning("No event loop for regeneration")

    def restore_to_panel(self, history_store: Any, job_id: str) -> None:
        """Handle Gallery Copy to Nodes button."""
        record = history_store.get_record(job_id)
        if not record:
            return
        provider = history_store.get_provider(record.service_id)
        if not provider:
            self._message_box.warning(
                self._window,
                self._window.tr("Warning"),
                self._window.tr("No provider available for restoration"),
            )
            return
        if hasattr(provider, "restore_to_panel"):
            ok = provider.restore_to_panel(record)
            if ok:
                logger.info("Restored snapshot to panel for %s", job_id[:8])
                panel_id_map = {
                    "comfyui": "comfyui_panel",
                    "nanobanana": "ui.nano_banana",
                    "gptimage2": "ui.gptimage2",
                }
                panel_id = panel_id_map.get(record.service_id)
                if panel_id:
                    self._window.set_panel_visible(panel_id, True)
