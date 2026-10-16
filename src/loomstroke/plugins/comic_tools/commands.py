"""Undoable comic edit command.

One :class:`ComicEditCommand` is one user-visible edit (split, merge, gutter
change, balloon create/edit, ...): it can ensure managed comic layers, swap
the frames-layer layout metadata, and add/replace/remove vector objects —
all reverted together by a single Undo.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from loomstroke.core.commands import Command


@dataclass(frozen=True)
class EnsureLayerSpec:
    layer_id: str
    name: str
    vector_kind: str = "shape"
    metadata: dict[str, Any] | None = None


class ComicEditCommand(Command):
    """Apply a batch of comic layer/vector mutations as one undo unit."""

    #: routes through the canvas vector-command submit path (same marker
    #: contract as ``ComicApplyCommand``)
    vector_object = object()
    _layer_id = "__loomstroke_comic_tools__"

    def __init__(
        self,
        *,
        label: str = "comic_edit",
        ensure_layers: Sequence[EnsureLayerSpec] = (),
        layer_metadata_updates: Mapping[str, Mapping[str, Any]] | None = None,
        additions: Sequence[tuple[str, Any]] = (),
        replacements: Sequence[tuple[str, Any]] = (),
        removals: Sequence[str] = (),
        layer_visibility_updates: Mapping[str, bool] | None = None,
        raster_parts: Sequence[Any] = (),
        raster_part_removals: Sequence[str] = (),
        preserve_existing_raster_parts: Sequence[str] = (),
    ) -> None:
        super().__init__()
        self.label = label
        self._ensure_layers = list(ensure_layers)
        #: layer_id -> full new metadata root (deep-copied)
        self._layer_metadata_updates = {
            str(layer_id): copy.deepcopy(dict(payload))
            for layer_id, payload in dict(layer_metadata_updates or {}).items()
        }
        self._additions = [(str(layer_id), obj) for layer_id, obj in additions]
        self._replacements = [(str(layer_id), obj) for layer_id, obj in replacements]
        self._removals = [str(object_id) for object_id in removals]
        #: e.g. hide the hand-drawn source layer after balloonize (element of
        #: the same undo unit; the source is never deleted)
        self._layer_visibility_updates = {
            str(layer_id): bool(visible)
            for layer_id, visible in dict(layer_visibility_updates or {}).items()
        }
        #: raster-preserve part layers: BalloonRecords whose body/tail crops
        #: are (re)blitted onto the balloon's dedicated raster layer
        self._raster_parts = list(raster_parts)
        self._raster_part_removals = [str(bid) for bid in raster_part_removals]
        self._preserve_existing_raster_parts = set(preserve_existing_raster_parts)

        # undo state captured on first apply
        self._created_layer_ids: list[str] = []
        self._metadata_before: dict[str, Any] = {}
        self._added: list[tuple[str, int, str]] = []
        self._replaced: list[tuple[str, str, Any]] = []
        self._story_replacements: list[Any] = []
        self._removed: list[tuple[str, int, Any]] = []
        self._visibility_before: dict[str, bool] = {}
        self._part_images_before: dict[str, Any] = {}
        self._part_layers_created: list[str] = []
        self._part_insertions: dict[str, Any] = {}
        self._part_layers_removed: list[tuple[int, Any]] = []

    # ── helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _get_layer(canvas: Any, layer_id: str) -> Any | None:
        for getter_name in ("_get_layer_by_id", "get_layer_by_id"):
            getter = getattr(canvas, getter_name, None)
            if callable(getter):
                layer = getter(layer_id)
                if layer is not None:
                    return layer
        return None

    def _find_object(self, canvas: Any, object_id: str) -> tuple[Any, int, Any] | None:
        layers = getattr(canvas, "_layers", None) or []
        stack = list(layers)
        while stack:
            layer = stack.pop()
            children = getattr(layer, "children", None) or []
            stack.extend(children)
            objects = getattr(layer, "vector_objects", None) or []
            for index, obj in enumerate(objects):
                if getattr(obj, "id", None) == object_id:
                    return layer, index, obj
        return None

    # ── Command protocol ───────────────────────────────────────────────

    def apply(self, canvas: Any) -> bool:  # noqa: PLR0912, PLR0915
        from .balloon_layer_units import unit_edits_allowed  # noqa: PLC0415

        if not unit_edits_allowed(
            canvas, {obj.id for _, obj in self._replacements} | set(self._removals)
        ):
            return False
        self._created_layer_ids = []
        self._metadata_before = {}
        self._added = []
        self._replaced = []
        self._removed = []
        touched_layers: set[str] = set()
        try:
            for spec in self._ensure_layers:
                if self._get_layer(canvas, spec.layer_id) is not None:
                    continue
                ref = canvas.restore_vector_layer(
                    layer_id=spec.layer_id, name=spec.name, vector_kind=spec.vector_kind
                )
                if ref is None:
                    raise RuntimeError(f"could not create comic layer {spec.layer_id}")
                layer = self._get_layer(canvas, spec.layer_id)
                if layer is not None and spec.metadata is not None:
                    layer.metadata = copy.deepcopy(spec.metadata)
                self._created_layer_ids.append(spec.layer_id)

            for layer_id, payload in self._layer_metadata_updates.items():
                layer = self._get_layer(canvas, layer_id)
                if layer is None:
                    raise RuntimeError(f"missing comic layer {layer_id}")
                if layer_id not in self._created_layer_ids:
                    self._metadata_before[layer_id] = copy.deepcopy(layer.metadata)
                layer.metadata = copy.deepcopy(payload)

            for object_id in self._removals:
                found = self._find_object(canvas, object_id)
                if found is None:
                    continue
                layer, index, obj = found
                canvas.remove_vector_object(layer.id, index, recompose=False, vec_id=object_id)
                self._removed.append((layer.id, index, obj))
                touched_layers.add(layer.id)

            for requested_layer_id, obj in self._replacements:
                found = self._find_object(canvas, str(obj.id))
                layer_id = found[0].id if found is not None else requested_layer_id
                from loomstroke.core.japanese_text.story_commands import (  # noqa: PLC0415
                    prepare_story_replacement,
                )

                story = prepare_story_replacement(canvas, str(obj.id), obj)
                if story is not None:
                    if not story.apply(canvas):
                        raise RuntimeError("linked comic text update failed")
                    self._story_replacements.append(story)
                    continue
                result = canvas.replace_vector_object(
                    getattr(obj, "id", ""), obj, layer_id=layer_id or None, recompose=False
                )
                if result is None:
                    # replacement target vanished -> treat as addition
                    added = canvas.add_vector_object(obj, layer_id=layer_id, recompose=False)
                    if added is None:
                        raise RuntimeError("vector object insert failed")
                    self._added.append((added[0], added[1], getattr(obj, "id", "")))
                    touched_layers.add(added[0])
                    continue
                actual_layer_id, _index, previous = result
                self._replaced.append((actual_layer_id, getattr(obj, "id", ""), previous))
                touched_layers.add(actual_layer_id)

            for layer_id, obj in self._additions:
                added = canvas.add_vector_object(obj, layer_id=layer_id, recompose=False)
                if added is None:
                    raise RuntimeError("vector object insert failed")
                self._added.append((added[0], added[1], getattr(obj, "id", "")))
                touched_layers.add(added[0])

            self._apply_raster_parts(canvas, touched_layers)

            for layer_id, visible in self._layer_visibility_updates.items():
                layer = self._get_layer(canvas, layer_id)
                if layer is None:
                    continue
                self._visibility_before[layer_id] = bool(getattr(layer, "visible", True))
                layer.visible = bool(visible)
                touched_layers.add(layer_id)

            self._bump(canvas, touched_layers | set(self._layer_metadata_updates))
            self._executed = True
            return True
        except Exception:
            self._rollback(canvas)
            return False

    def revert(self, canvas: Any) -> bool:
        if not self._executed:
            return False
        self._rollback(canvas)
        self._executed = False
        return True

    # ── rollback (shared by failed apply and undo) ─────────────────────

    def _rollback(self, canvas: Any) -> None:
        touched: set[str] = set()
        for story in reversed(self._story_replacements):
            story.revert(canvas)
        self._story_replacements = []
        for layer_id, index, object_id in reversed(self._added):
            canvas.remove_vector_object(layer_id, index, recompose=False, vec_id=object_id)
            touched.add(layer_id)
        for layer_id, object_id, previous in reversed(self._replaced):
            canvas.replace_vector_object(object_id, previous, layer_id=layer_id, recompose=False)
            touched.add(layer_id)
        for layer_id, index, obj in reversed(self._removed):
            layer = self._get_layer(canvas, layer_id)
            if layer is not None:
                objects = getattr(layer, "vector_objects", None)
                if objects is not None:
                    objects.insert(min(index, len(objects)), obj)
                    touched.add(layer_id)
        for layer_id, before in self._metadata_before.items():
            layer = self._get_layer(canvas, layer_id)
            if layer is not None:
                layer.metadata = copy.deepcopy(before)
        for layer_id in reversed(self._created_layer_ids):
            remove_layer = getattr(canvas, "remove_layer", None)
            if callable(remove_layer):
                remove_layer(layer_id)
        self._bump(canvas, touched)
        for layer_id, visible in self._visibility_before.items():
            layer = self._get_layer(canvas, layer_id)
            if layer is not None:
                layer.visible = visible
                touched.add(layer_id)
        self._rollback_raster_parts(canvas, touched)
        self._added = []
        self._replaced = []
        self._removed = []
        self._metadata_before = {}
        self._created_layer_ids = []
        self._visibility_before = {}

    # -- raster preserve part layers (Phase BZ3) ------------------------

    def _apply_raster_parts(self, canvas: Any, touched: set[str]) -> None:
        if not self._raster_parts and not self._raster_part_removals:
            return
        from .raster_parts import (  # noqa: PLC0415
            compose_part_layer_image,
            find_part_layer,
        )

        width, height = canvas.get_canvas_size()
        for record in self._raster_parts:
            balloon_id = record.balloon_id
            layer = find_part_layer(canvas, balloon_id)
            if layer is not None and balloon_id in self._preserve_existing_raster_parts:
                continue
            image = compose_part_layer_image(record, width, height)
            if layer is None:
                from .raster_part_creation import create_part_layer  # noqa: PLC0415

                layer = create_part_layer(canvas, balloon_id, image, self._part_insertions)
                self._part_layers_created.append(layer.id)
                self._reorder_below_comic_text(canvas, layer)
            elif balloon_id not in self._part_images_before:
                self._part_images_before[balloon_id] = layer.image.copy()
            layer.image = image
            touched.add(layer.id)
        for balloon_id in self._raster_part_removals:
            layer = find_part_layer(canvas, balloon_id)
            if layer is None:
                continue
            layers = getattr(canvas, "_layers", None) or []
            index = next((i for i, item in enumerate(layers) if item is layer), None)
            if index is None:
                continue
            self._part_layers_removed.append((index, layer))
            canvas.remove_layer(layer.id)

    @staticmethod
    def _reorder_below_comic_text(canvas: Any, part_layer: Any) -> None:
        """Keep the balloon text vector layer above the raster pixels."""

        from .model import LAYER_ROLE_BALLOON_TEXT, layer_comic_role  # noqa: PLC0415

        layers = getattr(canvas, "_layers", None) or []
        part_index = next((i for i, item in enumerate(layers) if item is part_layer), None)
        text_index = next(
            (
                i
                for i, item in enumerate(layers)
                if layer_comic_role(item) == LAYER_ROLE_BALLOON_TEXT
            ),
            None,
        )
        move = getattr(canvas, "move_layer", None)
        if (
            part_index is not None
            and text_index is not None
            and part_index > text_index
            and callable(move)
        ):
            move(part_index, text_index)

    def _rollback_raster_parts(self, canvas: Any, touched: set[str]) -> None:
        from .raster_parts import find_part_layer  # noqa: PLC0415

        for balloon_id, image in self._part_images_before.items():
            layer = find_part_layer(canvas, balloon_id)
            if layer is not None:
                layer.image = image
                touched.add(layer.id)
        for layer_id in reversed(self._part_layers_created):
            remove_layer = getattr(canvas, "remove_layer", None)
            if callable(remove_layer):
                remove_layer(layer_id)
        for index, layer in reversed(self._part_layers_removed):
            layers = getattr(canvas, "_layers", None)
            if layers is not None:
                layers.insert(min(index, len(layers)), layer)
                touched.add(layer.id)
        self._part_images_before = {}
        self._part_layers_created = []
        self._part_layers_removed = []

    @staticmethod
    def _bump(canvas: Any, layer_ids: set[str]) -> None:
        bump = getattr(canvas, "bump_layer_texture_versions", None)
        if callable(bump) and layer_ids:
            bump(sorted(layer_ids))


__all__ = ["ComicEditCommand", "EnsureLayerSpec"]
