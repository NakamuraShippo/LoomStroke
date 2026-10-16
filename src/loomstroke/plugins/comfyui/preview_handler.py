"""Preview handler for ComfyUI generation results.

Manages the preview → confirm workflow for AI-generated images
following the Master.md architecture.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from loomstroke.core.event_bus import EventBus
from loomstroke.core.events import Events
from loomstroke.plugins.sdk.base import JobResult

if TYPE_CHECKING:
    from .event_bridge import ComfyUIEventBridge

logger = logging.getLogger(__name__)


@dataclass
class PreviewAction:
    """Action available for a preview."""

    id: str
    label: str
    icon: str | None = None
    tooltip: str | None = None
    callback: Callable[[str], None] | None = None


class ComfyUIPreviewHandler(QObject):
    """Handles preview → confirm workflow for ComfyUI generations.

    This handler manages the lifecycle of AI-generated previews,
    providing UI integration for confirmation and rejection.
    """

    # Signals for UI updates
    preview_ready = Signal(str, object)  # job_id, JobResult
    preview_adjusted = Signal(str, dict)  # job_id, adjustments

    def __init__(
        self,
        event_bus: EventBus,
        event_bridge: ComfyUIEventBridge,
        parent: QWidget | None = None,
    ) -> None:
        """Initialize preview handler.

        Args:
            event_bus: Event bus for communication
            event_bridge: Event bridge for command generation
            parent: Optional parent widget
        """
        super().__init__(parent)
        self._event_bus = event_bus
        self._event_bridge = event_bridge
        self._active_previews: dict[str, JobResult] = {}
        self._preview_widgets: dict[str, QWidget] = {}

        # Subscribe to preview events
        event_bus.subscribe(Events.AI_PREVIEW_READY, self._on_preview_ready)

    def _on_preview_ready(self, data: dict[str, Any]) -> None:
        """Handle preview ready event.

        Args:
            data: Event data with job_id and result
        """
        job_id = data["job_id"]
        result = data["result"]

        # Store preview
        self._active_previews[job_id] = result

        # Emit signal for UI
        self.preview_ready.emit(job_id, result)

        logger.info(f"Preview ready for job {job_id}")

    def show_preview(
        self,
        result: JobResult,
        parent: QWidget | None = None,
    ) -> str:
        """Display preview with action buttons.

        Args:
            result: Job result to preview
            parent: Optional parent widget

        Returns:
            Job ID for tracking
        """
        job_id = result.job_id

        # Store preview
        self._active_previews[job_id] = result

        # Create preview widget (delegated to UI layer)

        if parent:
            # Get or create preview panel
            preview_panel = self._get_preview_panel(parent)

            # Display preview
            preview_panel.show_ai_preview(
                job_id=job_id,
                image_data=result.data,
                metadata=result.metadata,
                actions=self._get_preview_actions(job_id),
            )

            # Store widget reference
            self._preview_widgets[job_id] = preview_panel

        return job_id

    def _get_preview_panel(self, parent: QWidget) -> Any:
        """Get or create preview panel.

        Args:
            parent: Parent widget

        Returns:
            Preview panel instance
        """
        # This would typically get the existing preview panel
        # from the main window's dock system
        from loomstroke.ui.panels.preview import PreviewPanel  # noqa: PLC0415

        # For now, return a placeholder
        # In real implementation, this would integrate with the dock manager
        return PreviewPanel(parent)

    def _get_preview_actions(self, _job_id: str) -> list[PreviewAction]:
        """Get available actions for a preview.

        Args:
            job_id: Job ID

        Returns:
            List of available actions
        """
        return []

    def on_adjust(
        self,
        job_id: str,
        adjustments: dict[str, Any] | None = None,
    ) -> None:
        """Apply explicit preview adjustments.

        Args:
            job_id: Job ID to adjust
            adjustments: Adjustment parameters. Dialog-based editing is not available.
        """
        result = self._active_previews.get(job_id)
        if not result:
            logger.warning(f"No preview found for job {job_id}")
            return

        if not adjustments:
            logger.warning(
                "ComfyUI preview adjustment dialog is not available; "
                "call on_adjust() with explicit adjustments instead."
            )
            return

        # Apply adjustments directly
        self._event_bridge.adjust_preview(job_id, adjustments)

        # Emit adjustment signal
        self.preview_adjusted.emit(job_id, adjustments)

    def _cleanup_preview(self, job_id: str) -> None:
        """Clean up preview resources.

        Args:
            job_id: Job ID to clean up
        """
        # Remove from active previews
        if job_id in self._active_previews:
            del self._active_previews[job_id]

        # Remove widget reference
        if job_id in self._preview_widgets:
            del self._preview_widgets[job_id]

    def get_active_previews(self) -> dict[str, JobResult]:
        """Get all active previews.

        Returns:
            Dictionary of job_id to JobResult
        """
        return self._active_previews.copy()

    def clear_all_previews(self) -> None:
        """Clear all active previews."""
        job_ids = list(self._active_previews.keys())
        for job_id in job_ids:
            self._cleanup_preview(job_id)

    def cleanup(self) -> None:
        """Clean up all resources."""
        self.clear_all_previews()
        self._event_bus.unsubscribe(Events.AI_PREVIEW_READY, self._on_preview_ready)
