"""Local candidate-group lifecycle; no general history transaction framework."""

from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any

from loomstroke.core.command_layer_access import _resolve_layer_by_id
from loomstroke.core.commands import AddLayerCommand, GroupLayersCommand
from loomstroke.core.group_layer_history import GroupLayerHistory
from loomstroke.core.layer_creation_history import CreatedLayerHistory
from loomstroke.core.layer_history_memory import estimate_retained_layer_bytes

logger = logging.getLogger(__name__)
_MISSING = object()


class _Sam3CandidateGroupToLayerGroupCommand(AddLayerCommand):
    """Retain child/group Commands and stop when local recovery is unconfirmed."""

    def __init__(self, name: str, commands: Sequence[Any]) -> None:
        super().__init__(name, layer_type="group")
        self._extract_commands = list(commands)
        self._group_command: GroupLayersCommand | None = None
        self._stable_child_ids: tuple[str, ...] = ()
        self._created_layer_ids: tuple[str, ...] = ()
        self._created_group_layer_id: str | None = None
        self._active_before: tuple[Any, Any] | None = None
        self._active_after: tuple[Any, Any] | None = None
        self._invalid = False

    @property
    def created_layer_ids(self) -> tuple[str, ...]:
        return self._created_layer_ids

    @property
    def created_group_layer_id(self) -> str | None:
        return self._created_group_layer_id

    @property
    def retained_bytes(self) -> int:
        seen: set[int] = set()
        images: set[int] = set()
        total = estimate_retained_layer_bytes(self._created.layer, seen=seen, image_keys=images)
        if self._group_command is not None:
            group = self._group_command._group_history.group
            total += estimate_retained_layer_bytes(group, seen=seen, image_keys=images)
        commands: set[int] = set()
        for command in self._extract_commands:
            if id(command) in commands:
                continue
            commands.add(id(command))
            custody = getattr(command, "_created", None)
            if custody is None:
                retained = getattr(command, "retained_bytes", 0)
                total += retained if isinstance(retained, int) and retained >= 0 else 0
                continue
            total += estimate_retained_layer_bytes(custody.layer, seen=seen, image_keys=images)
            mask = getattr(command, "_mask_image", None)
            if mask is not None and mask.cacheKey() not in images:
                images.add(mask.cacheKey())
                total += mask.sizeInBytes()
        return total

    @staticmethod
    def _active(canvas: Any) -> tuple[Any, Any]:
        return getattr(canvas, "_active_layer_id", None), getattr(canvas, "_active_index", None)

    @staticmethod
    def _same_context(canvas: Any, roots: Any) -> bool:
        return getattr(canvas, "_layers", _MISSING) is roots

    def _stop(self, reason: str) -> bool:
        self._invalid = True
        logger.warning("SAM candidate group recovery unconfirmed (%s)", reason)
        return False

    @staticmethod
    def _restore_active(canvas: Any, state: tuple[Any, Any]) -> None:
        identifier, index = state
        if identifier is None or _resolve_layer_by_id(canvas, identifier) is None:
            return
        if not callable(getattr(canvas, "_layer_tree", None)):
            activate = getattr(canvas, "set_active_layer", None)
            if callable(activate):
                activate(identifier)
            return
        from loomstroke.core.layer_tree_service import (  # noqa: PLC0415
            LayerTreeQueryService,
            sync_active_markers,
        )

        canvas._active_layer_id = identifier
        if index is not None:
            canvas._active_index = index
        sync_active_markers(canvas, LayerTreeQueryService(canvas))
        canvas._invalidate_layer_cache()
        canvas._recompose()

    def _owned_step(self, canvas: Any, command: Any | None) -> bool:
        return (
            GroupLayerHistory.supports(canvas)
            if command is None
            else CreatedLayerHistory.supports(canvas)
        )

    @staticmethod
    def _layer_relation(canvas: Any, identifier: str | None) -> tuple[Any, ...]:
        layer = _resolve_layer_by_id(canvas, identifier) if identifier else None
        if layer is None:
            return ("absent",)
        location = (
            canvas._find_parent(identifier)
            if callable(getattr(canvas, "_find_parent", None))
            else None
        )
        return (
            id(layer),
            getattr(layer, "parent_id", None),
            location[1] if location else None,
            tuple(id(child) for child in getattr(layer, "children", ())),
        )

    @staticmethod
    def _child_identifier(command: Any) -> str | None:
        payload = getattr(getattr(command, "_created", None), "layer", None)
        return getattr(command, "created_layer_id", None) or getattr(payload, "id", None)

    def _relation(self, canvas: Any, command: Any | None) -> tuple[Any, ...]:
        if command is not None:
            return self._layer_relation(canvas, self._child_identifier(command))
        group = self._group_command
        payload = group._group_history.group if group is not None else None
        identifier = payload.id if payload is not None else getattr(group, "_group_id", None)
        identifiers = self._stable_child_ids or tuple(
            self._child_identifier(child) for child in self._extract_commands
        )
        # A failed group transition can leave its own group absent while a
        # target child is missing or has changed parents. Do not inverse the
        # successful prefix unless these local relationships also survived.
        return self._layer_relation(canvas, identifier), tuple(
            self._layer_relation(canvas, child_id) for child_id in identifiers
        )

    def _group_apply(self, canvas: Any) -> bool:
        identifiers = tuple(
            str(getattr(child, "created_layer_id", "") or "") for child in self._extract_commands
        )
        if (
            len(identifiers) < 2
            or not all(identifiers)
            or len(set(identifiers)) != len(identifiers)
        ):
            return False
        if self._group_command is not None and identifiers != self._stable_child_ids:
            if self._group_command._group_history.group is not None:
                return False
            # Legacy adapters have no reinsertion port. Keep their normal
            # add/group/remove replay contract, without claiming stable IDs.
            self._group_command = None
        if self._group_command is None:
            self._group_command = GroupLayersCommand(list(identifiers), self._name)
            self._stable_child_ids = identifiers
        if not self._group_command.apply(canvas):
            return False
        identifier = str(self._group_command._group_id or "").strip()
        if not identifier:
            return False
        self._group_command._group_id = identifier
        return True

    def _invoke(self, canvas: Any, command: Any | None, apply: bool) -> bool:
        try:
            if command is None:
                if apply:
                    return self._group_apply(canvas)
                return self._group_command is not None and self._group_command.revert(canvas)
            return bool(command.apply(canvas) if apply else command.revert(canvas))
        except Exception as exc:
            logger.warning("SAM candidate step failed (%s)", type(exc).__name__)
            return False

    def _compensate_step(
        self, canvas: Any, roots: Any, command: Any | None, applied: bool
    ) -> str | None:
        if not self._same_context(canvas, roots):
            return "context_changed"
        if not self._owned_step(canvas, command):
            return "legacy_inverse_unconfirmed"
        if not self._invoke(canvas, command, not applied):
            return "inverse_failed"
        return None if self._same_context(canvas, roots) else "context_changed"

    def _recover(
        self, canvas: Any, roots: Any, active: tuple[Any, Any], completed: list[tuple[Any, bool]]
    ) -> bool:
        for command, applied in reversed(completed):
            reason = self._compensate_step(canvas, roots, command, applied)
            if reason is not None:
                return self._stop(reason)
        try:
            self._restore_active(canvas, active)
        except Exception as exc:
            logger.warning("SAM candidate active recovery failed (%s)", type(exc).__name__)
            return self._stop("active_recovery_failed")
        if not self._same_context(canvas, roots):
            return self._stop("context_changed")
        return False  # The requested operation failed; recovery is not a successful Undo/Redo.

    def _perform_steps(
        self, canvas: Any, roots: Any, active: tuple[Any, Any], *, apply: bool
    ) -> list[tuple[Any, bool]] | None:
        steps = (
            [(command, True) for command in self._extract_commands] + [(None, True)]
            if apply
            else [(None, False)]
            + [(command, False) for command in reversed(self._extract_commands)]
        )
        completed: list[tuple[Any, bool]] = []
        for command, direction in steps:
            if not self._same_context(canvas, roots):
                self._stop("context_changed")
                return None
            before = self._relation(canvas, command)
            success = self._invoke(canvas, command, direction)
            if not self._same_context(canvas, roots):
                self._stop("context_changed")
                return None
            if not success:
                if (
                    not self._owned_step(canvas, command)
                    or self._relation(canvas, command) != before
                ):
                    self._stop("failed_step_changed_relations")
                else:
                    self._recover(canvas, roots, active, completed)
                return None
            completed.append((command, direction))
        return completed

    def _run(self, canvas: Any, *, apply: bool) -> bool:
        if self._invalid:
            return False
        roots, active = getattr(canvas, "_layers", _MISSING), self._active(canvas)
        completed = self._perform_steps(canvas, roots, active, apply=apply)
        if completed is None:
            return False
        desired = self._active_after if apply else self._active_before
        if desired is not None:
            try:
                self._restore_active(canvas, desired)
            except Exception:
                return self._recover(canvas, roots, active, completed)
        if not self._same_context(canvas, roots):
            return self._stop("context_changed")
        if apply:
            self._active_before = self._active_before or active
            self._active_after = self._active_after or self._active(canvas)
            self._created_layer_ids = self._stable_child_ids
            self._created_group_layer_id = self._group_command._group_id
            self._layer_id = self._created_group_layer_id
        else:
            self._created_layer_ids = ()
            self._created_group_layer_id = self._layer_id = None
        self._executed = apply
        return True

    def apply(self, canvas: Any) -> bool:
        if self._executed:
            return False
        return self._run(canvas, apply=True)

    def revert(self, canvas: Any) -> bool:
        if not self._executed:
            return False
        return self._run(canvas, apply=False)
