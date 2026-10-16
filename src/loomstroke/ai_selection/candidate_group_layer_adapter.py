"""Adapter from SAM3 candidates to a grouped foreground layer command."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

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

    from loomstroke.core.commands import AddLayerCommand  # noqa: PLC0415

    command_group_name = str(group_name or "").strip() or "SAM3 Candidates"

    class _Sam3CandidateGroupToLayerGroupCommand(AddLayerCommand):
        def __init__(self) -> None:
            super().__init__(command_group_name, layer_type="group")
            self._extract_commands = list(extract_commands)
            self._created_layer_ids: tuple[str, ...] = ()
            self._created_group_layer_id: str | None = None

        @property
        def created_layer_ids(self) -> tuple[str, ...]:
            return self._created_layer_ids

        @property
        def created_group_layer_id(self) -> str | None:
            return self._created_group_layer_id

        def apply(self, canvas: object) -> bool:
            applied_commands: list[Any] = []
            created_layer_ids: list[str] = []
            for command in self._extract_commands:
                if not command.apply(canvas):
                    _rollback_extract_commands(canvas, applied_commands)
                    self._clear_created_state()
                    return False
                created_layer_id = _command_created_layer_id(command)
                if created_layer_id is None:
                    _rollback_extract_commands(canvas, [*applied_commands, command])
                    self._clear_created_state()
                    return False
                applied_commands.append(command)
                created_layer_ids.append(created_layer_id)

            group_layers = getattr(canvas, "group_layers", None)
            if not callable(group_layers):
                _rollback_extract_commands(canvas, applied_commands)
                self._clear_created_state()
                return False
            group_layer_id = str(group_layers(created_layer_ids, command_group_name) or "").strip()
            if not group_layer_id:
                _rollback_extract_commands(canvas, applied_commands)
                self._clear_created_state()
                return False

            self._created_layer_ids = tuple(created_layer_ids)
            self._created_group_layer_id = group_layer_id
            self._layer_id = group_layer_id
            return True

        def revert(self, canvas: object) -> bool:
            if self._created_group_layer_id is None or not self._created_layer_ids:
                return False

            ungroup_layer = getattr(canvas, "ungroup_layer", None)
            if not callable(ungroup_layer):
                return False
            if not bool(ungroup_layer(self._created_group_layer_id)):
                return False

            for command in reversed(self._extract_commands):
                if not command.revert(canvas):
                    return False
            self._clear_created_state()
            return True

        def _clear_created_state(self) -> None:
            self._created_layer_ids = ()
            self._created_group_layer_id = None
            self._layer_id = None

    return _Sam3CandidateGroupToLayerGroupCommand()


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
