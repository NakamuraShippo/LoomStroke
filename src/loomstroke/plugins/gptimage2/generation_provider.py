"""GPT Image 2 implementation of the GenerationProvider protocol."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from loomstroke.core.generation_record import GenerationRecord

if TYPE_CHECKING:
    from loomstroke.plugins.sdk.base import JobResult

    from .ui.panel import GPTImage2Panel

logger = logging.getLogger(__name__)


def _dimensions_from_size(size: Any) -> tuple[int | None, int | None]:
    if not isinstance(size, str) or "x" not in size:
        return None, None
    left, right = size.split("x", 1)
    try:
        return int(left), int(right)
    except ValueError:
        return None, None


class GPTImage2GenerationProvider:
    """GenerationProvider for GPT Image 2."""

    def __init__(self) -> None:
        self._panel: GPTImage2Panel | None = None

    def set_panel(self, panel: GPTImage2Panel) -> None:
        self._panel = panel

    @property
    def service_id(self) -> str:
        return "gptimage2"

    @property
    def service_name(self) -> str:
        return "GPT Image 2"

    def build_record(
        self,
        job_id: str,
        job_result: Any,
        submission_context: dict[str, Any],
    ) -> GenerationRecord:
        now_iso = datetime.now(tz=UTC).isoformat()

        result: JobResult | None = job_result
        success = getattr(result, "success", False) if result else False
        error_text = getattr(result, "error", None) if result else None
        status = "completed" if success else "failed"

        prompt = submission_context.get("prompt")
        width, height = _dimensions_from_size(submission_context.get("size"))
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
            "input_layer_ids": list(submission_context.get("input_layer_ids") or []),
            "model": submission_context.get("model"),
            "size": submission_context.get("size"),
            "quality": submission_context.get("quality"),
            "output_format": submission_context.get("output_format"),
            "output_compression": submission_context.get("output_compression"),
            "background": submission_context.get("background"),
            "moderation": submission_context.get("moderation"),
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
            model_name=submission_context.get("model") or "GPT Image 2",
            width=width,
            height=height,
            service_snapshot=service_snapshot,
            error=error_text,
        )

    async def regenerate(self, record: GenerationRecord) -> str | None:
        """Not supported for GPT Image 2 yet."""
        del record
        return None

    def restore_to_panel(self, record: GenerationRecord) -> bool:
        if not self._panel:
            logger.warning("No GPT Image 2 panel attached for restore_to_panel")
            return False

        snapshot = record.service_snapshot
        if not snapshot:
            return False

        try:
            if snapshot.get("prompt") and hasattr(self._panel, "_prompt_text"):
                self._panel._prompt_text.setPlainText(snapshot["prompt"])
            self._restore_combo("_model_combo", snapshot.get("model"))
            if hasattr(self._panel, "_input_layers"):
                self._panel._input_layers.set_selected(snapshot.get("input_layer_ids") or [])
            self._restore_combo("_size_combo", snapshot.get("size"))
            self._restore_combo("_quality_combo", snapshot.get("quality"))
            self._restore_combo("_format_combo", snapshot.get("output_format"))
            self._restore_combo("_background_combo", snapshot.get("background"))
            self._restore_combo("_moderation_combo", snapshot.get("moderation"))
            compression = snapshot.get("output_compression")
            if compression is not None and hasattr(self._panel, "_compression_spin"):
                self._panel._compression_spin.setValue(int(compression))
            logger.info("Restored GPT Image 2 snapshot to panel for %s", record.job_id[:8])
            return True
        except Exception:
            logger.exception("Failed to restore GPT Image 2 snapshot to panel")
            return False

    def _restore_combo(self, attr: str, value: Any) -> None:
        combo = getattr(self._panel, attr, None)
        if combo is None or value is None:
            return
        idx = combo.findData(value)
        if idx >= 0:
            combo.setCurrentIndex(idx)

    async def fetch_result_image(self, record: GenerationRecord) -> bytes | None:
        """GPT Image 2 results are applied and stored locally; no remote fetch needed."""
        del record
        return None
