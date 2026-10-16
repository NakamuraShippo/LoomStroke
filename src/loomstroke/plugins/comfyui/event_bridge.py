"""Event to Command bridge for ComfyUI integration.

Converts AI job events to canvas commands following the Master.md
architecture where plugins generate Commands for the host to apply.
"""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

import numpy as np

from loomstroke.core.commands import ApplyImageCommand, Command
from loomstroke.core.event_bus import EventBus
from loomstroke.core.events import Events, JobResultEvent
from loomstroke.plugins.sdk.base import JobResult

if TYPE_CHECKING:
    from loomstroke.core.canvas import Canvas

logger = logging.getLogger(__name__)


class ComfyUIEventBridge:
    """Converts ComfyUI job events to Commands for canvas application.

    This bridge subscribes to AI job events and generates appropriate
    Commands that can be applied to the canvas through the host API.
    """

    def __init__(
        self,
        event_bus: EventBus,
        canvas: Canvas | None = None,
    ) -> None:
        """Initialize event bridge.

        Args:
            event_bus: Event bus for subscribing to job events
            canvas: Optional canvas reference for command submission
        """
        self._event_bus = event_bus
        self._canvas = canvas
        self._pending_previews: dict[str, JobResult] = {}
        self._subscriptions: list[tuple[str, Any, Any]] = []

        # Subscribe to AI job events
        self._subscribe(event_bus, Events.AI_JOB_RESULT, self._on_job_result)
        self._subscribe(event_bus, Events.AI_JOB_PROGRESS, self._on_job_progress)

    def _subscribe(self, bus: EventBus, event: str, handler: Any) -> None:
        """Register handler and retain strong references for weakref-based bus."""
        token = bus.subscribe(event, handler)
        self._subscriptions.append((event, token, handler))

    def set_canvas(self, canvas: Canvas) -> None:
        """Set canvas reference for command submission.

        Args:
            canvas: Canvas instance
        """
        self._canvas = canvas

    def _on_job_result(self, event: JobResultEvent) -> None:
        """Handle job completion event.

        Args:
            event: Job result event
        """
        if not event.success or not event.result:
            logger.warning(f"Job {event.job_id} failed: {event.error}")
            return

        # Store result for preview
        self._pending_previews[event.job_id] = event.result

        # Emit preview ready event
        self._event_bus.emit(
            Events.AI_PREVIEW_READY,
            {
                "job_id": event.job_id,
                "result": event.result,
            },
        )

    def _on_job_progress(self, event: Any) -> None:
        """Handle job progress event.

        Args:
            event: Job progress event
        """
        # Progress events are handled by UI components
        # This is here for potential future enhancements
        pass

    def confirm_preview(
        self,
        job_id: str,
        position: tuple[int, int] | None = None,
        blend_mode: str = "normal",
        opacity: float = 1.0,
    ) -> Command | None:
        """Confirm a preview and generate apply command.

        Args:
            job_id: Job ID of preview to confirm
            position: Optional position to place image at
            blend_mode: Blend mode for application
            opacity: Opacity for application

        Returns:
            ApplyImageCommand if preview exists, None otherwise
        """
        result = self._pending_previews.get(job_id)
        if not result or result.output_type != "image":
            logger.warning(f"No image preview found for job {job_id}")
            return None

        # Convert image data to numpy array
        image_data = self._decode_image(result.data)
        if image_data is None:
            logger.error(f"Failed to decode image for job {job_id}")
            return None

        # Create apply command
        command = ApplyImageCommand(
            image=image_data,
            position=position or (0, 0),
            blend_mode=blend_mode,
            opacity=opacity,
            metadata={
                "source": "ComfyUI",
                "job_id": job_id,
                "operation": result.metadata.get("operation", "unknown"),
            },
        )

        # Submit to canvas if available
        if self._canvas:
            self._canvas.submit(command, merge_key=f"ai_{job_id}")

        # Clean up preview
        del self._pending_previews[job_id]

        # Emit confirmation event
        self._event_bus.emit(
            Events.AI_GENERATION_APPLIED,
            {
                "job_id": job_id,
                "position": position,
                "blend_mode": blend_mode,
            },
        )

        return command

    def reject_preview(self, job_id: str) -> None:
        """Reject a preview and clean up.

        Args:
            job_id: Job ID of preview to reject
        """
        if job_id in self._pending_previews:
            del self._pending_previews[job_id]

            # Emit rejection event
            self._event_bus.emit(
                Events.AI_GENERATION_REJECTED,
                {"job_id": job_id},
            )

            logger.info(f"Preview for job {job_id} rejected")

    def adjust_preview(
        self,
        job_id: str,
        adjustments: dict[str, Any],
    ) -> Command | None:
        """Adjust a preview before application.

        Args:
            job_id: Job ID of preview to adjust
            adjustments: Adjustment parameters

        Returns:
            Command with adjustments if preview exists
        """
        result = self._pending_previews.get(job_id)
        if not result:
            logger.warning(f"No preview found for job {job_id}")
            return None

        # Apply adjustments to the result
        # This could involve color correction, scaling, etc.
        adjusted_data = self._apply_adjustments(
            result.data,
            adjustments,
        )

        # Update stored preview
        result.data = adjusted_data

        # Emit adjustment event
        self._event_bus.emit(
            Events.AI_PREVIEW_ADJUSTED,
            {
                "job_id": job_id,
                "adjustments": adjustments,
            },
        )

        return None  # Adjustments don't create commands directly

    def _decode_image(self, data: Any) -> np.ndarray | None:
        """Decode image data to numpy array.

        Args:
            data: Image data (bytes, base64, or array)

        Returns:
            Numpy array or None if decoding fails
        """
        import base64  # noqa: PLC0415
        from io import BytesIO  # noqa: PLC0415

        from PIL import Image  # noqa: PLC0415

        try:
            if isinstance(data, bytes):
                # Direct bytes
                image = Image.open(BytesIO(data))
            elif isinstance(data, str):
                # Base64 encoded
                decoded = base64.b64decode(data)
                image = Image.open(BytesIO(decoded))
            elif isinstance(data, np.ndarray):
                # Already numpy array
                return data
            else:
                logger.error(f"Unknown image data type: {type(data)}")
                return None

            # Convert to RGBA numpy array
            if image.mode != "RGBA":
                image = image.convert("RGBA")

            return np.array(image)

        except Exception as e:
            logger.error(f"Failed to decode image: {e}")
            return None

    def _apply_adjustments(
        self,
        data: Any,
        adjustments: dict[str, Any],
    ) -> Any:
        """Apply adjustments to image data.

        Args:
            data: Original image data
            adjustments: Adjustment parameters

        Returns:
            Adjusted image data
        """
        # Decode to numpy
        image = self._decode_image(data)
        if image is None:
            return data

        # Apply adjustments
        if "brightness" in adjustments:
            factor = adjustments["brightness"]
            image = np.clip(image * factor, 0, 255).astype(np.uint8)

        if "contrast" in adjustments:
            factor = adjustments["contrast"]
            mean = image.mean()
            image = np.clip((image - mean) * factor + mean, 0, 255).astype(np.uint8)

        if "saturation" in adjustments:
            # Convert to HSV, adjust S, convert back
            from PIL import Image  # noqa: PLC0415

            pil_image = Image.fromarray(image)
            if pil_image.mode == "RGBA":
                # Separate alpha
                r, g, b, a = pil_image.split()
                rgb = Image.merge("RGB", (r, g, b))
                hsv = rgb.convert("HSV")
                h, s, v = hsv.split()

                # Adjust saturation
                factor = adjustments["saturation"]
                s = s.point(lambda x: min(255, x * factor))

                hsv = Image.merge("HSV", (h, s, v))
                rgb = hsv.convert("RGB")
                r, g, b = rgb.split()
                pil_image = Image.merge("RGBA", (r, g, b, a))

            image = np.array(pil_image)

        # Encode back to bytes
        from io import BytesIO  # noqa: PLC0415

        from PIL import Image  # noqa: PLC0415

        pil_image = Image.fromarray(image)
        buffer = BytesIO()
        pil_image.save(buffer, format="PNG")
        return buffer.getvalue()

    def get_pending_previews(self) -> dict[str, JobResult]:
        """Get all pending previews.

        Returns:
            Dictionary of job_id to JobResult
        """
        return self._pending_previews.copy()

    def clear_previews(self) -> None:
        """Clear all pending previews."""
        self._pending_previews.clear()
        logger.info("All pending previews cleared")

    def cleanup(self) -> None:
        """Clean up resources and unsubscribe from events."""
        for event, token, _handler in self._subscriptions:
            with contextlib.suppress(Exception):
                self._event_bus.off(event, token)
        self._subscriptions.clear()
        self._pending_previews.clear()
