"""UI panel plugin wrapper for NanoBanana."""

from __future__ import annotations

import asyncio
import logging

from PySide6.QtWidgets import QWidget

from loomstroke.plugins.sdk.base import JobSpec, PluginMetadata, PluginType
from loomstroke.plugins.sdk.ui import PanelDescriptor, UIPanelPlugin
from loomstroke.ui.translations import translated_literal_domain

from .service import get_service_instance
from .ui.panel import NanoBananaPanel

logger = logging.getLogger(__name__)


def _generate_thumbnail(image_bytes: bytes) -> bytes | None:
    """Decode image and generate a 128px WEBP thumbnail (thread-safe, no QPixmap)."""
    from PySide6.QtCore import QBuffer, QIODevice, Qt  # noqa: PLC0415
    from PySide6.QtGui import QImage  # noqa: PLC0415

    qimg = QImage()
    qimg.loadFromData(image_bytes)
    if qimg.isNull():
        return None
    thumb = qimg.scaled(
        128,
        128,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    buf = QBuffer()
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    thumb.save(buf, "WEBP", 70)
    return bytes(buf.data())


def _ts(text: str) -> str:
    return translated_literal_domain("NanoBananaPanelPlugin", text, use_fallback=True)


class NanoBananaPanelPlugin(UIPanelPlugin):
    """Panel plugin integrating the NanoBanana UI."""

    metadata = PluginMetadata(
        id="nano_banana_panel",
        name=_ts("NanoBanana Panel"),
        version="2.0.0",
        description=_ts("Configure NanoBanana Gemini jobs"),
        author="LoomStroke Team",
        types=[PluginType.UI_PANEL],
    )

    def __init__(self) -> None:
        super().__init__(self.metadata)
        self._host = None
        self._panel: NanoBananaPanel | None = None
        self._service = get_service_instance()
        self._tasks: set[asyncio.Task[None]] = set()

    def descriptor(self) -> PanelDescriptor:
        return PanelDescriptor(
            id="nano_banana_panel",
            display_name=_ts("NanoBanana"),
            category="ai_tools",
            default_position="right",
            default_size=(360, 480),
        )

    def activate(self, host_api) -> None:  # type: ignore[override]  # plugin API signature divergence
        self._host = host_api
        if hasattr(self._service, "activate"):
            self._service.activate(host_api)  # type: ignore[call-arg]  # duck-typed service protocol

        # Register generation provider with history store
        get_service = getattr(host_api, "get_service", None)
        history_store = get_service("generation_history") if callable(get_service) else None
        if history_store is not None:
            from .generation_provider import NanoBananaGenerationProvider  # noqa: PLC0415

            self._generation_provider = NanoBananaGenerationProvider()
            self._history_store = history_store
            history_store.register_provider(self._generation_provider)
            if self._panel is not None:
                self._generation_provider.set_panel(self._panel)
        else:
            self._generation_provider = None
            self._history_store = None

    def deactivate(self) -> None:  # type: ignore[override]  # plugin API signature divergence
        for task in list(self._tasks):
            task.cancel()
        self._tasks.clear()
        self._panel = None
        self._host = None

    def create_widget(self, parent: QWidget | None = None) -> QWidget:  # type: ignore[override]  # plugin API signature divergence
        if not self._host:
            raise RuntimeError("NanoBanana panel activated without host API")
        if self._panel is None:
            self._panel = NanoBananaPanel(self._host, parent=parent)
            self._panel.submitRequested.connect(self._handle_submit)
            # Attach panel to generation provider
            provider = getattr(self, "_generation_provider", None)
            if provider and hasattr(provider, "set_panel"):
                provider.set_panel(self._panel)
        return self._panel

    # ------------------------------------------------------------------
    def _handle_submit(self, spec: JobSpec) -> None:
        if not self._service:
            current_panel = self._panel
            if current_panel:
                current_panel.append_status_translated(
                    "Service unavailable",
                    domain="NanoBananaPanelPlugin",
                )
            return

        from datetime import UTC, datetime  # noqa: PLC0415

        submission_context = {
            "prompt": spec.params.get("prompt", "") if isinstance(spec.params, dict) else "",
            "aspect_ratio": (
                spec.params.get("options", {}).get("aspect_ratio", "1:1")
                if isinstance(spec.params, dict)
                else "1:1"
            ),
            "modality": (
                spec.params.get("options", {}).get("modality", "Text+Image")
                if isinstance(spec.params, dict)
                else "Text+Image"
            ),
            "input_layer_ids": (
                spec.params.get("input_layer_ids", []) if isinstance(spec.params, dict) else []
            ),
            "created_at": datetime.now(tz=UTC).isoformat(),
        }

        async def _runner() -> None:
            try:
                job_id = await self._service.submit(spec)
                current_panel = self._panel
                if current_panel:
                    current_panel.append_status_translated(
                        "Job {job_id} submitted",
                        domain="NanoBananaPanelPlugin",
                        job_id=job_id,
                    )
                async for event in self._service.stream(job_id):
                    current_panel = self._panel
                    if current_panel and event.message:
                        current_panel.append_status_translated(
                            "{step}/{total}: {message}",
                            domain="NanoBananaPanelPlugin",
                            step=event.step,
                            total=event.total,
                            message=event.message,
                        )
                result = await self._service.result(job_id)
                current_panel = self._panel
                if current_panel:
                    if result.success:
                        current_panel.append_status_translated(
                            "Job completed",
                            domain="NanoBananaPanelPlugin",
                        )
                    elif result.error:
                        current_panel.append_status(str(result.error))
                    else:
                        current_panel.append_status_translated(
                            "Job failed",
                            domain="NanoBananaPanelPlugin",
                        )
                # Record to generation history
                await self._record_to_history(job_id, result, submission_context)
            except Exception as exc:  # pragma: no cover - UI reporting path
                logger.exception("NanoBanana job failed")
                current_panel = self._panel
                if current_panel:
                    current_panel.append_status_translated(
                        "Error: {error}",
                        domain="NanoBananaPanelPlugin",
                        error=exc,
                    )
            finally:
                # Remove task from tracking set
                task = asyncio.current_task()
                if task in self._tasks:
                    self._tasks.remove(task)

        task = asyncio.create_task(_runner())
        self._tasks.add(task)

    async def _record_to_history(
        self,
        job_id: str,
        job_result: object,
        submission_context: dict,
    ) -> None:
        """Record a completed job to the generation history store."""
        provider = getattr(self, "_generation_provider", None)
        store = getattr(self, "_history_store", None)
        if not provider or not store:
            return
        try:
            record = provider.build_record(job_id, job_result, submission_context)

            # Save result image if available
            output_bytes = getattr(job_result, "output", None)
            if isinstance(output_bytes, bytes) and output_bytes:
                record.image_path = store.save_image(job_id, output_bytes)
                thumb_bytes = await asyncio.to_thread(_generate_thumbnail, output_bytes)
                if thumb_bytes:
                    record.thumbnail_path = store.save_thumbnail(job_id, thumb_bytes)

            store.add_record(record)
            if record.image_path:
                store.update_record(record)
            logger.info("Recorded NanoBanana generation history for %s", job_id[:8])
        except Exception:
            logger.exception("Failed to record NanoBanana history for %s", job_id)


def create_plugin() -> NanoBananaPanelPlugin:
    return NanoBananaPanelPlugin()
