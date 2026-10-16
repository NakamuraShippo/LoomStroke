"""Detached, replaceable repair requests committed as one history entry."""

from __future__ import annotations

import copy
from collections import OrderedDict
from collections.abc import Iterable
from typing import Any

from loomstroke.core.command_types import Command
from loomstroke.core.layer_tree_service import LayerTreeQueryService, sync_active_markers

from .atomic_scope import panel_atomic_scope
from .models import PanelFrameError


class PanelEditCommand(Command):
    """Apply already prepared children; roll back every applied child on failure."""

    vector_object = None
    _layer_id = "__loomstroke_comic_tools__"

    def __init__(self, commands: Iterable[Command], *, label: str) -> None:
        super().__init__()
        self.commands = tuple(commands)
        if not self.commands:
            raise PanelFrameError("an empty repair draft cannot be committed")
        self.label = label
        self.last_error = ""
        self._applied: list[Command] = []
        self._active: str | None = None
        self._selection: Any = None

    def _restore_selection(self, canvas: Any) -> None:
        canvas._active_layer_id = self._active
        canvas._vector_selection = copy.deepcopy(self._selection)
        sync_active_markers(canvas, LayerTreeQueryService(canvas))

    def _rollback(self, canvas: Any) -> None:
        for command in reversed(self._applied):
            if not command.revert(canvas):
                raise PanelFrameError("repair rollback failed")
        self._applied.clear()
        self._restore_selection(canvas)

    def apply(self, canvas: Any) -> bool:
        with panel_atomic_scope(canvas) as scope:
            result = self._apply(canvas, refresh=scope.root)
            scope.committed = result
            return result

    def _apply(self, canvas: Any, *, refresh: bool) -> bool:
        self.last_error = ""
        self._active = canvas._active_layer_id
        self._selection = copy.deepcopy(getattr(canvas, "_vector_selection", None))
        self._applied = []
        try:
            for command in self.commands:
                if not command.apply(canvas):
                    self.last_error = getattr(command, "last_error", "") or "repair request failed"
                    self._rollback(canvas)
                    return False
                self._applied.append(command)
            from .panel_gate import refresh_shared_masks  # noqa: PLC0415

            if refresh:
                refresh_shared_masks(canvas)
        except Exception:
            self._rollback(canvas)
            from .panel_gate import refresh_shared_masks  # noqa: PLC0415

            if refresh:
                refresh_shared_masks(canvas)
            raise
        self._executed = True
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._executed:
            return False
        with panel_atomic_scope(canvas) as scope:
            self._rollback(canvas)
            from .panel_gate import refresh_shared_masks  # noqa: PLC0415

            if scope.root:
                refresh_shared_masks(canvas)
            self._executed = False
            scope.committed = True
        return True


class PanelEditDraft:
    """Session-only preview state. Cancel/replace never touch source or history."""

    def __init__(self, *, page_id: str, label: str) -> None:
        if not page_id:
            raise PanelFrameError("repair draft requires a page identity")
        self.page_id = page_id
        self.label = label
        self._requests: OrderedDict[str, Command] = OrderedDict()

    def put(self, key: str, command: Command) -> None:
        if not key:
            raise PanelFrameError("repair request requires an identity")
        self._requests[key] = command

    def remove(self, key: str) -> None:
        self._requests.pop(key, None)

    def cancel(self) -> None:
        self._requests.clear()

    def command(self, *, page_id: str) -> PanelEditCommand:
        if page_id != self.page_id:
            raise PanelFrameError("repair draft belongs to another page")
        return PanelEditCommand(self._requests.values(), label=self.label)
