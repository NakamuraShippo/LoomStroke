"""ComfyUI implementation of the GenerationProvider protocol.

Handles building generation records from ComfyUI job results,
re-generating from saved snapshots, and restoring parameters to the panel.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import TYPE_CHECKING, Any

from loomstroke.core.generation_record import GenerationRecord

if TYPE_CHECKING:
    from .extended_service import ExtendedComfyUIService
    from .ui.main_panel import ComfyUIMainPanel

logger = logging.getLogger(__name__)


class ComfyUIGenerationProvider:
    """GenerationProvider implementation for ComfyUI."""

    def __init__(
        self,
        service: ExtendedComfyUIService,
        panel: ComfyUIMainPanel | None = None,
    ) -> None:
        self._service = service
        self._panel = panel

    # ------------------------------------------------------------------
    # Protocol properties
    # ------------------------------------------------------------------

    @property
    def service_id(self) -> str:
        return "comfyui"

    @property
    def service_name(self) -> str:
        return "ComfyUI"

    # ------------------------------------------------------------------
    # Panel binding (set after panel creation)
    # ------------------------------------------------------------------

    def set_panel(self, panel: ComfyUIMainPanel) -> None:
        self._panel = panel

    # ------------------------------------------------------------------
    # build_record
    # ------------------------------------------------------------------

    def build_record(
        self,
        job_id: str,
        job_result: Any,
        submission_context: dict[str, Any],
    ) -> GenerationRecord:
        """Build a GenerationRecord from a completed ComfyUI job."""
        now_iso = datetime.now().astimezone().isoformat()

        # Determine status
        success = getattr(job_result, "success", True)
        error_text = getattr(job_result, "error", None)
        status = "completed" if success else "failed"

        # Extract common fields from submission_context
        prompt_text = self._extract_prompt(submission_context)
        negative_prompt = self._extract_negative_prompt(submission_context)
        model_name = self._extract_model_name(submission_context)
        seed = self._extract_seed(submission_context)
        width, height = self._extract_dimensions(submission_context)

        # Created timestamp from submission_context or now
        created_at = submission_context.get("created_at", now_iso)

        # Duration
        duration_sec: float | None = None
        if "created_at" in submission_context:
            try:
                start = datetime.fromisoformat(submission_context["created_at"])
                duration_sec = (datetime.now().astimezone() - start).total_seconds()
            except (ValueError, TypeError):
                logger.debug("created_at parse failed", exc_info=True)

        # Build service_snapshot (complete data for re-generation / restoration)
        service_snapshot: dict[str, Any] = {
            "workflow_name": submission_context.get("workflow_name", ""),
            "workflow_json": submission_context.get("workflow_json", {}),
            "workflow_model": submission_context.get("workflow_model", {}),
            "seed_modes": submission_context.get("seed_modes", {}),
        }

        # Attach ComfyUI history response if available
        comfyui_history = getattr(job_result, "metadata", None)
        if isinstance(comfyui_history, dict):
            service_snapshot["comfyui_history"] = comfyui_history

        # Output images info
        output_images = self._extract_output_images(job_result)

        return GenerationRecord(
            job_id=job_id,
            service_id=self.service_id,
            service_name=self.service_name,
            status=status,
            created_at=created_at,
            completed_at=now_iso,
            duration_sec=duration_sec,
            prompt=prompt_text,
            negative_prompt=negative_prompt,
            model_name=model_name,
            seed=seed,
            width=width,
            height=height,
            service_snapshot=service_snapshot,
            output_images=output_images,
            error=error_text,
        )

    # ------------------------------------------------------------------
    # regenerate
    # ------------------------------------------------------------------

    async def regenerate(self, record: GenerationRecord) -> str | None:
        """Re-submit a generation from the saved snapshot."""
        snapshot = record.service_snapshot
        workflow_model_data = snapshot.get("workflow_model")
        if not workflow_model_data:
            logger.warning("No workflow_model in snapshot for regeneration")
            return None

        try:
            from .workflow_model import WorkflowModel  # noqa: PLC0415

            workflow = WorkflowModel.from_json(workflow_model_data)

            # Re-apply seed modes if the panel is available
            if self._panel and hasattr(self._panel, "_apply_seed_modes"):
                self._panel._apply_seed_modes(workflow)

            job_id = await self._service.submit_workflow(workflow)
            return job_id

        except Exception:
            logger.exception("Failed to regenerate from snapshot")
            return None

    # ------------------------------------------------------------------
    # restore_to_panel
    # ------------------------------------------------------------------

    def restore_to_panel(self, record: GenerationRecord) -> bool:
        """Restore snapshot parameters to the ComfyUI panel."""
        if not self._panel:
            logger.warning("No panel attached for restore_to_panel")
            return False

        snapshot = record.service_snapshot
        workflow_model_data = snapshot.get("workflow_model")
        if not workflow_model_data:
            logger.warning("No workflow_model in snapshot for restoration")
            return False

        try:
            import asyncio  # noqa: PLC0415

            from .workflow_model import WorkflowModel  # noqa: PLC0415

            workflow = WorkflowModel.from_json(workflow_model_data)
            self._panel.current_workflow = workflow

            # Update node tabs (async) - schedule on event loop
            seed_modes = snapshot.get("seed_modes", {})

            async def _apply() -> None:
                try:
                    await self._panel.update_node_tabs(workflow)
                    if (
                        seed_modes
                        and hasattr(self._panel, "node_tabs")
                        and self._panel.node_tabs
                        and hasattr(self._panel.node_tabs, "set_seed_modes")
                    ):
                        self._panel.node_tabs.set_seed_modes(seed_modes)
                except Exception:
                    logger.exception("Failed to update node tabs during restore")

            try:
                loop = asyncio.get_event_loop()
                loop.create_task(_apply())  # noqa: RUF006
            except RuntimeError:
                logger.warning("No event loop for restore_to_panel")

            # Switch to Workflow Nodes tab
            if hasattr(self._panel, "content_tabs"):
                self._panel.content_tabs.setCurrentIndex(0)

            return True

        except Exception:
            logger.exception("Failed to restore snapshot to panel")
            return False

    # ------------------------------------------------------------------
    # fetch_result_image
    # ------------------------------------------------------------------

    async def fetch_result_image(self, record: GenerationRecord) -> bytes | None:
        """Fetch the result image from ComfyUI server."""
        output_images = record.output_images
        if not output_images:
            # Try to get from ComfyUI history
            return await self._fetch_from_history(record.job_id)

        first = output_images[0]
        filename = first.get("filename", "")
        subfolder = first.get("subfolder", "")
        folder_type = first.get("type", "output")

        if not filename:
            return None

        try:
            connection = getattr(self._service, "_connection", None)
            if connection is None:
                logger.warning("No connection available to fetch image")
                return None
            return await connection.get_image(filename, subfolder, folder_type)
        except Exception:
            logger.exception("Failed to fetch result image for %s", record.job_id)
            return None

    async def _fetch_from_history(self, job_id: str) -> bytes | None:
        """Fallback: fetch output image from ComfyUI /history endpoint."""
        connection = getattr(self._service, "_connection", None)
        if connection is None:
            logger.debug("_fetch_from_history: no connection for %s", job_id)
            return None

        try:
            history = await connection.get_history(job_id)
            if not history:
                logger.debug("_fetch_from_history: empty history for %s", job_id)
                return None

            # Parse history response for output images
            job_data = history.get(job_id, {})
            outputs = job_data.get("outputs", {})
            if not outputs:
                logger.debug(
                    "_fetch_from_history: no outputs for %s (keys=%s)",
                    job_id,
                    list(job_data.keys()),
                )
                return None
            for node_id, node_output in outputs.items():
                if not isinstance(node_output, dict):
                    continue
                images = node_output.get("images", [])
                if images:
                    img = images[0]
                    filename = img.get("filename", "")
                    if not filename:
                        continue
                    logger.debug(
                        "_fetch_from_history: fetching %s from node %s for %s",
                        filename,
                        node_id,
                        job_id,
                    )
                    return await connection.get_image(
                        filename,
                        img.get("subfolder", ""),
                        img.get("type", "output"),
                    )
            logger.debug(
                "_fetch_from_history: no images found in %d output nodes for %s",
                len(outputs),
                job_id,
            )
        except Exception:
            logger.exception("Failed to fetch image from history for %s", job_id)

        return None

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    def _extract_prompt(self, ctx: dict[str, Any]) -> str | None:
        """Extract positive prompt from workflow."""
        return self._search_workflow_inputs(ctx, ("text", "positive"), ("CLIPTextEncode",))

    def _extract_negative_prompt(self, ctx: dict[str, Any]) -> str | None:
        """Extract negative prompt from workflow."""
        return self._search_workflow_inputs(
            ctx, ("text",), ("CLIPTextEncode",), match_key="negative"
        )

    def _search_workflow_inputs(
        self,
        ctx: dict[str, Any],
        input_names: tuple[str, ...],
        class_types: tuple[str, ...],
        match_key: str | None = None,
    ) -> str | None:
        """Search workflow_json for a text input value in specific node types."""
        wf = ctx.get("workflow_json", {})
        if not isinstance(wf, dict):
            return None
        for node_id, node in wf.items():
            if not isinstance(node, dict):
                continue
            class_type = node.get("class_type", "")
            if class_type not in class_types:
                continue
            inputs = node.get("inputs", {})
            if not isinstance(inputs, dict):
                continue
            # If match_key specified, check that this node connects to a "negative" input
            if match_key and match_key not in str(node_id).lower():
                continue
            for name in input_names:
                val = inputs.get(name)
                if isinstance(val, str) and val.strip():
                    return val
        return None

    def _extract_model_name(self, ctx: dict[str, Any]) -> str | None:
        wf = ctx.get("workflow_json", {})
        if not isinstance(wf, dict):
            return None
        for node in wf.values():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") in ("CheckpointLoaderSimple", "CheckpointLoader"):
                inputs = node.get("inputs", {})
                ckpt = inputs.get("ckpt_name")
                if isinstance(ckpt, str):
                    return ckpt
        return None

    def _extract_seed(self, ctx: dict[str, Any]) -> int | None:
        wf = ctx.get("workflow_json", {})
        if not isinstance(wf, dict):
            return None
        for node in wf.values():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") == "KSampler":
                inputs = node.get("inputs", {})
                seed = inputs.get("seed")
                if seed is not None:
                    try:
                        return int(seed)
                    except (TypeError, ValueError):
                        logger.debug("seed int parse failed", exc_info=True)
        return None

    def _extract_dimensions(self, ctx: dict[str, Any]) -> tuple[int | None, int | None]:
        wf = ctx.get("workflow_json", {})
        if not isinstance(wf, dict):
            return None, None
        for node in wf.values():
            if not isinstance(node, dict):
                continue
            if node.get("class_type") == "EmptyLatentImage":
                inputs = node.get("inputs", {})
                w = inputs.get("width")
                h = inputs.get("height")
                try:
                    return int(w), int(h)
                except (TypeError, ValueError):
                    logger.debug("latent dimensions parse failed", exc_info=True)
        return None, None

    def _extract_output_images(self, job_result: Any) -> list[dict[str, Any]]:
        """Extract output_images from job result metadata."""
        metadata = getattr(job_result, "metadata", None) or {}
        if not isinstance(metadata, dict):
            return []

        images: list[dict[str, Any]] = []
        outputs = metadata.get("outputs", {})
        for node_output in outputs.values():
            if not isinstance(node_output, dict):
                continue
            for img in node_output.get("images", []):
                if isinstance(img, dict) and img.get("filename"):
                    images.append(img)
        return images
