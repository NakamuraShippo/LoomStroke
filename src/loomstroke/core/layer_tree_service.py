"""Layer tree helpers for SimpleCanvasService."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from typing import Any, cast

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from loomstroke.core.canvas_models import LayerIdentity, LayerTreeSnapshot
from loomstroke.host_api import LayerRef

from .comic_panels.membership import prepare_reparent_membership
from .comic_panels.models import PanelFrameError
from .events import Events

logger = logging.getLogger(__name__)


class LayerTreeQueryService:
    """Read layer tree structure and identity snapshots."""

    def __init__(self, canvas: Any) -> None:
        self._canvas = canvas

    def current_layer_ref(self) -> LayerRef:
        """Get current active layer reference."""
        if not self._canvas._layers:
            return LayerRef(id="", name="")
        layer = self.get_active_layer()
        if layer is None:
            return LayerRef(id="", name="")
        return LayerRef(
            id=layer.id,
            name=layer.name,
            layer_type=layer.layer_type,
            vector_kind=layer.vector_kind,
        )

    def layer_tree_snapshot(self) -> LayerTreeSnapshot:
        """Return an identity-only snapshot of the layer tree."""
        identities: list[LayerIdentity] = []
        self._collect_identities(self._canvas._layers, identities)
        active_layer = self.find_layer_by_id_tree(getattr(self._canvas, "_active_layer_id", None))
        if active_layer is None:
            active_layer = self.get_active_layer()
            if active_layer and self.find_layer_by_id_tree(active_layer.id) is None:
                active_layer = None
        return LayerTreeSnapshot(
            layers=tuple(identities),
            root_layer_ids=tuple(layer.id for layer in self._canvas._layers),
            active_layer_id=active_layer.id if active_layer is not None else None,
            layer_cache_version=int(self._canvas._layer_cache_version),
        )

    def flatten_layers(self, root: list[Any] | None = None) -> list[Any]:
        """Flatten the layer tree into a list in render order."""
        result: list[Any] = []
        for layer in root if root is not None else self._canvas._layers:
            result.append(layer)
            if layer.children:
                result.extend(self.flatten_layers(layer.children))
        return result

    def find_layer_by_id_tree(self, layer_id: str, root: list[Any] | None = None) -> Any | None:
        """Find a layer anywhere in the tree by its ID."""
        for layer in root if root is not None else self._canvas._layers:
            if layer.id == layer_id:
                return layer
            if layer.children:
                found = self.find_layer_by_id_tree(layer_id, layer.children)
                if found is not None:
                    return found
        return None

    def get_active_layer(self) -> Any | None:
        """Return the currently active layer, searching the full tree."""
        pending = getattr(self._canvas, "_pending_panel_drawing", None)
        if pending is not None:
            return pending.command._layer
        active_layer_id = getattr(self._canvas, "_active_layer_id", None)
        if active_layer_id:
            found = self.find_layer_by_id_tree(active_layer_id)
            if found is not None:
                return found
        if 0 <= self._canvas._active_index < len(self._canvas._layers):
            return self._canvas._layers[self._canvas._active_index]
        return None

    def find_parent(
        self,
        layer_id: str,
        root: list[Any] | None = None,
    ) -> tuple[list[Any], int] | None:
        """Find the parent list and index of a layer by its ID."""
        siblings = root if root is not None else self._canvas._layers
        for index, layer in enumerate(siblings):
            if layer.id == layer_id:
                return (siblings, index)
            if layer.children:
                found = self.find_parent(layer_id, layer.children)
                if found is not None:
                    return found
        return None

    def walk_tree(
        self,
        visitor: Callable[[Any, int], None],
        root: list[Any] | None = None,
        *,
        depth: int = 0,
    ) -> None:
        """Depth-first walk of the layer tree, calling *visitor(layer, depth)*."""
        for layer in root if root is not None else self._canvas._layers:
            visitor(layer, depth)
            if layer.children:
                self.walk_tree(visitor, layer.children, depth=depth + 1)

    def top_level_index_for_layer(self, layer_id: str) -> int:
        """Root index of *layer_id* or of the root group containing it, else ``-1``.

        Returning ``-1`` rather than the current ``_active_index`` matters: a
        self-referential fallback silently reports "wherever we already point"
        for a layer that is not in the tree, which is how a stale index keeps
        outliving the layer it addressed.
        """
        for index, layer in enumerate(self._canvas._layers):
            if layer.id == layer_id:
                return index
            if layer.children and self.find_layer_by_id_tree(layer_id, layer.children):
                return index
        return -1

    def _collect_identities(
        self,
        layers: list[Any],
        identities: list[LayerIdentity],
        parent_id: str | None = None,
    ) -> None:
        for index, layer in enumerate(layers):
            identities.append(
                LayerIdentity(
                    id=layer.id,
                    name=layer.name,
                    parent_id=parent_id,
                    index=index,
                    layer_type=layer.layer_type,
                    vector_kind=layer.vector_kind,
                )
            )
            if layer.children:
                self._collect_identities(layer.children, identities, layer.id)


def sync_active_markers(canvas: Any, query: LayerTreeQueryService) -> None:
    """Re-derive both active-layer markers so they denote the same layer.

    ``SimpleCanvasService`` carries two markers: ``_active_layer_id`` (which
    ``get_active_layer()`` resolves first, backing the layer panel, stroke
    submit, mask commit and alpha preservation) and ``_active_index`` (which
    fill, selection, content mutation, apply-image and render invalidation use
    to index ``_layers`` directly).  Any operation that restructures ``_layers``
    has to repair both, otherwise those two groups of subsystems silently
    target different layers -- and when the index falls out of range, the
    direct readers raise ``IndexError``.

    The id wins whenever it still resolves, because that is the marker the
    active-layer resolution is built on.  When it does not resolve the index is
    all that is left, so it is clamped into range and its layer is adopted as
    the new id: that keeps a stale id from resurfacing and stealing the
    activation later, when a layer carrying it reappears (page switch back to a
    previously open page, undo of a delete).
    """
    layers = canvas._layers
    if not layers:
        canvas._active_index = 0
        canvas._active_layer_id = None
        return

    # Read defensively, as ``get_active_layer()`` does: lightweight canvases
    # built without ``__init__`` carry ``_active_index`` but no id.
    active_id = getattr(canvas, "_active_layer_id", None)
    if active_id:
        index = query.top_level_index_for_layer(active_id)
        if 0 <= index < len(layers):
            canvas._active_index = index
            return

    index = min(max(int(getattr(canvas, "_active_index", 0)), 0), len(layers) - 1)
    canvas._active_index = index
    canvas._active_layer_id = layers[index].id


class LayerTreeMutationService:
    """Mutate active layer state and layer parent/sibling relationships."""

    def __init__(self, canvas: Any, query: LayerTreeQueryService) -> None:
        self._canvas = canvas
        self._query = query

    def set_active_layer(self, layer_id: str) -> None:
        """Set the active layer by ID."""
        self._canvas._pending_panel_drawing = None
        if not self._canvas._layers:
            logger.debug("No layers available to activate")
            return

        if self._canvas._active_index >= len(self._canvas._layers):
            self._canvas._active_index = max(0, len(self._canvas._layers) - 1)

        current_layer = self._canvas._layers[self._canvas._active_index]
        if self._canvas._active_layer_id == layer_id:
            # Already active by id, but the index may have drifted behind a
            # tree restructure; repair it instead of returning on a mismatch.
            sync_active_markers(self._canvas, self._query)
            logger.debug("Requested layer is already active")
            return

        target = self._query.find_layer_by_id_tree(layer_id)
        if target is None:
            logger.debug("Layer %s not found in tree", layer_id)
            return

        self._canvas._active_index = self._query.top_level_index_for_layer(layer_id)
        previous = current_layer
        self._canvas._active_layer_id = layer_id
        logger.debug("Active layer set to: %s", target.name)
        if self._canvas._defer_recompose_depth > 0:
            self._canvas._defer_recompose_needed = True
        else:
            self._canvas.composited_changed.emit(self._canvas._composited)
        self._canvas._emit_layer_selected(previous, target)

    def move_layer(self, from_index: int, to_index: int) -> bool:
        """Move a root-level layer to a new stack position."""
        if len(self._canvas._layers) <= 1:
            logger.debug("Ignoring move_layer on insufficient layer count")
            return False
        if not (0 <= from_index < len(self._canvas._layers)):
            logger.warning("move_layer from_index out of range: %s", from_index)
            return False
        if not (0 <= to_index < len(self._canvas._layers)):
            logger.warning("move_layer to_index out of range: %s", to_index)
            return False
        if from_index == to_index:
            logger.debug("move_layer requested with identical indices; no-op")
            return False

        moved_layer = self._canvas._layers.pop(from_index)
        self._canvas._layers.insert(to_index, moved_layer)
        self._adjust_active_index_after_root_move(from_index, to_index)
        sync_active_markers(self._canvas, self._query)

        self._canvas._invalidate_layer_cache()
        self._canvas._recompose()
        logger.info(
            "Layer %s moved from index %s to %s",
            moved_layer.id,
            from_index,
            to_index,
        )
        self._canvas._publish_layer_event(
            Events.LAYER_CHANGED,
            layer=moved_layer,
            change_type="reordered",
            old_value={"from_index": from_index},
            new_value={"to_index": to_index},
        )
        return True

    def group_layers(self, layer_ids: list[str], name: str | None = None) -> str | None:
        """Move sibling layers into a new group."""
        if len(layer_ids) < 2:
            return None

        first_result = self._query.find_parent(layer_ids[0])
        if first_result is None:
            return None
        parent_list, _ = first_result
        indices: list[int] = []
        for layer_id in layer_ids:
            found = False
            for index, layer in enumerate(parent_list):
                if layer.id == layer_id:
                    indices.append(index)
                    found = True
                    break
            if not found:
                return None

        indices.sort()
        children: list[Any] = []
        for index in reversed(indices):
            child = parent_list.pop(index)
            children.insert(0, child)

        default_index = (
            sum(1 for layer in self._query.flatten_layers() if layer.layer_type == "group") + 1
        )
        group_name = name or f"Group {default_index}"
        group = self._canvas._create_group_layer_for_tree_service(group_name)
        for child in children:
            child.parent_id = group.id
        group.children = children

        insert_pos = min(indices)
        parent_list.insert(insert_pos, group)
        sync_active_markers(self._canvas, self._query)

        self._canvas._invalidate_layer_cache()
        self._canvas._recompose()
        self._canvas._publish_layer_event(
            Events.LAYER_CHANGED,
            layer=group,
            change_type="grouped",
        )
        return cast(str, group.id)

    def ungroup_layer(self, group_id: str) -> bool:
        """Dissolve a group, moving its children to the group's parent level."""
        result = self._query.find_parent(group_id)
        if result is None:
            return False
        parent_list, index = result
        group = parent_list[index]
        if group.layer_type != "group":
            return False

        parent_list.pop(index)
        parent_id = group.parent_id
        for offset, child in enumerate(group.children):
            child.parent_id = parent_id
            parent_list.insert(index + offset, child)
        sync_active_markers(self._canvas, self._query)

        self._canvas._invalidate_layer_cache()
        self._canvas._recompose()
        self._canvas._publish_layer_event(
            Events.LAYER_CHANGED,
            layer=group,
            change_type="ungrouped",
        )
        return True

    def reparent_layer(  # noqa: PLR0912 - atomic tree and comic membership validation
        self,
        layer_id: str,
        new_parent_id: str | None,
        position: int = -1,
        *,
        _membership_restore: tuple[Any, ...] | None = None,
        _part_move: bool = False,
    ) -> bool:
        """Move a layer to a new parent, or root when *new_parent_id* is ``None``."""
        if not _part_move and _membership_restore is None:
            from .comic_panels.membership import content_parent, whole_unit_source  # noqa: PLC0415

            try:
                layer_id = whole_unit_source(self._query, layer_id)
                new_parent_id = content_parent(self._query, new_parent_id)
            except PanelFrameError:
                return False
        source = self._query.find_parent(layer_id)
        if source is None or layer_id == new_parent_id:
            return False
        source_list, source_index = source
        layer = source_list[source_index]

        if new_parent_id is None:
            target_list = self._canvas._layers
        else:
            target = self._query.find_layer_by_id_tree(new_parent_id)
            if target is None or not self._can_parent_layer(target, layer):
                return False
            if self._query.find_layer_by_id_tree(new_parent_id, layer.children) is not None:
                return False
            target_list = target.children

        try:
            membership = (
                () if _part_move else prepare_reparent_membership(self._query, layer, new_parent_id)
            )
        except PanelFrameError as exc:
            logger.warning("Cannot reparent comic content: %s", exc)
            return False
        old_parent_id = layer.parent_id
        source_list.pop(source_index)
        layer.parent_id = new_parent_id
        if position < 0 or position > len(target_list):
            target_list.append(layer)
        else:
            target_list.insert(position, layer)
        try:
            for change in membership if _membership_restore is None else _membership_restore:
                change.install(before=_membership_restore is not None)
            sync_active_markers(self._canvas, self._query)
            self._canvas._invalidate_layer_cache()
            self._canvas._recompose()
            self._canvas._publish_layer_event(
                Events.LAYER_CHANGED,
                layer=layer,
                change_type="reparented",
            )
        except Exception:
            target_list.remove(layer)
            source_list.insert(source_index, layer)
            layer.parent_id = old_parent_id
            for change in membership:
                change.install(before=True)
            self._canvas._invalidate_layer_cache()
            sync_active_markers(self._canvas, self._query)
            raise
        return True

    @staticmethod
    def _can_parent_layer(target: Any, child: Any) -> bool:
        if target.layer_type == "group":
            return True
        return target.layer_type in ("raster", "vector") and child.layer_type == "effect"

    def _adjust_active_index_after_root_move(self, from_index: int, to_index: int) -> None:
        if self._canvas._active_index == from_index:
            self._canvas._active_index = to_index
        elif from_index < self._canvas._active_index <= to_index:
            self._canvas._active_index -= 1
        elif to_index <= self._canvas._active_index < from_index:
            self._canvas._active_index += 1


class LayerRemovalService:
    """Remove layers and run deletion cleanup side effects."""

    def __init__(self, canvas: Any, query: LayerTreeQueryService) -> None:
        self._canvas = canvas
        self._query = query

    def remove_layer(self, layer_id: str) -> bool:
        """Remove a layer by ID, preserving single-delete side effects."""
        if len(self._canvas._layers) <= 1:
            logger.warning("Cannot remove last layer")
            return False

        if self._canvas._layers and self._canvas._layers[0].id == layer_id:
            logger.warning("Cannot remove the background layer")
            return False

        result = self._query.find_parent(layer_id)
        if result is None:
            logger.warning("Layer not found for removal: %s", layer_id)
            return False

        parent_list, index = result
        removed_layer = parent_list.pop(index)

        if parent_list is self._canvas._layers:
            self._adjust_active_index_after_root_remove(index)
        sync_active_markers(self._canvas, self._query)

        self._cleanup_removed_layer(removed_layer)
        self._canvas._invalidate_layer_cache()
        self._canvas._recompose()
        self._canvas._cascade_ai_effect_refs_on_delete(layer_id)

        removed_ref = LayerRef(id=removed_layer.id, name=removed_layer.name)
        logger.info("Layer removed: %s (id: %s)", removed_layer.name, layer_id)
        self._canvas._publish_layer_event(
            Events.LAYER_REMOVED,
            layer=removed_layer,
            change_type="removed",
            old_value=removed_ref,
        )
        return True

    def remove_layer_by_index(self, index: int) -> bool:
        """Remove a root-level layer by index."""
        if len(self._canvas._layers) <= 1:
            logger.warning("Cannot remove last layer")
            return False

        if index == 0:
            logger.warning("Cannot remove the background layer")
            return False

        if 0 <= index < len(self._canvas._layers):
            removed_layer = self._canvas._layers.pop(index)
            self._adjust_active_index_after_root_remove(index)
            sync_active_markers(self._canvas, self._query)
            self._cleanup_removed_layer(removed_layer)
            self._canvas._invalidate_layer_cache()
            self._canvas._recompose()

            removed_ref = LayerRef(id=removed_layer.id, name=removed_layer.name)
            logger.info("Layer removed at index %s: %s", index, removed_layer.name)
            self._canvas._publish_layer_event(
                Events.LAYER_REMOVED,
                layer=removed_layer,
                change_type="removed",
                old_value=removed_ref,
            )
            return True

        logger.warning("Invalid layer index for removal: %s", index)
        return False

    def remove_layers(self, layer_ids: list[str]) -> bool:
        """Bulk-remove layers, tombstoning them for AI Effect undo/redo."""
        tombstones = self._ai_effect_output_tombstones()

        removed: list[Any] = []
        for layer_id in layer_ids:
            result = self._query.find_parent(layer_id)
            if result is None:
                logger.debug("remove_layers: id %s not found, skipped", layer_id)
                continue
            parent_list, index = result
            layer = parent_list.pop(index)
            if parent_list is self._canvas._layers:
                self._adjust_active_index_after_root_remove(index, clamp_to_zero=True)
            tombstones[layer_id] = layer
            removed.append(layer)
            self._canvas._cascade_ai_effect_refs_on_delete(layer_id)

        if not removed:
            return False

        sync_active_markers(self._canvas, self._query)
        self._canvas._invalidate_layer_cache()
        self._canvas._recompose()
        logger.info("remove_layers removed %d layer(s)", len(removed))
        self._canvas._publish_layer_event(
            Events.LAYER_REMOVED,
            layer=removed[-1],
            change_type="ai_effect_outputs_removed",
        )
        return True

    def _cleanup_removed_layer(self, removed_layer: Any) -> None:
        if getattr(self._canvas, "_vector_preview_layer_id", None) == removed_layer.id:
            self._canvas._vector_preview = None
            self._canvas._vector_preview_layer_id = None
        vector_selection = getattr(self._canvas, "_vector_selection", None)
        if vector_selection:
            try:
                if vector_selection.get("layer_id") in (None, removed_layer.id):
                    self._canvas._vector_selection = None
            except Exception:
                self._canvas._vector_selection = None

    def _ai_effect_output_tombstones(self) -> dict[str, Any]:
        tombstones = getattr(self._canvas, "_ai_effect_output_tombstones", None)
        if tombstones is None:
            tombstones = {}
            self._canvas._ai_effect_output_tombstones = tombstones
        return tombstones

    def _adjust_active_index_after_root_remove(
        self,
        index: int,
        *,
        clamp_to_zero: bool = False,
    ) -> None:
        if self._canvas._active_index >= len(self._canvas._layers):
            new_index = len(self._canvas._layers) - 1
            if clamp_to_zero:
                new_index = max(0, new_index)
            self._canvas._active_index = new_index
        elif self._canvas._active_index > index:
            self._canvas._active_index -= 1


class LayerCreationService:
    """Create and replace layer stack entries for SimpleCanvasService."""

    def __init__(
        self,
        canvas: Any,
        query: LayerTreeQueryService,
        *,
        layer_type: type[Any] | None = None,
        document_change_event_type: Callable[..., Any] | None = None,
        blend_mode_type: type[Any] | None = None,
        normal_blend_mode: Any | None = None,
        identity_transform: Callable[[], Any] | None = None,
    ) -> None:
        self._canvas = canvas
        self._query = query
        self._layer_type = layer_type
        self._document_change_event_type = document_change_event_type
        self._blend_mode_type = blend_mode_type
        self._normal_blend_mode = normal_blend_mode
        self._identity_transform = identity_transform

    def add_layer(
        self,
        name: str,
        *,
        layer_type: str = "raster",
        vector_kind: str = "draw",
    ) -> LayerRef:
        kind = layer_type if layer_type in {"raster", "vector", "mask", "3d"} else "raster"
        native = getattr(self._canvas, "_native_cmyk_service", None)
        if native is None and any(
            getattr(layer, "native_cmyk", None) is not None
            for layer in self._canvas._layer_tree().flatten_layers()
        ):
            native = self._canvas.native_cmyk()
        if (
            native is not None
            and native.profile() is not None
            and kind not in ("raster", "mask", "vector")
        ):
            raise ValueError("Native CMYK requires raster layers")
        image: QImage | None = None
        if kind != "vector":
            image = QImage(
                self._canvas._width,
                self._canvas._height,
                QImage.Format.Format_ARGB32_Premultiplied,
            )
            image.fill(Qt.GlobalColor.transparent)
        layer = self._make_layer(
            id=f"layer-{len(self._canvas._layers)}-{time.time_ns()}",
            name=name,
            visible=True,
            opacity=1.0,
            image=image,
            layer_type=kind,
            vector_kind=vector_kind if kind == "vector" else "draw",
        )

        if native is not None:
            native.initialize_layer(layer)
        self._canvas._layers.append(layer)
        self._canvas._active_index = len(self._canvas._layers) - 1
        self._canvas._active_layer_id = layer.id
        self._canvas._invalidate_layer_cache()
        self._canvas._recompose()

        layer_ref = self.layer_ref(layer)
        logger.info("Layer added: %s (id: %s)", name, layer.id)
        self._canvas._publish_layer_event(
            Events.LAYER_ADDED,
            layer=layer,
            change_type="added",
            new_value=layer_ref,
        )
        return layer_ref

    def add_mask_layer(self, name: str | None = None) -> LayerRef:
        default_index = sum(1 for layer in self._canvas._layers if layer.layer_type == "mask") + 1
        layer_name = name or f"Mask {default_index}"
        return self.add_layer(layer_name, layer_type="mask")

    def add_vector_layer(
        self,
        name: str | None = None,
        *,
        vector_kind: str = "draw",
    ) -> LayerRef:
        default_index = (
            sum(
                1
                for layer in self._canvas._layers
                if layer.layer_type == "vector" and layer.vector_kind == vector_kind
            )
            + 1
        )
        if vector_kind == "shape":
            layer_name = name or f"Shape {default_index}"
        else:
            layer_name = name or f"Vector Layer {default_index}"
        from .comic_panels.tool_context import add_vector_in_panel  # noqa: PLC0415

        return add_vector_in_panel(self._canvas, self.add_layer, layer_name, vector_kind)

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
        default_index = sum(1 for layer in self._canvas._layers if layer.layer_type == "3d") + 1
        layer_name = name or f"3D Layer {default_index}"
        image = QImage(
            self._canvas._width,
            self._canvas._height,
            QImage.Format.Format_ARGB32_Premultiplied,
        )
        image.fill(Qt.GlobalColor.transparent)
        layer = self._make_layer(
            id=f"layer-{len(self._canvas._layers)}-{time.time_ns()}",
            name=layer_name,
            visible=True,
            opacity=1.0,
            image=image,
            layer_type="3d",
            vector_kind="draw",
        )
        layer.scene_3d = scene
        layer.camera_3d = camera
        layer.lighting_3d = lighting
        layer.display_mode_3d = str(display_mode or "solid")
        layer.gen_3d = gen_3d
        layer.origin = origin

        self._canvas._layers.append(layer)
        self._canvas._active_index = len(self._canvas._layers) - 1
        self._canvas._active_layer_id = layer.id
        self._canvas._invalidate_layer_cache()
        self._canvas._recompose()

        layer_ref = self.layer_ref(layer)
        logger.info("3D layer added: %s (id: %s)", layer_name, layer.id)
        self._canvas._publish_layer_event(
            Events.LAYER_ADDED,
            layer=layer,
            change_type="added",
            new_value=layer_ref,
        )
        return layer_ref

    def add_group_layer(self, name: str | None = None) -> LayerRef:
        default_index = (
            sum(1 for layer in self._query.flatten_layers() if layer.layer_type == "group") + 1
        )
        group_name = name or f"Group {default_index}"
        layer = self._make_layer(
            id=f"group-{len(self._canvas._layers)}-{time.time_ns()}",
            name=group_name,
            layer_type="group",
            image=None,
        )
        self._canvas._layers.append(layer)
        self._canvas._active_index = len(self._canvas._layers) - 1
        # ``get_active_layer()`` resolves ``_active_layer_id`` first and only
        # falls back to ``_active_index``, so leaving the id pointing at the
        # previously active layer would keep that layer active (and receiving
        # strokes) even though the new group is the one just created.
        self._canvas._active_layer_id = layer.id
        self._canvas._invalidate_layer_cache()
        self._canvas._recompose()

        layer_ref = LayerRef(id=layer.id, name=layer.name, layer_type=layer.layer_type)
        self._canvas._publish_layer_event(
            Events.LAYER_ADDED,
            layer=layer,
            change_type="added",
            new_value=layer_ref,
        )
        return layer_ref

    def restore_vector_layer(
        self,
        *,
        layer_id: str,
        name: str | None = None,
        vector_kind: str = "draw",
        index: int | None = None,
    ) -> LayerRef | None:
        existing = self._query.find_layer_by_id_tree(layer_id)
        if existing is not None:
            return self.layer_ref(existing)

        layer = self._make_layer(
            id=layer_id,
            name=name or "Vector Layer",
            visible=True,
            opacity=1.0,
            image=None,
            layer_type="vector",
            vector_kind=vector_kind or "draw",
        )
        insert_at = (
            len(self._canvas._layers)
            if index is None
            else max(
                0,
                min(index, len(self._canvas._layers)),
            )
        )
        self._canvas._layers.insert(insert_at, layer)
        self._canvas._active_index = insert_at
        # Keep both active markers on the restored layer.  ``_active_index``
        # feeds fill, selection and content mutation while
        # ``get_active_layer()`` resolves ``_active_layer_id`` first and only
        # falls back to the index, so moving one without the other makes those
        # subsystems target different layers.  Restoring is the redo half of a
        # create, and the original create activated the new layer.
        self._canvas._active_layer_id = layer.id
        self._canvas._invalidate_layer_cache()
        self._canvas._recompose()

        layer_ref = self.layer_ref(layer)
        self._canvas._publish_layer_event(
            Events.LAYER_ADDED,
            layer=layer,
            change_type="restored",
            new_value=layer_ref,
        )
        return layer_ref

    def replace_layers(self, layers: Sequence[Any]) -> None:
        incoming_layers = list(layers)
        if not incoming_layers:
            logger.warning("replace_layers called with an empty layer list; ignoring")
            return

        if len(incoming_layers) > self._canvas._max_layers:
            logger.info(
                "replace_layers received %s layers; expanding max_layers from %s",
                len(incoming_layers),
                self._canvas._max_layers,
            )
            self._canvas._max_layers = len(incoming_layers)

        previous_active_id = None
        if self._canvas._layers and 0 <= self._canvas._active_index < len(self._canvas._layers):
            previous_active_id = self._canvas._layers[self._canvas._active_index].id

        new_layers = [self.copy_layer_for_replace(layer) for layer in incoming_layers]
        if not new_layers:
            logger.warning("replace_layers did not receive any valid layers; ignoring")
            return

        from .layer_replacement_versions import advance_replacement_versions  # noqa: PLC0415

        advance_replacement_versions(self._canvas._layers, new_layers)
        self._canvas._pending_panel_drawing = None
        self._canvas._layers = new_layers
        self._canvas._vector_raster_cache.clear()
        self._canvas._active_index = len(self._canvas._layers) - 1
        if previous_active_id is not None:
            for index, layer in enumerate(self._canvas._layers):
                if layer.id == previous_active_id:
                    self._canvas._active_index = index
                    break
        sync_active_markers(self._canvas, self._query)
        self._canvas._invalidate_layer_cache()
        self._canvas._reset_filter_cache_bytes()

        self._canvas.history_clear()
        self._canvas._recompose()

        self._emit_replace_layers_doc_changed()

    def copy_layer_for_replace(self, layer: Any) -> Any:
        raw_blend_mode = getattr(layer, "blend_mode", self._normal_blend_mode)
        if self._blend_mode_type is not None and isinstance(raw_blend_mode, self._blend_mode_type):
            blend_mode = raw_blend_mode
        elif self._normal_blend_mode is not None:
            blend_mode = self._normal_blend_mode
        else:
            blend_mode = raw_blend_mode
        image = layer.image.copy() if layer.image is not None else None
        mask = layer.layer_mask.copy() if layer.layer_mask is not None else None
        filter_mask = getattr(layer, "filter_mask", getattr(layer, "effect_mask", None))
        mask_copy = filter_mask.copy() if filter_mask is not None else None
        new_layer = self._make_layer(
            id=layer.id,
            name=layer.name,
            visible=layer.visible,
            opacity=float(layer.opacity),
            blend_mode=blend_mode,
            image=image,
            locked=layer.locked,
            alpha_lock=getattr(layer, "alpha_lock", False),
            layer_type=getattr(layer, "layer_type", "raster"),
            vector_kind=getattr(layer, "vector_kind", "draw"),
            vector_objects=list(getattr(layer, "vector_objects", []) or []),
            clipping=getattr(layer, "clipping", False),
            layer_mask=mask,
            mask_enabled=getattr(layer, "mask_enabled", True),
            effect_type=getattr(layer, "filter_type", getattr(layer, "effect_type", None)),
            effect_params=dict(
                getattr(layer, "filter_params", getattr(layer, "effect_params", {})) or {}
            ),
            effect_mask=mask_copy,
            parent_id=getattr(layer, "parent_id", None),
        )
        new_layer.ai_effect = getattr(layer, "ai_effect", None)
        new_layer.origin = getattr(layer, "origin", None)
        # CT-07 T3a: persisted layer metadata (e.g. the master instance
        # marker) must survive the replace copy like every other field.
        source_metadata = getattr(layer, "metadata", None)
        new_layer.metadata = dict(source_metadata) if isinstance(source_metadata, dict) else None
        new_layer.scene_3d = getattr(layer, "scene_3d", None)
        new_layer.camera_3d = getattr(layer, "camera_3d", None)
        new_layer.lighting_3d = getattr(layer, "lighting_3d", None)
        new_layer.display_mode_3d = str(getattr(layer, "display_mode_3d", "solid") or "solid")
        new_layer.gen_3d = getattr(layer, "gen_3d", None)
        new_layer.offset_x = int(getattr(layer, "offset_x", 0))
        new_layer.offset_y = int(getattr(layer, "offset_y", 0))
        new_layer.transform = getattr(layer, "transform", self._identity_transform_value())
        new_layer.transform_version = int(getattr(layer, "transform_version", 0))
        new_layer.texture_version = int(getattr(layer, "texture_version", 0))
        native = getattr(layer, "native_cmyk", None)
        if native is not None:
            new_layer.native_cmyk = native.copy()
        children = getattr(layer, "children", [])
        if children:
            new_layer.children = [self.copy_layer_for_replace(c) for c in children]
        return new_layer

    def _emit_replace_layers_doc_changed(self) -> None:
        if self._document_change_event_type is None:
            return
        payload = self._document_change_event_type(
            change_type="replace_layers",
            affected_area=(0, 0, self._canvas._width, self._canvas._height),
            layer_id=(
                self._canvas._layers[self._canvas._active_index].id
                if self._canvas._layers
                else None
            ),
            source="canvas",
            timestamp=time.time(),
        )
        self._canvas._emit_doc_changed(payload)

    def _identity_transform_value(self) -> Any:
        if self._identity_transform is None:
            return None
        return self._identity_transform()

    def _make_layer(self, **kwargs: Any) -> Any:
        if self._layer_type is None:
            raise RuntimeError("LayerCreationService requires a layer_type factory")
        return self._layer_type(**kwargs)

    @staticmethod
    def layer_ref(layer: Any) -> LayerRef:
        return LayerRef(
            id=layer.id,
            name=layer.name,
            layer_type=layer.layer_type,
            vector_kind=layer.vector_kind,
        )


class LayerTreeService:
    """Facade for layer query, mutation, and removal helpers."""

    def __init__(
        self,
        canvas: Any,
        *,
        layer_type: type[Any] | None = None,
        document_change_event_type: Callable[..., Any] | None = None,
        blend_mode_type: type[Any] | None = None,
        normal_blend_mode: Any | None = None,
        identity_transform: Callable[[], Any] | None = None,
    ) -> None:
        self._query = LayerTreeQueryService(canvas)
        self._creation = LayerCreationService(
            canvas,
            self._query,
            layer_type=layer_type,
            document_change_event_type=document_change_event_type,
            blend_mode_type=blend_mode_type,
            normal_blend_mode=normal_blend_mode,
            identity_transform=identity_transform,
        )
        self._mutation = LayerTreeMutationService(canvas, self._query)
        self._removal = LayerRemovalService(canvas, self._query)

    def current_layer_ref(self) -> LayerRef:
        return self._query.current_layer_ref()

    def layer_tree_snapshot(self) -> LayerTreeSnapshot:
        return self._query.layer_tree_snapshot()

    def flatten_layers(self, root: list[Any] | None = None) -> list[Any]:
        return self._query.flatten_layers(root)

    def find_layer_by_id_tree(self, layer_id: str, root: list[Any] | None = None) -> Any | None:
        return self._query.find_layer_by_id_tree(layer_id, root)

    def get_active_layer(self) -> Any | None:
        return self._query.get_active_layer()

    def find_parent(
        self,
        layer_id: str,
        root: list[Any] | None = None,
    ) -> tuple[list[Any], int] | None:
        return self._query.find_parent(layer_id, root)

    def walk_tree(
        self,
        visitor: Callable[[Any, int], None],
        root: list[Any] | None = None,
        *,
        depth: int = 0,
    ) -> None:
        self._query.walk_tree(visitor, root, depth=depth)

    def add_layer(
        self,
        name: str,
        *,
        layer_type: str = "raster",
        vector_kind: str = "draw",
    ) -> LayerRef:
        return self._creation.add_layer(name, layer_type=layer_type, vector_kind=vector_kind)

    def add_mask_layer(self, name: str | None = None) -> LayerRef:
        return self._creation.add_mask_layer(name)

    def add_vector_layer(
        self,
        name: str | None = None,
        *,
        vector_kind: str = "draw",
    ) -> LayerRef:
        return self._creation.add_vector_layer(name, vector_kind=vector_kind)

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
        return self._creation.add_3d_layer(
            name,
            scene=scene,
            camera=camera,
            lighting=lighting,
            display_mode=display_mode,
            gen_3d=gen_3d,
            origin=origin,
        )

    def add_group_layer(self, name: str | None = None) -> LayerRef:
        return self._creation.add_group_layer(name)

    def restore_vector_layer(
        self,
        *,
        layer_id: str,
        name: str | None = None,
        vector_kind: str = "draw",
        index: int | None = None,
    ) -> LayerRef | None:
        return self._creation.restore_vector_layer(
            layer_id=layer_id,
            name=name,
            vector_kind=vector_kind,
            index=index,
        )

    def replace_layers(self, layers: Sequence[Any]) -> None:
        self._creation.replace_layers(layers)

    def copy_layer_for_replace(self, layer: Any) -> Any:
        return self._creation.copy_layer_for_replace(layer)

    def set_active_layer(self, layer_id: str) -> None:
        self._mutation.set_active_layer(layer_id)

    def move_layer(self, from_index: int, to_index: int) -> bool:
        return self._mutation.move_layer(from_index, to_index)

    def group_layers(self, layer_ids: list[str], name: str | None = None) -> str | None:
        return self._mutation.group_layers(layer_ids, name=name)

    def ungroup_layer(self, group_id: str) -> bool:
        return self._mutation.ungroup_layer(group_id)

    def reparent_layer(
        self,
        layer_id: str,
        new_parent_id: str | None,
        position: int = -1,
        *,
        _membership_restore: tuple[Any, ...] | None = None,
        _part_move: bool = False,
    ) -> bool:
        return self._mutation.reparent_layer(
            layer_id,
            new_parent_id,
            position,
            _membership_restore=_membership_restore,
            _part_move=_part_move,
        )

    def remove_layer(self, layer_id: str) -> bool:
        return self._removal.remove_layer(layer_id)

    def remove_layer_by_index(self, index: int) -> bool:
        return self._removal.remove_layer_by_index(index)

    def remove_layers(self, layer_ids: list[str]) -> bool:
        return self._removal.remove_layers(layer_ids)
