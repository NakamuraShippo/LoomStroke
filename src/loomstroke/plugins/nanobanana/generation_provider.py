"""NanoBanana implementation of the GenerationProvider protocol."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loomstroke.core.generation_record import GenerationRecord

if TYPE_CHECKING:
    from loomstroke.plugins.sdk.base import JobResult

    from .ui.panel import NanoBananaPanel

logger = logging.getLogger(__name__)


class NanoBananaGenerationProvider:
    """GenerationProvider for NanoBanana (Gemini)."""

    def __init__(self) -> None:
        self._panel: NanoBananaPanel | None = None

    def set_panel(self, panel: NanoBananaPanel) -> None:
        self._panel = panel

    @property
    def service_id(self) -> str:
        return "nanobanana"

    @property
    def service_name(self) -> str:
        return "NanoBanana"

    def build_record(
        self,
        job_id: str,
        job_result: Any,
        submission_context: dict[str, Any],
    ) -> GenerationRecord:
        """Build a GenerationRecord from a completed NanoBanana job."""
        now_iso = datetime.now(tz=UTC).isoformat()

        result: JobResult | None = job_result
        success = getattr(result, "success", False) if result else False
        error_text = getattr(result, "error", None) if result else None
        status = "completed" if success else "failed"

        prompt = submission_context.get("prompt")
        aspect = submission_context.get("aspect_ratio")
        modality = submission_context.get("modality")

        width: int | None = None
        height: int | None = None
        if aspect:
            from .types import ASPECT_TO_RESOLUTION  # noqa: PLC0415

            dims = ASPECT_TO_RESOLUTION.get(aspect)
            if dims:
                width, height = dims

        created_at = submission_context.get("created_at", now_iso)
        duration_sec: float | None = None
        if "created_at" in submission_context:
            try:
                start = datetime.fromisoformat(submission_context["created_at"])
                duration_sec = (datetime.now(tz=UTC) - start).total_seconds()
            except (ValueError, TypeError):
                logger.debug("created_at parse failed", exc_info=True)

        service_snapshot: dict[str, Any] = {
            "prompt": prompt,
            "modality": modality,
            "aspect_ratio": aspect,
            "model": submission_context.get("model"),
            "image_size": submission_context.get("image_size"),
            "thinking_level": submission_context.get("thinking_level"),
            "include_thoughts": submission_context.get("include_thoughts"),
            "google_search": submission_context.get("google_search"),
            "input_layer_ids": submission_context.get("input_layer_ids", []),
        }

        return GenerationRecord(
            job_id=job_id,
            service_id=self.service_id,
            service_name=self.service_name,
            status=status,
            created_at=created_at,
            completed_at=now_iso,
            duration_sec=duration_sec,
            prompt=prompt,
            model_name=submission_context.get("model") or "Gemini",
            width=width,
            height=height,
            service_snapshot=service_snapshot,
            error=error_text,
        )

    async def regenerate(self, record: GenerationRecord) -> str | None:
        """Not supported for NanoBanana."""
        return None

    def restore_to_panel(self, record: GenerationRecord) -> bool:  # noqa: PLR0912
        """Restore saved generation parameters to the NanoBanana panel."""
        if not self._panel:
            logger.warning("No NanoBanana panel attached for restore_to_panel")
            return False

        snapshot = record.service_snapshot
        if not snapshot:
            return False

        try:
            self._restore_combos(snapshot)
            self._restore_checkboxes(snapshot)
            logger.info("Restored NanoBanana snapshot to panel for %s", record.job_id[:8])
            return True
        except Exception:
            logger.exception("Failed to restore NanoBanana snapshot to panel")
            return False

    def _restore_combo(self, attr: str, value: Any) -> None:
        """Restore a combo box selection by data value."""
        combo = getattr(self._panel, attr, None)
        if combo is None or value is None:
            return
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    def _restore_combos(self, snapshot: dict[str, Any]) -> None:
        if snapshot.get("prompt") and hasattr(self._panel, "_prompt_text"):
            self._panel._prompt_text.setPlainText(snapshot["prompt"])
        self._restore_combo("_modality_combo", snapshot.get("modality"))
        self._restore_combo("_aspect_combo", snapshot.get("aspect_ratio"))
        self._restore_combo("_model_combo", snapshot.get("model"))
        self._restore_combo("_image_size_combo", snapshot.get("image_size"))
        self._restore_combo("_thinking_combo", snapshot.get("thinking_level"))

    def _restore_checkboxes(self, snapshot: dict[str, Any]) -> None:
        if hasattr(self._panel, "_include_thoughts_check"):
            self._panel._include_thoughts_check.setChecked(
                bool(snapshot.get("include_thoughts", False))
            )
        if hasattr(self._panel, "_google_search_check"):
            self._panel._google_search_check.setChecked(bool(snapshot.get("google_search", False)))

    async def fetch_result_image(self, record: GenerationRecord) -> bytes | None:
        """NanoBanana results are applied directly; no remote fetch needed."""
        return None
