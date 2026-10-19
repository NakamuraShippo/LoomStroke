"""Connect editable vector/text raster snapshots to the existing native tile compositor."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication, QImage

from loomstroke.core.snapshot_vector_rasterizer import SnapshotVectorRasterizer

from .native_cmyk_selection_scene import project_selection
from .native_cmyk_vector_masks import NativeCmykVectorMasks
from .native_cmyk_vector_source import NativeCmykVectorSource
from .render_scene import RenderLayerSnapshot

if TYPE_CHECKING:
    from .render_scene import RenderSceneSnapshot


class NativeCmykVectorScene:
    def __init__(self) -> None:
        self._sources: dict[str, NativeCmykVectorSource] = {}
        self._masks = NativeCmykVectorMasks()

    def prepare(self, scene: RenderSceneSnapshot) -> RenderSceneSnapshot:
        return self._prepare(project_selection(scene))

    def _prepare(self, scene: RenderSceneSnapshot) -> RenderSceneSnapshot:
        native = next(
            (node.native_cmyk for node in scene.flat_layers if node.native_cmyk is not None), None
        )
        if native is None:
            self._sources.clear()
            self._masks.retain(())
            return scene
        scratch = SnapshotVectorRasterizer(width=scene.width, height=scene.height)
        retained: dict[str, NativeCmykVectorSource] = {}
        flat: list[RenderLayerSnapshot] = []

        def source_for(
            identifier: str,
            image: QImage,
            bounds: QRect,
            origin: tuple[int, int],
            color: tuple[int, ...] | None = None,
            *,
            bounded: bool = False,
        ) -> NativeCmykVectorSource:
            source = self._sources.get(identifier)
            if (
                source is None
                or source.profile != native.profile
                or source.dtype != native.inks.dtype
                or source.logical_size != (scene.width, scene.height)
                or source._bounded != bounded
            ):
                source = NativeCmykVectorSource(
                    identifier,
                    native.profile,
                    native.inks.dtype,
                    (scene.width, scene.height),
                    bounded=bounded,
                )
            source.update(image, bounds, origin, color)
            retained[identifier] = source
            return source

        def prepare(node: RenderLayerSnapshot) -> RenderLayerSnapshot:
            result = replace(node, children=[])
            if node.layer_type in ("vector", "text") and node.native_cmyk is None:
                image = node.image
                origin_x, origin_y = node.vector_raster_origin
                if image is None:
                    if any(
                        getattr(obj, "paths", ()) or scratch._extract_text_payload(obj) is not None
                        for obj in node.vector_objects
                    ):
                        raise ValueError("CMYK vector/text rendering requires a prepared raster")
                    result.visible = False
                if image is not None:
                    extra: tuple[Any, ...] = ()
                    if (
                        scene.vector.preview_object is not None
                        and scene.vector.preview_layer_id in (None, node.id)
                    ):
                        extra = (scene.vector.preview_object,)
                    paints = self._masks.prepare(
                        node,
                        extra[0] if extra else None,
                        (scene.width, scene.height),
                        native.profile,
                        native.inks.dtype,
                    )
                    if paints is not None:
                        result.vector_kind = "native-cmyk-paints"
                        if len(paints) == 1:
                            paint = paints[0]
                            result.native_cmyk = source_for(
                                node.id,
                                paint.image,
                                paint.bounds,
                                paint.bounds.topLeft().toTuple(),
                                paint.color,
                                bounded=True,
                            )
                            flat.append(result)
                            return result
                        result.layer_type = "group"
                        flat.append(result)
                        for index, paint in enumerate(paints):
                            identifier = f"{node.id}:cmyk-paint-{index}"
                            child_source = source_for(
                                identifier,
                                paint.image,
                                paint.bounds,
                                paint.bounds.topLeft().toTuple(),
                                paint.color,
                                bounded=True,
                            )
                            child = RenderLayerSnapshot(
                                identifier,
                                node.name,
                                True,
                                1.0,
                                "normal",
                                child_source.version,
                                native_cmyk=child_source,
                                offset_x=node.offset_x,
                                offset_y=node.offset_y,
                                parent_id=node.id,
                            )
                            result.children.append(child)
                            flat.append(child)
                        return result
                    region = (
                        scratch._vector_text_render_service._layer_raster_region(node, extra)
                        if isinstance(QGuiApplication.instance(), QGuiApplication)
                        else None
                    )
                    bounds = QRect(*region) if region else QRect(0, 0, scene.width, scene.height)
                    source = source_for(node.id, image, bounds, (origin_x, origin_y))
                    result.native_cmyk = source
            flat.append(result)
            result.children = [prepare(child) for child in node.children]
            return result

        roots = tuple(prepare(node) for node in scene.layers)
        self._sources = retained
        self._masks.retain({node.id for node in scene.flat_layers})
        active_id = (
            scene.flat_layers[scene.active_flat_index].id
            if 0 <= scene.active_flat_index < len(scene.flat_layers)
            else None
        )
        return replace(
            scene,
            layers=roots,
            flat_layers=tuple(flat),
            parent_ids=tuple(node.parent_id for node in flat),
            active_flat_index=next((i for i, node in enumerate(flat) if node.id == active_id), -1),
        )
