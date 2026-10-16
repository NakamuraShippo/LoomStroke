"""Generation provider protocol for AI generation plugins.

Each AI generation plugin implements this protocol to integrate with
the common generation history gallery.  The protocol enables:

- ``build_record``: construct a GenerationRecord from job results
- ``regenerate``: re-submit a previous generation from its snapshot
- ``restore_to_panel``: restore snapshot parameters to the plugin's UI
- ``fetch_result_image``: download the result image from the service
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from loomstroke.core.generation_record import GenerationRecord


@runtime_checkable
class GenerationProvider(Protocol):
    """Contract that AI generation plugins implement for history integration."""

    @property
    def service_id(self) -> str:
        """Unique plugin identifier (e.g. ``"comfyui"``)."""
        ...

    @property
    def service_name(self) -> str:
        """Human-readable display name (e.g. ``"ComfyUI"``)."""
        ...

    def build_record(
        self,
        job_id: str,
        job_result: Any,
        submission_context: dict[str, Any],
    ) -> GenerationRecord:
        """Build a GenerationRecord when a job completes.

        Args:
            job_id: Job identifier.
            job_result: Service-specific result payload.
            submission_context: Context saved at submission time.

        Returns:
            Fully populated GenerationRecord.
        """
        ...

    async def regenerate(self, record: GenerationRecord) -> str | None:
        """Re-submit a generation from its ``service_snapshot``.

        Returns:
            New job_id on success, ``None`` on failure.
        """
        ...

    def restore_to_panel(self, record: GenerationRecord) -> bool:
        """Restore ``service_snapshot`` parameters to the plugin's panel UI.

        Returns:
            ``True`` on success.
        """
        ...

    async def fetch_result_image(self, record: GenerationRecord) -> bytes | None:
        """Fetch the result image bytes from the service.

        Called once at job completion time so the HistoryStore can persist
        the original image and generate a thumbnail.  After local save,
        subsequent reads use the local file.

        Returns:
            PNG bytes or ``None`` if unavailable.
        """
        ...
