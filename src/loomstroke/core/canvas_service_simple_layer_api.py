"""Canvas service LayerApi mixin."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeAlias, cast

from numpy.typing import NDArray
from PySide6.QtCore import QRect
from PySide6.QtGui import (
    QImage,
)

from loomstroke.core.canvas_blend_service import CanvasBlendService
from loomstroke.core.canvas_layer_model import _Layer
from loomstroke.core.canvas_layer_snapshot_service import CanvasLayerSnapshotService
from loomstroke.core.canvas_models import (
    CanvasStateSnapshot,
    LayerTreeSnapshot,
)
from loomstroke.core.commands import (
    BlendMode,
    LayerTransform,
    LayerVisibilityCommand,
    SetLayer3DSceneCommand,
)
from loomstroke.core.selection_clipboard import SelectionPayload
from loomstroke.core.selection_state import SelectionStateService
from loomstroke.host_api import DocumentRef, LayerRef, Selection

logger = logging.getLogger(__name__)
_PEN_LAG_DIAG = os.environ.get("LOOMSTROKE_PEN_LAG_DIAG", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
LayerValue: TypeAlias = LayerRef | Mapping[str, int] | bool | str | int | None


if TYPE_CHECKING:
    from loomstroke.core.canvas_service_simple_accessors import CanvasServiceAccessorsMixin

    class _CanvasServiceLayerApiBase(CanvasServiceAccessorsMixin):
        _doc: DocumentRef
        _layers: list[_Layer]

        def submit(self, cmd: Any, *, merge_key: str | None = None) -> bool: ...

else:

    class _CanvasServiceLayerApiBase:
        pass


class CanvasServiceLayerApiMixin(_CanvasServiceLayerApiBase):
    def current_document(self) -> DocumentRef:
        """Get current document reference."""
        return self._doc

    def current_layer(self) -> LayerRef:
        """Get current active layer reference (tree-aware)."""
        return self._layer_tree().current_layer_ref()

    def get_canvas_state_snapshot(self) -> CanvasStateSnapshot:
        """Return scalar canvas state without exposing mutable internals."""

        return self._runtime_snapshot().get_canvas_state_snapshot()

    def get_layer_tree_snapshot(self) -> LayerTreeSnapshot:
        """Return an identity-only snapshot of the layer tree."""
        return self._layer_tree().layer_tree_snapshot()

    def get_selection_state_snapshot(self) -> Any:
        """Return a deep-copied selection snapshot."""

        return self._runtime_snapshot().get_selection_state_snapshot()

    def get_vector_state_snapshot(self) -> Any:
        """Return vector edit/render state without exposing canvas globals."""

        return self._runtime_snapshot().get_vector_state_snapshot()

    def get_document_layer_snapshots(self) -> Any:
        """Capture persistence-safe layer snapshots on the Qt main thread."""

        return self._runtime_snapshot().get_document_layer_snapshots()

    def _flatten_layers(self, root: list[_Layer] | None = None) -> list[_Layer]:
        """Flatten the layer tree into a list in render order (depth-first).

        Group and effect layers are included; callers must filter as needed.
        """
        return cast("list[_Layer]", self._layer_tree().flatten_layers(root))

    def _find_layer_by_id_tree(
        self, layer_id: str, root: list[_Layer] | None = None
    ) -> _Layer | None:
        """Find a layer anywhere in the tree by its ID."""
        return cast("_Layer | None", self._layer_tree().find_layer_by_id_tree(layer_id, root))

    def set_layer_transform_preview(self, layer_id: str, transform: LayerTransform) -> bool:
        """Set a runtime-only layer transform preview.

        The preview is consumed by the renderer and intentionally omitted
        from document snapshots, autosave, and .lstroke manifests.
        """
        return self._layer_transform_preview().set_layer_transform_preview(
            layer_id,
            transform,
        )

    def clear_layer_transform_preview(self, layer_id: str) -> bool:
        """Clear a runtime-only layer transform preview."""
        return self._layer_transform_preview().clear_layer_transform_preview(layer_id)

    def _get_active_layer(self) -> _Layer | None:
        """Return the currently active layer, searching the full tree."""
        return cast("_Layer | None", self._layer_tree().get_active_layer())

    @property
    def active_layer(self) -> _Layer | None:
        """Public access to the currently active layer (tree-aware)."""
        return self._get_active_layer()

    def _find_parent(
        self, layer_id: str, root: list[_Layer] | None = None
    ) -> tuple[list[_Layer], int] | None:
        """Find the parent list and index of a layer by its ID.

        Returns ``(sibling_list, index)`` or ``None`` if not found.
        """
        return cast(
            "tuple[list[_Layer], int] | None", self._layer_tree().find_parent(layer_id, root)
        )

    def _walk_tree(
        self,
        visitor: Callable[[_Layer, int], None],
        root: list[_Layer] | None = None,
        *,
        _depth: int = 0,
    ) -> None:
        """Depth-first walk of the layer tree, calling *visitor(layer, depth)*."""
        self._layer_tree().walk_tree(visitor, root, depth=_depth)

    def add_group_layer(self, name: str | None = None) -> LayerRef:
        """Add an empty group layer at the top of the stack.

        Returns:
            Reference to the created group layer.
        """
        return self._layer_tree().add_group_layer(name)

    def _create_group_layer_for_tree_service(self, group_name: str) -> _Layer:
        return _Layer(
            id=f"group-{len(self._layers)}-{time.time_ns()}",
            name=group_name,
            layer_type="group",
            blend_mode=BlendMode.PASS_THROUGH,
            image=None,
        )

    def group_layers(self, layer_ids: list[str], name: str | None = None) -> str | None:
        """Move the given layers into a new group.

        The layers must be siblings (same parent list) and will be placed in
        a new group layer at the position of the topmost selected layer.

        Returns:
            The group layer ID, or ``None`` on failure.
        """
        return self._layer_tree().group_layers(layer_ids, name=name)

    def ungroup_layer(self, group_id: str) -> bool:
        """Dissolve a group, moving its children to the group's parent level.

        Returns:
            ``True`` if the group was dissolved successfully.
        """
        return self._layer_tree().ungroup_layer(group_id)

    def reparent_layer(self, layer_id: str, new_parent_id: str | None, position: int = -1) -> bool:
        """Move a layer to a new parent (or root level if *new_parent_id* is None).

        Args:
            layer_id: ID of the layer to move.
            new_parent_id: ID of the target group, or ``None`` for root.
            position: Insertion index in the target list (``-1`` = append).

        Returns:
            ``True`` on success.
        """
        return self._layer_tree().reparent_layer(layer_id, new_parent_id, position=position)

    def set_clipping(self, layer_id: str, enabled: bool) -> bool:
        """Enable or disable clipping on a layer."""
        return self._layer_property().set_clipping(layer_id, enabled)

    def add_layer_mask(self, layer_id: str) -> bool:
        """Add a white (fully visible) mask to the specified layer."""
        return self._layer_mask().add_layer_mask(layer_id)

    def remove_layer_mask(self, layer_id: str) -> bool:
        """Remove the attached mask from a layer."""
        return self._layer_mask().remove_layer_mask(layer_id)

    def set_layer_mask_enabled(self, layer_id: str, enabled: bool) -> bool:
        """Toggle the mask on/off without deleting it."""
        return self._layer_mask().set_layer_mask_enabled(layer_id, enabled)

    def get_layer_mask(self, layer_id: str) -> QImage | None:
        """Return a copy of the layer mask, or None."""
        return self._layer_mask().get_layer_mask(layer_id)

    def set_mask_edit_mode(self, layer_id: str, enabled: bool) -> bool:
        """Toggle mask editing mode for a layer."""
        return self._layer_mask().set_mask_edit_mode(layer_id, enabled)

    def selection_to_layer_mask(self, layer_id: str) -> bool:
        """Convert the current selection into a layer mask.

        The selection mask (Format_Alpha8, 0-255) is converted to a
        layer mask (Format_ARGB32_Premultiplied) where alpha values
        become grayscale luminance (white=selected, black=unselected).
        If the layer already has a mask it is replaced.
        """
        return self._layer_mask().selection_to_layer_mask(layer_id)

    def get_selection(self) -> Selection | None:
        """Get current selection if available."""
        return self._selection_state().get_selection()

    def get_selection_mask(self) -> QImage | None:
        """Return a defensive copy of the selection mask."""
        return self._selection_state().get_selection_mask()

    def get_selection_bounds(self) -> QRect | None:
        """Return current selection bounds."""
        return self._selection_state().get_selection_bounds()

    def get_selection_snapshot(self) -> SelectionPayload | None:
        """Return a snapshot of the current selection (image + mask)."""
        return self._selection_state().get_selection_snapshot()

    def set_selection_mask(
        self,
        mask: QImage | None,
        bounds: tuple[int, int, int, int] | QRect | None,
    ) -> None:
        """Update selection mask state without mutating layer data."""
        self._selection_state().set_selection_mask(mask, bounds)

    def _selection_bounds_from_mask(self, mask: QImage | None) -> QRect | None:
        return SelectionStateService.selection_bounds_from_mask(mask)

    @staticmethod
    def _selection_mask_to_numpy(mask: QImage) -> NDArray[Any]:
        return SelectionStateService.selection_mask_to_numpy(mask)

    def _selection_clip_state(self) -> tuple[QImage | None, QRect | None]:
        return self._selection_state().selection_clip_state()

    @staticmethod
    def _apply_selection_mask_to_image(image: QImage, mask: QImage) -> None:
        SelectionStateService.apply_selection_mask_to_image(image, mask)

    def cache_layer_snapshot(
        self,
        layer_id: str | None = None,
        fmt: str = "png",
        name: str | None = None,
    ) -> str:
        """Cache a snapshot of the specified layer image to disk.

        Args:
            layer_id: ID of the layer to snapshot; None uses the active layer.
            fmt: Preferred image format string.
            name: Optional filename stem for the cached file.

        Returns:
            Absolute path to the cached image.

        Raises:
            ValueError: If the layer cannot be resolved or has no image buffer.
            OSError: If the snapshot cannot be written.
        """
        return self._layer_snapshot().cache_layer_snapshot(layer_id=layer_id, fmt=fmt, name=name)

    def _snapshot_layer(self, layer_id: str | None) -> _Layer:
        return cast(_Layer, self._layer_snapshot()._snapshot_layer(layer_id))

    def _snapshot_image_for_layer(self, target_layer: _Layer) -> QImage:
        return self._layer_snapshot()._snapshot_image_for_layer(target_layer)

    @staticmethod
    def _snapshot_format(fmt: str) -> str:
        return CanvasLayerSnapshotService._snapshot_format(fmt)

    @staticmethod
    def _snapshot_path(stem: str, fmt: str) -> Path:
        return CanvasLayerSnapshotService._snapshot_path(stem, fmt)

    def _persist_snapshot(
        self,
        *,
        layer_id: str,
        layer_name: str,
        snapshot_image: QImage,
        fmt: str,
        stem: str,
    ) -> str:
        return self._layer_snapshot()._persist_snapshot(
            layer_id=layer_id,
            layer_name=layer_name,
            snapshot_image=snapshot_image,
            fmt=fmt,
            stem=stem,
        )

    def add_layer(
        self,
        name: str,
        *,
        layer_type: str = "raster",
        vector_kind: str = "draw",
    ) -> LayerRef:
        """Add a new layer.

        Args:
            name: Layer name
            layer_type: Layer kind ("raster", "vector", "mask", or "3d")
            vector_kind: Sub-kind for vector layers ("draw", "shape", or "text")

        Returns:
            Reference to the created layer
        """
        return self._layer_tree().add_layer(name, layer_type=layer_type, vector_kind=vector_kind)

    def add_mask_layer(self, name: str | None = None) -> LayerRef:
        """Add a mask layer."""
        return self._layer_tree().add_mask_layer(name)

    def add_vector_layer(
        self,
        name: str | None = None,
        *,
        vector_kind: str = "draw",
    ) -> LayerRef:
        """Convenience wrapper to add a vector layer and return its reference."""
        return self._layer_tree().add_vector_layer(name, vector_kind=vector_kind)

    def add_3d_layer(
        self,
        name: str | None = None,
        *,
        scene: Any = None,
        camera: Any = None,
        lighting: Any = None,
        display_mode: str = "solid",
        gen_3d: Any = None,
        origin: Any = None,
    ) -> LayerRef:
        """Add a 3D layer and return its reference."""
        if any(getattr(layer, "native_cmyk", None) is not None for layer in self._flatten_layers()):
            raise ValueError("3D layers require an RGB document")
        return self._layer_tree().add_3d_layer(
            name,
            scene=scene,
            camera=camera,
            lighting=lighting,
            display_mode=display_mode,
            gen_3d=gen_3d,
            origin=origin,
        )

    def add_3d_layer_from_file(
        self,
        path: str | Path,
        *,
        name: str | None = None,
        display_mode: str = "solid",
        gen_3d: Any = None,
        origin: Any = None,
    ) -> LayerRef:
        """Import a glTF/GLB/OBJ scene as an editable 3D layer."""
        from loomstroke.core.assets_3d import load_scene  # noqa: PLC0415

        source = Path(path).expanduser().resolve()
        scene = load_scene(source)
        return self.add_3d_layer(
            name or source.stem or None,
            scene=scene,
            display_mode=display_mode,
            gen_3d=gen_3d,
            origin=origin if origin is not None else self._layer_origin("import"),
        )

    def set_3d_layer_scene(
        self,
        layer_id: str,
        *,
        scene: Any,
        camera: Any = None,
        lighting: Any = None,
        display_mode: str = "solid",
        gen_3d: Any = None,
        origin: Any = None,
        name: str | None = None,
    ) -> bool:
        """Replace an existing raster/3D layer with an editable 3D scene."""
        return self.submit(
            SetLayer3DSceneCommand(
                layer_id,
                scene=scene,
                camera=camera,
                lighting=lighting,
                display_mode=display_mode,
                gen_3d=gen_3d,
                origin=origin,
                name=name,
            )
        )

    def set_3d_layer_from_file(
        self,
        layer_id: str,
        path: str | Path,
        *,
        camera: Any = None,
        lighting: Any = None,
        display_mode: str = "solid",
        gen_3d: Any = None,
        origin: Any = None,
        name: str | None = None,
    ) -> bool:
        """Load a glTF/GLB/OBJ scene and apply it to an existing layer."""
        from loomstroke.core.assets_3d import load_scene  # noqa: PLC0415

        source = Path(path).expanduser().resolve()
        scene = load_scene(source)
        return self.set_3d_layer_scene(
            layer_id,
            scene=scene,
            camera=camera,
            lighting=lighting,
            display_mode=display_mode,
            gen_3d=gen_3d,
            origin=origin if origin is not None else self._layer_origin("import"),
            name=name,
        )

    @staticmethod
    def _layer_origin(origin_type: str) -> Any:
        try:
            from loomstroke.plugins.ai_effect.data_model import LayerOrigin  # noqa: PLC0415
        except ImportError:
            return {"type": origin_type}
        return LayerOrigin(type=origin_type)

    def restore_vector_layer(
        self,
        *,
        layer_id: str,
        name: str | None = None,
        vector_kind: str = "draw",
        index: int | None = None,
    ) -> LayerRef | None:
        """Restore an empty vector layer with a stable ID for command redo."""
        return self._layer_tree().restore_vector_layer(
            layer_id=layer_id,
            name=name,
            vector_kind=vector_kind,
            index=index,
        )

    def replace_layers(self, layers: Sequence[_Layer]) -> None:
        """Replace the current layer stack with ``layers``.

        Document loading relies on this to atomically swap in the set of
        layers recovered from disk. The method copies layer state so that the
        caller retains ownership of the provided objects.
        """
        self._layer_tree().replace_layers(layers)
        channels = getattr(self, "_channels_service", None)
        if channels is not None:
            channels.reset()

    def _copy_layer_for_replace(self, layer: _Layer) -> _Layer:
        """Deep-copy a single layer (recursively) for replace_layers."""
        return cast("_Layer", self._layer_tree().copy_layer_for_replace(layer))

    def set_active_layer(self, layer_id: str) -> None:
        """Set the active layer by ID.

        Args:
            layer_id: ID of layer to activate
        """
        self._layer_tree().set_active_layer(layer_id)

    def _bump_active_texture_version(self) -> None:
        """Increment texture_version on the active layer for GPU cache invalidation."""
        self._render_invalidation().bump_active_texture_version()

    def bump_layer_texture_versions(self, layer_ids: Sequence[str]) -> None:
        """Increment texture versions by ID without exposing layer objects."""
        self._render_invalidation().bump_layer_texture_versions(layer_ids)

    def set_layer_visibility(self, layer_id: str, visible: bool) -> None:
        """Set layer visibility (undo-able).

        Args:
            layer_id: ID of layer to modify
            visible: New visibility state
        """
        cmd = LayerVisibilityCommand(layer_id, visible)
        self.submit(cmd)

    def set_layer_locked(self, layer_id: str, locked: bool) -> None:
        """Set layer lock state."""
        return self._layer_property().set_layer_locked(layer_id, locked)

    def set_layer_alpha_locked(self, layer_id: str, alpha_locked: bool) -> None:
        """Set layer alpha lock (transparency lock) state."""
        return self._layer_property().set_layer_alpha_locked(layer_id, alpha_locked)

    def set_layer_opacity(self, layer_id: str, opacity: float) -> None:
        """Set layer opacity.

        Args:
            layer_id: ID of layer to modify
            opacity: Opacity value (0.0 to 1.0)
        """
        return self._layer_property().set_layer_opacity(layer_id, opacity)

    def set_layer_blend_mode(self, layer_id: str, mode: str) -> None:
        """Set layer blend mode.

        Args:
            layer_id: ID of layer to modify
            mode: Blend mode string (normal, multiply, screen, etc.)
        """
        return self._layer_property().set_layer_blend_mode(layer_id, mode)

    def _publish_layer_event(
        self,
        event_name: str,
        *,
        layer: _Layer,
        change_type: str,
        old_value: LayerValue = None,
        new_value: LayerValue = None,
    ) -> None:
        """Publish a layer change event on the event bus if available."""
        self._layer_events().publish_layer_event(
            event_name,
            layer=layer,
            change_type=change_type,
            old_value=old_value,
            new_value=new_value,
        )

    def _emit_layer_selected(self, previous: _Layer | None, current: _Layer) -> None:
        """Publish a layer selected event on the event bus if available."""
        self._layer_events().emit_layer_selected(previous, current)

    def get_layers(self) -> list[LayerRef]:
        """Get list of all layers.

        Returns:
            List of layer references
        """
        return self._layer_query().get_layers()

    def get_layer_blend_mode(self, layer_id: str) -> str:
        """Get layer blend mode.

        Args:
            layer_id: ID of layer to query

        Returns:
            Blend mode string
        """
        return self._layer_query().get_layer_blend_mode(layer_id)

    def get_layer_info(self, layer_id: str) -> dict[str, Any] | None:
        """Get comprehensive layer information.

        Args:
            layer_id: ID of layer to query

        Returns:
            Dictionary with layer info or None if not found
        """
        return self._layer_query().get_layer_info(layer_id)

    def remove_layer(self, layer_id: str) -> bool:
        """Remove a layer by ID (supports nested layers in groups).

        Args:
            layer_id: ID of layer to remove

        Returns:
            True if layer was removed successfully
        """
        return self._layer_tree().remove_layer(layer_id)

    def remove_layer_by_index(self, index: int) -> bool:
        """Remove a layer by index.

        Args:
            index: Index of layer to remove (bottom layer = 0)

        Returns:
            True if layer was removed successfully
        """
        return self._layer_tree().remove_layer_by_index(index)

    def move_layer(self, from_index: int, to_index: int) -> bool:
        """Move a layer to a new stack position."""
        return self._layer_tree().move_layer(from_index, to_index)

    def ensure_layer_bounds(self, layer_id: str | None, bounds: tuple[int, int, int, int]) -> None:
        self._layer_bounds().ensure_layer_bounds(layer_id, bounds)

    def _ensure_layer_image_bounds(self, layer: _Layer, rect: QRect) -> None:
        self._layer_bounds().ensure_layer_image_bounds(layer, rect)

    def _normalize_blend_mode(self, mode: str | BlendMode) -> BlendMode:
        return CanvasBlendService.normalize_blend_mode(mode)

    def _emit_layer_content_changed(
        self,
        layer: _Layer,
        rect: QRect,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._layer_events().emit_layer_content_changed(layer, rect, metadata)

    def clear_layer(self, layer_id: str | None = None) -> None:
        """Clear a layer or the active layer.

        Args:
            layer_id: ID of layer to clear, or None for active layer
        """
        self._layer_content_mutation().clear_layer(layer_id)

    def get_layer_content_bounds(
        self, layer_id: str | None = None
    ) -> tuple[int, int, int, int] | None:
        """Return the bounding box of non-transparent content in canvas coordinates.

        Returns ``(x, y, width, height)`` or ``None`` for empty layers.
        """
        return self._layer_content_bounds().get_layer_content_bounds(layer_id)

    def invert_mask_layer(self, layer_id: str) -> bool:
        """Invert the alpha channel of a mask layer (swap painted/unpainted)."""
        return self._mask_layer().invert_mask_layer(layer_id)

    def load_mask_as_selection(self, layer_id: str) -> bool:
        """Load a mask layer's alpha channel as the current selection."""
        return self._mask_layer().load_mask_as_selection(layer_id)
