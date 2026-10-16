"""Command-backed creation and editing of image filter layers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .command_layer_access import _resolve_layer_by_id
from .command_types import Command
from .filters import FILTER_LAYER_TYPE

if TYPE_CHECKING:
    from .canvas import Canvas


def _filter_port(canvas: Any, name: str, legacy_name: str) -> Any:
    port = getattr(canvas, name, None)
    return port if callable(port) else getattr(canvas, legacy_name, None)


def _filter_identifier(value: str | None, legacy: str | None, name: str) -> str:
    if legacy is not None:
        if value is not None and value != legacy:
            raise TypeError(f"Conflicting {name} and legacy effect identifier")
        value = legacy
    if value is None:
        raise TypeError(f"{name} is required")
    return value


class AddFilterLayerCommand(Command):
    """Add a filter layer with undo support and stable layer placement."""

    def __init__(
        self,
        parent_layer_id: str | None,
        filter_type: str | None = None,
        params: dict[str, Any] | None = None,
        *,
        effect_type: str | None = None,
    ) -> None:
        super().__init__()
        self._parent_id = parent_layer_id
        self._filter_type = _filter_identifier(filter_type, effect_type, "filter_type")
        self._params = dict(params) if params else {}
        self._filter_id: str | None = None

    def apply(self, canvas: Canvas) -> bool:
        fn = _filter_port(canvas, "add_filter_layer", "add_effect_layer")
        if not callable(fn):
            return False
        filter_id = fn(self._parent_id, self._filter_type, self._params)
        if filter_id is None:
            return False
        self._filter_id = filter_id
        return True

    def revert(self, canvas: Canvas) -> bool:
        if self._filter_id is None:
            return False
        fn = _filter_port(canvas, "remove_filter_layer", "remove_effect_layer")
        return bool(fn(self._filter_id)) if callable(fn) else False

    @property
    def _effect_id(self) -> str | None:
        return self._filter_id

    @_effect_id.setter
    def _effect_id(self, value: str | None) -> None:
        self._filter_id = value

    @property
    def _effect_type(self) -> str:
        return self._filter_type

    @_effect_type.setter
    def _effect_type(self, value: str) -> None:
        self._filter_type = value


class RemoveFilterLayerCommand(Command):
    """Remove a filter layer with undo support."""

    def __init__(
        self, filter_layer_id: str | None = None, *, effect_layer_id: str | None = None
    ) -> None:
        super().__init__()
        self._filter_id = _filter_identifier(filter_layer_id, effect_layer_id, "filter_layer_id")
        self._parent_id: str | None = None
        self._filter_type: str | None = None
        self._filter_params: dict[str, Any] = {}
        self._position: int = -1

    def apply(self, canvas: Canvas) -> bool:
        layer = _resolve_layer_by_id(canvas, self._filter_id)
        if layer is None or getattr(layer, "layer_type", "") != FILTER_LAYER_TYPE:
            return False
        self._filter_type = getattr(layer, "filter_type", getattr(layer, "effect_type", None))
        self._filter_params = dict(
            getattr(layer, "filter_params", getattr(layer, "effect_params", {}))
        )
        self._parent_id = getattr(layer, "parent_id", None)
        find_parent = getattr(canvas, "_find_parent", None)
        if callable(find_parent):
            result = find_parent(self._filter_id)
            if result is not None:
                self._position = result[1]
        fn = _filter_port(canvas, "remove_filter_layer", "remove_effect_layer")
        return bool(fn(self._filter_id)) if callable(fn) else False

    def revert(self, canvas: Canvas) -> bool:
        if self._filter_type is None:
            return False
        fn = _filter_port(canvas, "add_filter_layer", "add_effect_layer")
        if not callable(fn):
            return False
        filter_id = fn(
            self._parent_id, self._filter_type, self._filter_params, position=self._position
        )
        if filter_id is None:
            return False
        self._filter_id = filter_id
        return True

    @property
    def _effect_id(self) -> str:
        return self._filter_id

    @_effect_id.setter
    def _effect_id(self, value: str) -> None:
        self._filter_id = value

    @property
    def _effect_type(self) -> str | None:
        return self._filter_type

    @_effect_type.setter
    def _effect_type(self, value: str | None) -> None:
        self._filter_type = value

    @property
    def _effect_params(self) -> dict[str, Any]:
        return self._filter_params

    @_effect_params.setter
    def _effect_params(self, value: dict[str, Any]) -> None:
        self._filter_params = value


class ModifyFilterParamsCommand(Command):
    """Edit filter parameters, merging continuous slider changes."""

    def __init__(
        self,
        filter_layer_id: str | None = None,
        params: dict[str, Any] | None = None,
        *,
        merge_key: str | None = None,
        effect_layer_id: str | None = None,
    ) -> None:
        if params is None:
            raise TypeError("params is required")
        super().__init__(merge_key=merge_key)
        self._filter_id = _filter_identifier(filter_layer_id, effect_layer_id, "filter_layer_id")
        self._new_params = dict(params)
        self._old_params: dict[str, Any] | None = None

    def apply(self, canvas: Canvas) -> bool:
        fn_get = _filter_port(canvas, "get_filter_params", "get_effect_params")
        fn_set = _filter_port(canvas, "set_filter_params", "set_effect_params")
        if not callable(fn_get) or not callable(fn_set):
            return False
        old = fn_get(self._filter_id)
        if old is None:
            return False
        if self._old_params is None:
            self._old_params = old
        return bool(fn_set(self._filter_id, self._new_params))

    def revert(self, canvas: Canvas) -> bool:
        if self._old_params is None:
            return False
        fn_set = _filter_port(canvas, "set_filter_params", "set_effect_params")
        return bool(fn_set(self._filter_id, self._old_params)) if callable(fn_set) else False

    def can_merge_with(self, other: Command) -> bool:
        return (
            super().can_merge_with(other)
            and isinstance(other, ModifyFilterParamsCommand)
            and other._filter_id == self._filter_id
        )

    def merge_with(self, other: ModifyFilterParamsCommand) -> None:
        self._new_params = dict(other._new_params)

    @property
    def _effect_id(self) -> str:
        return self._filter_id

    @_effect_id.setter
    def _effect_id(self, value: str) -> None:
        self._filter_id = value


# Alias identity preserves isinstance routing and old command merge behavior.
AddEffectCommand = AddFilterLayerCommand
RemoveEffectCommand = RemoveFilterLayerCommand
ModifyEffectParamsCommand = ModifyFilterParamsCommand
