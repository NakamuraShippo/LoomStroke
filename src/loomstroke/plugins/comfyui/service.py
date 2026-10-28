"""ComfyUI AIServicePlugin implementation.

This module implements the ComfyUI service as an AIServicePlugin following
the plugin SDK contract defined in Master.md.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import AsyncIterator, Mapping
from typing import Any
from urllib.parse import urlparse

from loomstroke.core.event_bus import EventBus, get_event_bus
from loomstroke.core.events import (
    Events,
    JobProgressEvent,
    JobResultEvent,
)
from loomstroke.platform.settings_manager import get_settings_manager
from loomstroke.platform.settings_schema import DEFAULT_COMFYUI_PORT
from loomstroke.plugins.common.outbound_policy import (
    build_and_validate_comfyui_base_url,
    ensure_network_services_consent,
)
from loomstroke.plugins.sdk.base import (
    AIServicePlugin,
    JobResult,
    JobSpec,
    ProgressEvent,
    ServiceDescriptor,
)

from .async_connection import AsyncComfyUIConnection as ComfyUIConnection
from .job_cancellation import _JobTaskCleanup
from .job_connection import (
    _cancel_job,
    _get_job_result,
    _job_connection,
    _JobConnection,
    _JobConnectionError,
    _stream_progress,
)

logger = logging.getLogger(__name__)


class ComfyUIService(AIServicePlugin):
    """ComfyUI service implementation as AIServicePlugin.

    Provides text2img, img2img, inpaint, and upscale capabilities
    through the ComfyUI backend.
    """

    def __init__(self, event_bus: EventBus | None = None) -> None:
        """Initialize ComfyUI service.

        Args:
            event_bus: Optional event bus for job lifecycle events
        """
        self._connection: ComfyUIConnection | None = None
        self._event_bus = event_bus or get_event_bus()
        self._settings = get_settings_manager()
        self._active_jobs: dict[str, asyncio.Task[None]] = {}
        self._job_prompt_ids: dict[str, str] = {}

    def _resolve_prompt_id(self, job_id: str) -> str:
        """Resolve internal prompt id for a public job id."""
        return self._job_prompt_ids.get(job_id, job_id)

    def _cleanup_job_tracking(self, job_id: str) -> None:
        """Remove internal mappings for finished jobs."""
        self._job_prompt_ids.pop(job_id, None)
        if job_id in self._active_jobs:
            del self._active_jobs[job_id]

    @staticmethod
    def _normalize_host_port_from_url(raw_url: str) -> tuple[str | None, int | None]:
        """Parse host/port from a base URL string."""

        normalized = raw_url.strip()
        if not normalized:
            return None, None

        candidate = normalized
        parsed = urlparse(candidate if "://" in candidate else f"//{candidate}")
        if parsed.hostname:
            host = parsed.hostname
            port = parsed.port or DEFAULT_COMFYUI_PORT
            return host, port

        candidate = candidate.split("/", 1)[0].strip()
        if not candidate:
            return None, None

        if candidate.startswith("["):
            closing = candidate.find("]")
            if closing != -1:
                host = candidate[1:closing]
                remainder = candidate[closing + 1 :]
                if remainder.startswith(":") and remainder[1:].isdigit():
                    return host, int(remainder[1:])
                return host, DEFAULT_COMFYUI_PORT

        if ":" in candidate:
            host_part, port_part = candidate.rsplit(":", 1)
            if port_part.isdigit():
                return host_part, int(port_part)

        return candidate, DEFAULT_COMFYUI_PORT

    @classmethod
    def _resolve_connection_settings(cls, settings: Any) -> tuple[str | None, int | None, bool]:
        local_url = settings.get("comfyui.local_url")
        if isinstance(local_url, str) and local_url.strip():
            parsed_host, parsed_port = cls._normalize_host_port_from_url(local_url)
            if parsed_host is not None:
                parsed_port = parsed_port or DEFAULT_COMFYUI_PORT
                parsed_ssl = local_url.strip().lower().startswith("https://")
                return parsed_host, int(parsed_port), parsed_ssl

        host = settings.get("comfyui.host")
        port = settings.get("comfyui.port")
        use_ssl = settings.get("comfyui.use_ssl", False)

        if host and port:
            return str(host), int(port), bool(use_ssl)

        base_url = settings.get("connection/base_url")
        if isinstance(base_url, str):
            parsed_host, parsed_port = cls._normalize_host_port_from_url(base_url)
            if parsed_host is not None:
                parsed_port = parsed_port or DEFAULT_COMFYUI_PORT
                parsed_ssl = base_url.strip().lower().startswith("https://")
                return parsed_host, int(parsed_port), parsed_ssl

        return None, None, bool(use_ssl)

    def descriptor(self) -> ServiceDescriptor:
        """Return service descriptor with capabilities."""
        return ServiceDescriptor(
            name="ComfyUI",
            capabilities={"text2img", "img2img", "inpaint", "upscale", "controlnet"},
            max_concurrency=3,
            max_resolution=(4096, 4096),
            supports_streaming=True,
        )

    async def initialize(self) -> None:
        """Initialize connection to ComfyUI backend.

        Note: This method now only prepares the connection object.
        Actual connection is established when connect() is called explicitly.
        """
        if not ensure_network_services_consent(self._settings, service_name="ComfyUI"):
            if self._connection is not None:
                with contextlib.suppress(Exception):
                    if self._connection.is_connected():
                        await self._connection.disconnect()
                self._connection = None
            return

        from .server_registry import get_registry  # noqa: PLC0415
        from .session_target import bind_service  # noqa: PLC0415

        await bind_service(self, get_registry())
        if self._connection is None:
            from loomstroke.platform import get_async_manager  # noqa: PLC0415

            # Per Master.md Section 6.7: 接続先は QSettings、デフォルト埋め込み禁止
            host, port, use_ssl = self._resolve_connection_settings(self._settings)

            # Validate required settings
            if not host or not port:
                raise ValueError(
                    "ComfyUI host and port must be configured in settings. "
                    "Please configure connection settings first."
                )

            base_url = build_and_validate_comfyui_base_url(
                str(host),
                port,
                bool(use_ssl),
                service_name="ComfyUI",
            )

            # Get async manager
            async_manager = get_async_manager()

            # Create connection object but don't connect yet
            # Connection will be established when user clicks Connect button
            self._connection = ComfyUIConnection(async_manager=async_manager, base_url=base_url)
            # Don't auto-connect - wait for explicit connect() call
            # await self._connection.connect()  # Removed - manual connection only

    async def connect(self) -> None:
        """Explicitly connect to ComfyUI backend.

        This method should be called when user clicks Connect button.
        """
        if not ensure_network_services_consent(self._settings, service_name="ComfyUI"):
            return

        await self.initialize()

        if self._connection and not self._connection.is_connected():
            await self._connection.connect()
        if self._connection and self._connection.is_connected():
            from .server_registry import get_registry  # noqa: PLC0415
            from .session_target import confirm  # noqa: PLC0415

            confirm(get_registry(), self._connection.base_url)

    async def health(self) -> bool:
        """Check if ComfyUI backend is healthy and reachable.

        Returns:
            True if service is healthy, False otherwise
        """
        if not ensure_network_services_consent(self._settings, service_name="ComfyUI"):
            return False
        if self._connection is None:
            return False

        try:
            # Check system stats endpoint
            response = await self._connection.get_system_stats()
            return response is not None
        except Exception:
            return False

    async def submit(self, spec: JobSpec) -> str:
        """Submit a job to ComfyUI backend.

        Args:
            spec: Job specification with operation and parameters

        Returns:
            Unique job ID for tracking
        """
        if not ensure_network_services_consent(self._settings, service_name="ComfyUI"):
            raise RuntimeError(
                "ComfyUI access is disabled. "
                "Set 'privacy/allow_external_services' to true to enable."
            )

        await self.initialize()

        # Generate unique job ID
        job_id = str(uuid.uuid4())

        # Convert JobSpec to ComfyUI workflow
        workflow = self._build_workflow(spec)

        # Queue the prompt
        prompt_id = await self._connection.queue_prompt(workflow, client_id=job_id)
        self._job_prompt_ids[job_id] = prompt_id or job_id

        # Start streaming task
        if self._event_bus:
            task = asyncio.create_task(self._stream_job(job_id, prompt_id))
            self._active_jobs[job_id] = task

        return job_id

    async def stream(self, job_id: str) -> AsyncIterator[ProgressEvent]:
        """Stream progress events for a job.

        Args:
            job_id: Job ID to stream progress for

        Yields:
            Progress events as they occur
        """
        if not ensure_network_services_consent(self._settings, service_name="ComfyUI"):
            raise RuntimeError(
                "ComfyUI access is disabled. "
                "Set 'privacy/allow_external_services' to true to enable."
            )

        await self.initialize()

        async for event in _stream_progress(self, job_id):
            yield event

    async def cancel(self, job_id: str) -> bool:
        """Cancel a running job.

        Args:
            job_id: Job ID to cancel

        Returns:
            True if cancelled successfully
        """
        return await _cancel_job(self, job_id)

    async def result(self, job_id: str) -> JobResult:
        """Get the final result of a completed job.

        Args:
            job_id: Job ID to get result for

        Returns:
            Job result
        """
        if not ensure_network_services_consent(self._settings, service_name="ComfyUI"):
            return JobResult(
                job_id=job_id,
                success=False,
                error="ComfyUI access is disabled. "
                "Set 'privacy/allow_external_services' to true to enable.",
            )

        await self.initialize()

        result = await self._connection.get_result(self._resolve_prompt_id(job_id))
        if result:
            output = await self._extract_history_result_image(result)
            return JobResult(
                job_id=job_id,
                success=True,
                output=output,
                metadata=result.get("metadata", {}),
            )
        # Return empty result if not found
        return JobResult(
            job_id=job_id,
            success=False,
            output=None,
            error="Job not found or not completed",
            metadata={},
        )

    async def get_result(self, job_id: str) -> JobResult | None:
        """Get the final result of a completed job.

        Args:
            job_id: Job ID to get result for

        Returns:
            Job result if available, None otherwise
        """
        if not ensure_network_services_consent(self._settings, service_name="ComfyUI"):
            return None

        await self.initialize()

        return await _get_job_result(self, job_id)

    async def _extract_history_result_image(
        self, result: dict[str, Any], *, _job: _JobConnection | None = None
    ) -> bytes | None:
        """Extract generated image bytes from ComfyUI history result.

        Args:
            result: History response entry

        Returns:
            Image bytes if available, otherwise None.
        """
        if not _job_connection(self, _job):
            return None

        outputs = result.get("outputs")
        if not isinstance(outputs, dict):
            return None

        def _iter_output_items(output: Any) -> list[Any]:
            items: list[Any] = []
            if isinstance(output, dict):
                images = output.get("images")
                if isinstance(images, list) and images:
                    items.extend(images)
                    return items

                items.append(output)
                return items

            if isinstance(output, list):
                items.extend(output)
                return items

            if output:
                items.append(output)
            return items

        def _to_candidate(
            output_item: Any, fallback: dict[str, Any] | None = None
        ) -> tuple[str, str, str] | None:
            if fallback is None:
                fallback = {}

            if isinstance(output_item, str):
                return (
                    output_item,
                    str(fallback.get("subfolder", "")),
                    str(fallback.get("type", "output")),
                )

            if not isinstance(output_item, dict):
                return None

            # Nested image metadata
            if "filename" in output_item or "name" in output_item:
                filename = output_item.get("filename") or output_item.get("name")
                if isinstance(filename, str):
                    return (
                        filename,
                        str(output_item.get("subfolder", fallback.get("subfolder", ""))),
                        str(output_item.get("type", fallback.get("type", "output"))),
                    )

            images = output_item.get("images")
            if isinstance(images, list) and images:
                return _to_candidate(images[0], output_item)

            return None

        candidate: tuple[str, str, str] | None = None
        for _output in outputs.values():
            for item in _iter_output_items(_output):
                candidate = _to_candidate(item, _output if isinstance(_output, dict) else None)
                if candidate is not None:
                    break
            if candidate is not None:
                break

        if candidate is None:
            return None

        filename, subfolder, folder_type = candidate
        try:
            return await _job_connection(self, _job).get_image(
                filename=filename,
                subfolder=subfolder,
                folder_type=folder_type,
            )
        except _JobConnectionError:
            raise
        except Exception as exc:
            logger.error("Failed to fetch result image from history entry: %s", exc)
            return None

    def _build_workflow(self, spec: JobSpec) -> dict[str, Any]:
        """Build ComfyUI workflow from job specification.

        Args:
            spec: Job specification

        Returns:
            ComfyUI workflow dictionary
        """
        op = spec.op
        params = spec.params

        if op == "text2img":
            return self._build_text2img_workflow(params)
        elif op == "img2img":
            return self._build_img2img_workflow(params)
        elif op == "inpaint":
            return self._build_inpaint_workflow(params)
        elif op == "upscale":
            return self._build_upscale_workflow(params)
        else:
            raise ValueError(f"Unsupported operation: {op}")

    def _build_text2img_workflow(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Build text2img workflow."""
        return {
            "3": {
                "class_type": "KSampler",
                "inputs": {
                    "seed": params.get("seed", -1),
                    "steps": params.get("steps", 20),
                    "cfg": params.get("cfg", 8.0),
                    "sampler_name": params.get("sampler", "euler"),
                    "scheduler": params.get("scheduler", "normal"),
                    "denoise": 1.0,
                    "model": ["4", 0],
                    "positive": ["6", 0],
                    "negative": ["7", 0],
                    "latent_image": ["5", 0],
                },
            },
            "4": {
                "class_type": "CheckpointLoaderSimple",
                "inputs": {
                    "ckpt_name": params.get("model", "sd_xl_base_1.0.safetensors"),
                },
            },
            "5": {
                "class_type": "EmptyLatentImage",
                "inputs": {
                    "width": params.get("width", 512),
                    "height": params.get("height", 512),
                    "batch_size": 1,
                },
            },
            "6": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "text": params.get("prompt", ""),
                    "clip": ["4", 1],
                },
            },
            "7": {
                "class_type": "CLIPTextEncode",
                "inputs": {
                    "text": params.get("negative_prompt", ""),
                    "clip": ["4", 1],
                },
            },
            "8": {
                "class_type": "VAEDecode",
                "inputs": {
                    "samples": ["3", 0],
                    "vae": ["4", 2],
                },
            },
            "9": {
                "class_type": "SaveImage",
                "inputs": {
                    "images": ["8", 0],
                    "filename_prefix": "ComfyUI",
                },
            },
        }

    def _build_img2img_workflow(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Build img2img workflow."""
        workflow = self._build_text2img_workflow(params)
        # Replace EmptyLatentImage with LoadImage + VAEEncode
        workflow["5"] = {
            "class_type": "LoadImage",
            "inputs": {
                "image": params.get("image"),
            },
        }
        workflow["10"] = {
            "class_type": "VAEEncode",
            "inputs": {
                "pixels": ["5", 0],
                "vae": ["4", 2],
            },
        }
        workflow["3"]["inputs"]["latent_image"] = ["10", 0]
        workflow["3"]["inputs"]["denoise"] = params.get("denoise", 0.75)
        return workflow

    def _build_inpaint_workflow(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Build inpaint workflow."""
        workflow = self._build_img2img_workflow(params)
        # Add mask handling
        workflow["11"] = {
            "class_type": "LoadImageMask",
            "inputs": {
                "image": params.get("mask"),
                "channel": "alpha",
            },
        }
        workflow["12"] = {
            "class_type": "SetLatentNoiseMask",
            "inputs": {
                "samples": ["10", 0],
                "mask": ["11", 0],
            },
        }
        workflow["3"]["inputs"]["latent_image"] = ["12", 0]
        return workflow

    def _build_upscale_workflow(self, params: Mapping[str, Any]) -> dict[str, Any]:
        """Build upscale workflow."""
        return {
            "1": {
                "class_type": "LoadImage",
                "inputs": {
                    "image": params.get("image"),
                },
            },
            "2": {
                "class_type": "UpscaleModelLoader",
                "inputs": {
                    "model_name": params.get("model", "RealESRGAN_x4plus.pth"),
                },
            },
            "3": {
                "class_type": "ImageUpscaleWithModel",
                "inputs": {
                    "upscale_model": ["2", 0],
                    "image": ["1", 0],
                },
            },
            "4": {
                "class_type": "SaveImage",
                "inputs": {
                    "images": ["3", 0],
                    "filename_prefix": "Upscaled",
                },
            },
        }

    async def _stream_job(self, job_id: str, prompt_id: str) -> None:
        """Stream job progress and emit events.

        Args:
            job_id: Job ID
            prompt_id: ComfyUI prompt ID
        """
        cleanup = _JobTaskCleanup.capture(self, job_id)
        try:
            async for event in self.stream(job_id):
                if self._event_bus:
                    self._event_bus.emit(
                        Events.AI_JOB_PROGRESS,
                        JobProgressEvent(
                            job_id=job_id,
                            step=event.step,
                            total=event.total,
                            message=event.message,
                            preview_data=event.preview_png,
                        ),
                    )

                    # Check if complete
                    is_complete = event.total > 0 and event.step >= event.total
                    if is_complete:
                        result = await self.get_result(job_id)
                        # Fetch raw history entry for image extraction
                        raw_history = None
                        if self._connection:
                            raw_history = await self._connection.get_result(
                                self._resolve_prompt_id(job_id)
                            )
                        if self._event_bus:
                            self._event_bus.emit(
                                Events.AI_JOB_RESULT,
                                JobResultEvent(
                                    job_id=job_id,
                                    success=True,
                                    result=result,
                                    result_data=raw_history,
                                    error=None,
                                ),
                            )
                        return
        except asyncio.CancelledError:
            # Job was cancelled
            pass
        except Exception as e:
            if self._event_bus:
                self._event_bus.emit(
                    Events.AI_JOB_RESULT,
                    JobResultEvent(
                        job_id=job_id,
                        success=False,
                        result=None,
                        error=str(e),
                    ),
                )
        finally:
            # Clean up
            cleanup.finish_stream()

    async def cleanup(self) -> None:
        """Clean up resources."""
        # Cancel all active jobs
        for task in self._active_jobs.values():
            task.cancel()
        self._active_jobs.clear()

        # Close connection
        if self._connection:
            await self._connection.disconnect()
            self._connection = None


_JOB_CONNECTION_DEFAULTS = {
    name: getattr(ComfyUIService, name)
    for name in (
        "initialize",
        "connect",
        "stream",
        "cancel",
        "get_result",
        "_extract_history_result_image",
        "_resolve_prompt_id",
        "_cleanup_job_tracking",
    )
}

_JOB_TRACKING_CLEANUP_DEFAULT = ComfyUIService._cleanup_job_tracking
