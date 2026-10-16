"""High-level QImage runner for SAM3 AI Select."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .metadata_scrub import scrub_sam3_metadata
from .provider import Sam3MaskProvider
from .selection_run import Sam3SelectionRunResult, Sam3SelectionRunStatus, run_selection_request
from .selection_source_snapshot import (
    Sam3SelectionSourceSnapshot,
    create_selection_source_snapshot_from_qimage,
)
from .selection_tool_settings import Sam3AiSelectRunConfig, Sam3AiSelectToolSettings

SAM3_AI_SELECT_IMAGE_RUN_SCHEMA_VERSION = "sam3.ai_select_image_run.v1"


@dataclass(frozen=True, slots=True)
class Sam3AiSelectImageRunResult:
    """Complete UI-independent result for one QImage-backed AI Select run."""

    source_snapshot: Sam3SelectionSourceSnapshot
    run_config: Sam3AiSelectRunConfig
    selection_result: Sam3SelectionRunResult
    schema_version: str = SAM3_AI_SELECT_IMAGE_RUN_SCHEMA_VERSION

    @property
    def completed(self) -> bool:
        return self.selection_result.completed

    @property
    def status(self) -> Sam3SelectionRunStatus:
        return self.selection_result.status

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "completed": self.completed,
            "status": self.status,
            "sourceSnapshot": self.source_snapshot.to_dict(),
            "runConfig": self.run_config.to_dict(),
            "selectionResult": self.selection_result.to_dict(),
        }


async def run_ai_select_from_qimage(
    provider: Sam3MaskProvider,
    *,
    image: object,
    request_id: str,
    settings: Sam3AiSelectToolSettings,
    snapshot_id: str | None = None,
    document_id: str | None = None,
    page_id: str | None = None,
    layer_id: str | None = None,
    dpi: int | None = None,
    available_layer_ids: tuple[str, ...] = (),
    metadata: Mapping[str, Any] | None = None,
) -> Sam3AiSelectImageRunResult:
    """Create a source snapshot and run AI Select against a provider."""

    run_config = settings.to_run_config()
    run_config.prompt_spec.to_prompt()
    run_metadata = _merge_run_metadata(run_config.metadata, metadata)

    source_snapshot = create_selection_source_snapshot_from_qimage(
        image,
        asset_store=provider.asset_store,
        scope=run_config.scope,
        snapshot_id=snapshot_id,
        document_id=document_id,
        page_id=page_id,
        layer_id=layer_id,
        dpi=dpi,
        available_layer_ids=available_layer_ids,
        metadata=run_metadata,
    )
    selection_result = await run_selection_request(
        provider,
        request_id=request_id,
        context=source_snapshot.context,
        prompt_spec=run_config.prompt_spec,
        operation=run_config.operation,
        overlay_options=run_config.overlay_options,
        max_candidates=run_config.max_candidates,
        min_score=run_config.min_score,
        mask_threshold=run_config.mask_threshold,
        split_disconnected_instances=run_config.split_disconnected_instances,
        auto_mask_cleanup=run_config.auto_mask_cleanup,
        metadata=run_metadata,
    )
    return Sam3AiSelectImageRunResult(
        source_snapshot=source_snapshot,
        run_config=run_config,
        selection_result=selection_result,
    )


def _merge_run_metadata(
    settings_metadata: Mapping[str, Any],
    caller_metadata: Mapping[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(scrub_sam3_metadata(dict(settings_metadata)))
    settings_tool_fields = {
        key: merged[key] for key in ("showAllInstances", "toolMode", "toolScope") if key in merged
    }
    merged.update(scrub_sam3_metadata(dict(caller_metadata or {})))
    merged.update(settings_tool_fields)
    return merged


__all__ = [
    "SAM3_AI_SELECT_IMAGE_RUN_SCHEMA_VERSION",
    "Sam3AiSelectImageRunResult",
    "run_ai_select_from_qimage",
]
