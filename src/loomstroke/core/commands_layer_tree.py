"""Layer tree commands."""

from __future__ import annotations

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

    def apply(self, canvas: Canvas) -> bool:
        fn = getattr(canvas, "group_layers", None)
        if not callable(fn):
            return False
        gid = fn(self._layer_ids, self._name)
        if gid is None:
            return False
        self._group_id = gid
        return True

    def revert(self, canvas: Canvas) -> bool:
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

    def apply(self, canvas: Canvas) -> bool:
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

    def apply(self, canvas: Canvas) -> bool:
        fn = getattr(canvas, "add_layer_mask", None)
        if not callable(fn):
            return False
        return bool(fn(self._layer_id))

    def revert(self, canvas: Canvas) -> bool:
        fn = getattr(canvas, "remove_layer_mask", None)
        if not callable(fn):
            return False
        return bool(fn(self._layer_id))


class RemoveLayerMaskCommand(Command):
    """Remove a layer mask with undo support (stores mask data for revert)."""

    def __init__(self, layer_id: str) -> None:
        super().__init__()
        self._layer_id = layer_id
        self._saved_mask: Any | None = None
        self._mask_enabled: bool = True

    def apply(self, canvas: Canvas) -> bool:
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

    def apply(self, canvas: Canvas) -> bool:
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

    def apply(self, canvas: Canvas) -> bool:
        fn = getattr(canvas, "add_layer", None)
        if not callable(fn):
            return False
        ref = fn(self._name, layer_type=self._layer_type)
        if ref is None:
            return False
        self._layer_id = getattr(ref, "id", None)
        return self._layer_id is not None

    def revert(self, canvas: Canvas) -> bool:
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

    def apply(self, canvas: Canvas) -> bool:
        from .comic_panels.content_tree_operation import (  # noqa: PLC0415
            apply_content_tree_operation,
        )

        result = apply_content_tree_operation(self, canvas, "copy")
        return self._apply_standard(canvas) if result is None else result

    def _apply_standard(self, canvas: Canvas) -> bool:
        import copy  # noqa: PLC0415

        source = _resolve_layer_by_id(canvas, self._source_id)
        if source is None:
            return False

        source_name = getattr(source, "name", "Layer")
        layer_type = getattr(source, "layer_type", "raster")
        add_layer = getattr(canvas, "add_layer", None)
        if not callable(add_layer):
            return False

        ref = add_layer(f"{source_name} Copy", layer_type=layer_type)
        if ref is None:
            return False
        new_id = getattr(ref, "id", None)
        if new_id is None:
            return False
        self._new_layer_id = new_id

        new_layer = _resolve_layer_by_id(canvas, new_id)
        if new_layer is None:
            return False

        # Copy visual state
        new_layer.visible = getattr(source, "visible", True)
        new_layer.opacity = float(getattr(source, "opacity", 1.0))
        new_layer.locked = bool(getattr(source, "locked", False))
        new_layer.blend_mode = getattr(source, "blend_mode", BlendMode.NORMAL)
        new_layer.offset_x = int(getattr(source, "offset_x", 0))
        new_layer.offset_y = int(getattr(source, "offset_y", 0))

        src_image = getattr(source, "image", None)
        if src_image is not None and hasattr(src_image, "copy"):
            new_layer.image = src_image.copy()
        native = getattr(source, "native_cmyk", None)
        if native is not None:
            new_layer.native_cmyk = native.copy()

        src_vectors = getattr(source, "vector_objects", None)
        if src_vectors:
            new_layer.vector_objects = copy.deepcopy(src_vectors)

        src_metadata = getattr(source, "metadata", None)
        if isinstance(src_metadata, dict):
            new_layer.metadata = copy.deepcopy(src_metadata)

        return True

    def revert(self, canvas: Canvas) -> bool:
        if self._panel_command is not None:
            return self._panel_command.revert(canvas)
        if self._new_layer_id is None:
            return False
        fn = getattr(canvas, "remove_layer", None)
        if not callable(fn):
            return False
        return bool(fn(self._new_layer_id))
