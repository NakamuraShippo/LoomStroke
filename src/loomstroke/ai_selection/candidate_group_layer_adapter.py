"""Adapter from SAM3 candidates to a grouped foreground layer command."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from .assets import Sam3AssetStore
from .cut_to_layer_adapter import (
    Sam3ExtractLayerAdapterError,
    build_extract_to_new_layer_command,
)

if TYPE_CHECKING:
    from loomstroke.core.commands import AddLayerCommand


class Sam3CandidateGroupLayerAdapterError(Sam3ExtractLayerAdapterError):
    """Raised when SAM3 candidates cannot be converted to a layer group command."""


def build_candidate_group_to_layer_group_command(
    asset_store: Sam3AssetStore,
    candidates: Sequence[object],
    *,
    source_layer_id: str,
    canvas_width: int,
    canvas_height: int,
    group_name: str | None = None,
) -> AddLayerCommand:
    """Build a command that extracts candidate foregrounds into one layer group."""

    items = _candidate_items(candidates)
    if len(items) < 2:
        raise Sam3CandidateGroupLayerAdapterError(
            "candidate_multi_selection_missing",
            "SAM3 candidate layer group requires at least two candidates.",
        )

    extract_commands = [
        build_extract_to_new_layer_command(
            asset_store,
            item["mask_asset_id"],
            source_layer_id=source_layer_id,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
            mode="foreground",
            layer_name=_candidate_layer_name(item["label"], index),
        )
        for index, item in enumerate(items, start=1)
    ]

    from .candidate_group_command import _Sam3CandidateGroupToLayerGroupCommand  # noqa: PLC0415

    command_group_name = str(group_name or "").strip() or "SAM3 Candidates"

    return _Sam3CandidateGroupToLayerGroupCommand(command_group_name, extract_commands)


def _candidate_items(candidates: Sequence[object]) -> tuple[dict[str, str], ...]:
    items: list[dict[str, str]] = []
    for index, candidate in enumerate(candidates, start=1):
        candidate_id = _candidate_text(candidate, "candidate_id")
        if not candidate_id:
            raise Sam3CandidateGroupLayerAdapterError(
                "candidate_unavailable",
                "SAM3 candidate layer group candidate is not available.",
            )
        mask_asset_id = _candidate_text(candidate, "mask_asset_id")
        if not mask_asset_id:
            raise Sam3CandidateGroupLayerAdapterError(
                "candidate_mask_missing",
                "SAM3 candidate layer group candidate has no mask asset.",
            )
        label = _candidate_text(candidate, "label") or f"Candidate {index}"
        items.append(
            {
                "candidate_id": candidate_id,
                "mask_asset_id": mask_asset_id,
                "label": label,
            }
        )
    return tuple(items)


def _candidate_text(candidate: object, key: str) -> str:
    value = candidate.get(key) if isinstance(candidate, Mapping) else getattr(candidate, key, None)
    return str(value or "").strip()


def _candidate_layer_name(label: str, index: int) -> str:
    clean_label = " ".join(str(label or "").split())
    if not clean_label:
        clean_label = f"Candidate {int(index)}"
    return f"SAM3 {clean_label}"


def _command_created_layer_id(command: object) -> str | None:
    created_layer_id = str(getattr(command, "created_layer_id", "") or "").strip()
    return created_layer_id or None


def _rollback_extract_commands(canvas: object, commands: Sequence[object]) -> None:
    for command in reversed(commands):
        revert = getattr(command, "revert", None)
        if callable(revert):
            revert(canvas)


__all__ = [
    "Sam3CandidateGroupLayerAdapterError",
    "build_candidate_group_to_layer_group_command",
]
