"""Layer tree commands."""

from __future__ import annotations

from inspect import getattr_static
from typing import TYPE_CHECKING, Any

from .comic_panels.membership import prepare_reparent_membership
from .comic_panels.models import PanelFrameError
from .command_layer_access import _resolve_layer_by_id
from .command_types import BlendMode, Command
from .commands_filter import (
    AddFilterLayerCommand,
    ModifyFilterParamsCommand,
    RemoveFilterLayerCommand,
)
from .group_layer_history import GroupLayerHistory
from .layer_creation_history import CreatedLayerHistory
from .layer_creation_preparation import duplicate_payload
from .layer_mask_command_state import _retained_mask_bytes

AddEffectCommand = AddFilterLayerCommand
ModifyEffectParamsCommand = ModifyFilterParamsCommand
RemoveEffectCommand = RemoveFilterLayerCommand

if TYPE_CHECKING:
    from .canvas import Canvas


# ------------------------------------------------------------------
# Phase 11: Tree-structure undo/redo commands
# ------------------------------------------------------------------


class GroupLayersCommand(Command):
    """Group layers into a new group with undo support."""

    def __init__(self, layer_ids: list[str], name: str | None = None) -> None:
        super().__init__()
        self._layer_ids = list(layer_ids)
        self._name = name
        self._group_id: str | None = None
        self._group_history = GroupLayerHistory()

    @property
    def retained_bytes(self) -> int:
        return self._group_history.retained_bytes

    def apply(self, canvas: Canvas) -> bool:
        if self._group_history.supports(canvas):
            if self._group_history.group is None and not self._group_history.prepare_group(
                canvas, self._layer_ids, self._name
            ):
                return False
            success = self._group_history.set_grouped(canvas, True)
            if success:
                self._group_id, self._executed = self._group_history.group.id, True
            return success
        fn = getattr(canvas, "group_layers", None)
        if not callable(fn):
            return False
        gid = fn(self._layer_ids, self._name)
        if gid is None:
            return False
        self._group_id = gid
        return True

    def revert(self, canvas: Canvas) -> bool:
        if self._group_history.group is not None:
            success = self._group_history.set_grouped(canvas, False)
            if success:
                self._executed = False
            return success
        if self._group_id is None:
            return False
        fn = getattr(canvas, "ungroup_layer", None)
        if not callable(fn):
            return False
        return bool(fn(self._group_id))


class UngroupLayerCommand(Command):
    """Dissolve a group, restoring children to the parent level."""

    def __init__(self, group_id: str) -> None:
        super().__init__()
        self._group_id = group_id
        self._child_ids: list[str] = []
        self._group_name: str | None = None
        self._parent_id: str | None = None
        self._insert_pos: int = -1
        self._group_history = GroupLayerHistory()

    @property
    def retained_bytes(self) -> int:
        return self._group_history.retained_bytes

    def apply(self, canvas: Canvas) -> bool:
        if self._group_history.supports(canvas):
            if self._group_history.group is None and not self._group_history.prepare_ungroup(
                canvas, self._group_id
            ):
                return False
            success = self._group_history.set_grouped(canvas, False)
            if success:
                self._executed = True
            return success
        layer = _resolve_layer_by_id(canvas, self._group_id)
        if layer is None or getattr(layer, "layer_type", "") != "group":
            return False
        self._group_name = getattr(layer, "name", None)
        self._child_ids = [getattr(c, "id", "") for c in getattr(layer, "children", [])]
        self._parent_id = getattr(layer, "parent_id", None)
        # Find position in parent list
        find_parent = getattr(canvas, "_find_parent", None)
        if callable(find_parent):
            result = find_parent(self._group_id)
            if result is not None:
                self._insert_pos = result[1]
        fn = getattr(canvas, "ungroup_layer", None)
        if not callable(fn):
            return False
        return bool(fn(self._group_id))

    def revert(self, canvas: Canvas) -> bool:
        if self._group_history.group is not None:
            success = self._group_history.set_grouped(canvas, True)
            if success:
                self._executed = False
            return success
        if not self._child_ids:
            return False
        fn = getattr(canvas, "group_layers", None)
        if not callable(fn):
            return False
        gid = fn(self._child_ids, self._group_name)
        if gid is None:
            return False
        self._group_id = gid
        return True


class AddLayerMaskCommand(Command):
    """Add a layer mask with undo support."""

    def __init__(self, layer_id: str) -> None:
        super().__init__()
        self._layer_id = layer_id
        self._mask_state: Any = None
        self._mask_submitting = False

    @property
    def retained_bytes(self) -> int:
        return _retained_mask_bytes(self)

    def apply(self, canvas: Canvas) -> bool:
        if self._mask_state is not None:
            return self._restore_checked(canvas, use_after=True)
        fn = getattr(canvas, "add_layer_mask", None)
        if not callable(fn):
            return False
        return bool(fn(self._layer_id))

    def revert(self, canvas: Canvas) -> bool:
        if self._mask_state is not None:
            return self._restore_checked(canvas, use_after=False)
        fn = getattr(canvas, "remove_layer_mask", None)
        if not callable(fn):
            return False
        return bool(fn(self._layer_id))

    def _restore_checked(self, canvas: Canvas, *, use_after: bool) -> bool:
        return bool(self._mask_state.restore(canvas, self, use_after=use_after))


class RemoveLayerMaskCommand(Command):
    """Remove a layer mask with undo support (stores mask data for revert)."""

    def __init__(self, layer_id: str) -> None:
        super().__init__()
        self._layer_id = layer_id
        self._saved_mask: Any | None = None
        self._mask_enabled: bool = True
        self._mask_state: Any = None
        self._mask_submitting = False

    @property
    def retained_bytes(self) -> int:
        return _retained_mask_bytes(self)

    def apply(self, canvas: Canvas) -> bool:
        if self._mask_state is not None:
            return self._restore_checked(canvas, use_after=True)
        layer = _resolve_layer_by_id(canvas, self._layer_id)
        if layer is None:
            return False
        mask = getattr(layer, "layer_mask", None)
        if mask is None:
            return False
        self._saved_mask = mask.copy() if hasattr(mask, "copy") else mask
        self._mask_enabled = bool(getattr(layer, "mask_enabled", True))
        fn = getattr(canvas, "remove_layer_mask", None)
        if not callable(fn):
            return False
        return bool(fn(self._layer_id))

    def revert(self, canvas: Canvas) -> bool:
        if self._mask_state is not None:
            return self._restore_checked(canvas, use_after=False)
        if self._saved_mask is None:
            return False
        layer = _resolve_layer_by_id(canvas, self._layer_id)
        if layer is None:
            return False
        layer.layer_mask = self._saved_mask
        layer.mask_enabled = self._mask_enabled
        recompose = getattr(canvas, "_recompose", None)
        if callable(recompose):
            recompose()
        return True

    def _restore_checked(self, canvas: Canvas, *, use_after: bool) -> bool:
        return bool(self._mask_state.restore(canvas, self, use_after=use_after))


class SetLayerMaskCommand(Command):
    """Replace a layer mask with undo support."""

    def __init__(self, layer_id: str, mask: Any, *, mask_enabled: bool = True) -> None:
        super().__init__()
        self._layer_id = layer_id
        self._mask = _copy_mask(mask)
        self._mask_enabled = bool(mask_enabled)
        self._old_mask: Any | None = None
        self._old_mask_enabled: bool = True
        self._applied = False
        self._mask_state: Any = None
        self._mask_submitting = False

    @property
    def retained_bytes(self) -> int:
        return _retained_mask_bytes(self)

    def apply(self, canvas: Canvas) -> bool:
        if self._mask_state is not None:
            return self._restore_checked(canvas, use_after=True)
        layer = _resolve_layer_by_id(canvas, self._layer_id)
        if layer is None or _mask_is_null(self._mask):
            return False
        if not _mask_matches_canvas_size(canvas, self._mask):
            return False
        current = getattr(layer, "layer_mask", None)
        self._old_mask = _copy_mask(current)
        self._old_mask_enabled = bool(getattr(layer, "mask_enabled", True))
        success = _set_layer_mask_state(
            canvas,
            layer,
            _copy_mask(self._mask),
            self._mask_enabled,
            change_type="mask_set",
        )
        self._applied = success
        return success

    def revert(self, canvas: Canvas) -> bool:
        if self._mask_state is not None:
            return self._restore_checked(canvas, use_after=False)
        if not self._applied:
            return False
        layer = _resolve_layer_by_id(canvas, self._layer_id)
        if layer is None:
            return False
        return _set_layer_mask_state(
            canvas,
            layer,
            _copy_mask(self._old_mask),
            self._old_mask_enabled,
            change_type="mask_restored",
        )

    def _restore_checked(self, canvas: Canvas, *, use_after: bool) -> bool:
        return bool(self._mask_state.restore(canvas, self, use_after=use_after))


_MASK_DEFAULT_METHODS = {
    kind: tuple((name, getattr(kind, name)) for name in ("apply", "revert", "_restore_checked"))
    for kind in (AddLayerMaskCommand, RemoveLayerMaskCommand, SetLayerMaskCommand)
}


def _is_standard_mask_command(command: Any) -> bool:
    defaults = _MASK_DEFAULT_METHODS.get(type(command))
    return defaults is not None and all(
        getattr_static(command, name, None) is method for name, method in defaults
    )


def _copy_mask(mask: Any | None) -> Any | None:
    if mask is None:
        return None
    copy = getattr(mask, "copy", None)
    return copy() if callable(copy) else mask


def _mask_is_null(mask: Any | None) -> bool:
    if mask is None:
        return True
    is_null = getattr(mask, "isNull", None)
    return bool(is_null()) if callable(is_null) else False


def _mask_matches_canvas_size(canvas: Canvas, mask: Any) -> bool:
    width_fn = getattr(mask, "width", None)
    height_fn = getattr(mask, "height", None)
    if not callable(width_fn) or not callable(height_fn):
        return False
    width = int(width_fn())
    height = int(height_fn())
    canvas_width = int(getattr(canvas, "_width", 0))
    canvas_height = int(getattr(canvas, "_height", 0))
    return width > 0 and height > 0 and width == canvas_width and height == canvas_height


def _set_layer_mask_state(
    canvas: Canvas,
    layer: Any,
    mask: Any | None,
    mask_enabled: bool,
    *,
    change_type: str,
) -> bool:
    layer.layer_mask = _copy_mask(mask)
    layer.mask_enabled = bool(mask_enabled)
    invalidate = getattr(canvas, "_invalidate_layer_cache", None)
    if callable(invalidate):
        invalidate()
    recompose = getattr(canvas, "_recompose", None)
    if callable(recompose):
        recompose()
    publish = getattr(canvas, "_publish_layer_event", None)
    if callable(publish):
        from loomstroke.core.events import Events  # noqa: PLC0415

        publish(Events.LAYER_CHANGED, layer=layer, change_type=change_type)
    return True


class ToggleClippingCommand(Command):
    """Toggle the clipping flag on a layer."""

    def __init__(self, layer_id: str, enabled: bool) -> None:
        super().__init__()
        self._layer_id = layer_id
        self._new_state = enabled
        self._old_state: bool | None = None

    def apply(self, canvas: Canvas) -> bool:
        layer = _resolve_layer_by_id(canvas, self._layer_id)
        if layer is None:
            return False
        self._old_state = bool(getattr(layer, "clipping", False))
        fn = getattr(canvas, "set_clipping", None)
        if not callable(fn):
            return False
        return bool(fn(self._layer_id, self._new_state))

    def revert(self, canvas: Canvas) -> bool:
        if self._old_state is None:
            return False
        fn = getattr(canvas, "set_clipping", None)
        if not callable(fn):
            return False
        return bool(fn(self._layer_id, self._old_state))


class AddLayerCommand(Command):
    """Add a new layer with undo support."""

    def __init__(self, name: str, *, layer_type: str = "raster") -> None:
        super().__init__()
        self._name = name
        self._layer_type = layer_type
        self._layer_id: str | None = None
        self._created = CreatedLayerHistory()

    @property
    def retained_bytes(self) -> int:
        return self._created.retained_bytes

    def _uses_standard_history(self, canvas: Canvas) -> bool:
        # A custom revert owns its own payload; never acquire only half that contract.
        return type(self).revert is AddLayerCommand.revert and self._created.supports(canvas)

    def apply(self, canvas: Canvas) -> bool:
        if self._uses_standard_history(canvas):
            succeeded = self._created.create(canvas, self._name, self._layer_type)
            if succeeded:
                self._layer_id = self._created.layer.id
                self._executed = True
            return succeeded
        fn = getattr(canvas, "add_layer", None)
        if not callable(fn):
            return False
        ref = fn(self._name, layer_type=self._layer_type)
        if ref is None:
            return False
        self._layer_id = getattr(ref, "id", None)
        return self._layer_id is not None

    def revert(self, canvas: Canvas) -> bool:
        if self._created.layer is not None:
            succeeded = self._created.remove(canvas)
            if succeeded:
                self._executed = False
            return succeeded
        if self._layer_id is None:
            return False
        fn = getattr(canvas, "remove_layer", None)
        if not callable(fn):
            return False
        return bool(fn(self._layer_id))


class DeleteLayerCommand(Command):
    """Delete a layer with undo support (stores full layer state for revert)."""

    def __init__(self, layer_id: str) -> None:
        super().__init__()
        self._layer_id = layer_id
        self._saved_layer: Any | None = None
        self._parent_id: str | None = None
        self._position: int = -1
        self._panel_command = None
        self.last_error = ""

    def apply(self, canvas: Canvas) -> bool:
        if not getattr(self, "_panel_raw", False):
            from .comic_panels.content_tree_operation import (  # noqa: PLC0415
                apply_content_tree_operation,
            )

            result = apply_content_tree_operation(self, canvas, "delete")
            if result is not None:
                return result
        # Find position in parent list and save reference before removal
        find_parent = getattr(canvas, "_find_parent", None)
        if not callable(find_parent):
            return False
        result = find_parent(self._layer_id)
        if result is None:
            return False
        parent_list, idx = result
        self._position = idx
        layer = parent_list[idx]
        self._parent_id = getattr(layer, "parent_id", None)
        # Keep reference to the layer object (not deepcopy — QImage is not picklable)
        fn = getattr(canvas, "remove_layer", None)
        if not callable(fn):
            return False
        if not fn(self._layer_id):
            return False
        self._saved_layer = layer
        from .japanese_text.story_geometry import (  # noqa: PLC0415
            layer_story_objects,
            refresh_story_geometry,
        )

        changed = refresh_story_geometry(canvas, layer_story_objects(layer))
        recompose = getattr(canvas, "_recompose", None)
        if changed and callable(recompose):
            recompose()
        return True

    def revert(self, canvas: Canvas) -> bool:  # noqa: PLR0912
        if self._panel_command is not None:
            return self._panel_command.revert(canvas)
        if self._saved_layer is None:
            return False
        # Re-insert the layer at its original position
        if self._parent_id is None:
            layers = getattr(canvas, "_layers", None)
            if layers is None:
                return False
            if self._position < 0 or self._position > len(layers):
                layers.append(self._saved_layer)
            else:
                layers.insert(self._position, self._saved_layer)
        else:
            parent = _resolve_layer_by_id(canvas, self._parent_id)
            if parent is None:
                return False
            children = getattr(parent, "children", None)
            if children is None:
                return False
            if self._position < 0 or self._position > len(children):
                children.append(self._saved_layer)
            else:
                children.insert(self._position, self._saved_layer)
        invalidate = getattr(canvas, "_invalidate_layer_cache", None)
        if callable(invalidate):
            invalidate()
        from .japanese_text.story_geometry import (  # noqa: PLC0415
            layer_story_objects,
            refresh_story_geometry,
        )

        refresh_story_geometry(canvas, layer_story_objects(self._saved_layer))
        recompose = getattr(canvas, "_recompose", None)
        if callable(recompose):
            recompose()
        # Notify UI so layer panel refreshes
        publish = getattr(canvas, "_publish_layer_event", None)
        if callable(publish):
            from loomstroke.core.events import Events  # noqa: PLC0415

            publish(Events.LAYER_CHANGED, layer=self._saved_layer, change_type="restored")
        return True


class ReparentLayerCommand(Command):
    """Move a layer to a new parent/position with undo support."""

    def __init__(
        self,
        layer_id: str,
        new_parent_id: str | None,
        position: int = -1,
    ) -> None:
        super().__init__()
        self._layer_id = layer_id
        self._new_parent_id = new_parent_id
        self._new_position = position
        self._old_parent_id: str | None = None
        self._old_position: int = -1
        self._membership_before: tuple[Any, ...] = ()

    def apply(self, canvas: Canvas) -> bool:
        # Save current position for revert
        from .comic_panels.membership import content_parent, whole_unit_source  # noqa: PLC0415

        tree_fn = getattr(canvas, "_layer_tree", None)
        if callable(tree_fn):
            try:
                self._layer_id = whole_unit_source(tree_fn(), self._layer_id)
                self._new_parent_id = content_parent(tree_fn(), self._new_parent_id)
            except PanelFrameError:
                return False
        find_parent = getattr(canvas, "_find_parent", None)
        if callable(find_parent):
            result = find_parent(self._layer_id)
            if result is not None:
                self._old_position = result[1]
        layer = _resolve_layer_by_id(canvas, self._layer_id)
        if layer is None:
            return False
        self._old_parent_id = getattr(layer, "parent_id", None)
        fn = getattr(canvas, "reparent_layer", None)
        if not callable(fn):
            return False
        if callable(tree_fn):
            try:
                self._membership_before = prepare_reparent_membership(
                    tree_fn(), layer, self._new_parent_id
                )
            except PanelFrameError:
                return False
        return bool(fn(self._layer_id, self._new_parent_id, self._new_position))

    def revert(self, canvas: Canvas) -> bool:
        fn = getattr(canvas, "reparent_layer", None)
        if not callable(fn):
            return False
        if self._membership_before:
            return bool(
                canvas._layer_tree().reparent_layer(
                    self._layer_id,
                    self._old_parent_id,
                    self._old_position,
                    _membership_restore=self._membership_before,
                )
            )
        return bool(fn(self._layer_id, self._old_parent_id, self._old_position))


class DuplicateLayerCommand(Command):
    """Duplicate a layer with undo support."""

    def __init__(self, source_layer_id: str) -> None:
        super().__init__()
        self._source_id = source_layer_id
        self._new_layer_id: str | None = None
        self._panel_command = None
        self.last_error = ""
        self._created = CreatedLayerHistory()

    @property
    def retained_bytes(self) -> int:
        return self._created.retained_bytes

    def apply(self, canvas: Canvas) -> bool:
        if self._created.layer is not None:
            succeeded = self._created.restore(canvas)
            if succeeded:
                self._new_layer_id = self._created.layer.id
                self._executed = True
            return succeeded
        from .comic_panels.content_tree_operation import (  # noqa: PLC0415
            apply_content_tree_operation,
        )

        result = apply_content_tree_operation(self, canvas, "copy")
        return self._apply_standard(canvas) if result is None else result

    def _apply_standard(self, canvas: Canvas) -> bool:
        source = _resolve_layer_by_id(canvas, self._source_id)
        if source is None:
            return False

        source_name = getattr(source, "name", "Layer")
        layer_type = getattr(source, "layer_type", "raster")
        add_layer = getattr(canvas, "add_layer", None)
        if not callable(add_layer):
            return False

        values = duplicate_payload(source, BlendMode.NORMAL)
        if self._created.supports(canvas):
            succeeded = self._created.create(canvas, f"{source_name} Copy", layer_type, values)
            if succeeded:
                self._new_layer_id = self._created.layer.id
                self._executed = True
            return succeeded

        ref = add_layer(f"{source_name} Copy", layer_type=layer_type)
        new_id = getattr(ref, "id", None) if ref is not None else None
        if new_id is None:
            return False
        self._new_layer_id = new_id

        new_layer = _resolve_layer_by_id(canvas, new_id)
        if new_layer is None:
            return False

        for key, value in values.items():
            setattr(new_layer, key, value)

        return True

    def revert(self, canvas: Canvas) -> bool:
        if self._panel_command is not None:
            return self._panel_command.revert(canvas)
        if self._created.layer is not None:
            succeeded = self._created.remove(canvas)
            if succeeded:
                self._executed = False
            return succeeded
        if self._new_layer_id is None:
            return False
        fn = getattr(canvas, "remove_layer", None)
        if not callable(fn):
            return False
        return bool(fn(self._new_layer_id))
