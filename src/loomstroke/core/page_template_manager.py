"""Page template assembly helpers for PageService."""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from typing import Any
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray

from ..host_api import CanvasSpec as HostCanvasSpec
from ..host_api import PageTemplateSummary
from ..io.layer_io import CanvasSpec as PersistCanvasSpec
from ..io.layer_io import (
    LayerSnapshot,
    PageSnapshot,
    deserialize_vector_objects,
    serialize_vector_objects,
)
from .page_templates import (
    LayerTemplate,
    PageTemplate,
    PageTemplateStore,
    TemplateCaptureOptions,
)
from .template_raster_codec import TemplateRasterCodec

logger = logging.getLogger(__name__)

LayerIdFactory = Callable[[], str]


class PageTemplateManager:
    """Owns page template lookup, validation, and snapshot construction."""

    def __init__(self, template_store: PageTemplateStore | None = None) -> None:
        self._template_store = template_store or PageTemplateStore()

    @property
    def template_store(self) -> PageTemplateStore:
        """The backing store (CT-07 T2: library operations go through it)."""
        return self._template_store

    def list_summaries(self) -> Sequence[PageTemplateSummary]:
        return self._template_store.list_summaries()

    def get(self, template_id: str) -> PageTemplate | None:
        return self._template_store.get(template_id)

    @staticmethod
    def validate_template_name(name: str) -> str:
        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("name is required")
        if len(normalized_name) > 64:
            raise ValueError("name too long (max 64)")
        if any(ord(ch) < 32 for ch in normalized_name):
            raise ValueError("name contains control characters")
        return normalized_name

    def resolve_canvas_spec(
        self,
        template: PageTemplate,
        *,
        document_canvas: HostCanvasSpec | None,
        active_canvas_spec: HostCanvasSpec,
        fallback_canvas_spec: HostCanvasSpec,
    ) -> HostCanvasSpec:
        if template.canvas_mode == "fixed":
            if template.canvas is None:
                raise ValueError("template canvas is required for fixed canvas_mode")
            return template.canvas
        if template.canvas_mode == "document_default":
            if document_canvas is not None:
                return document_canvas
            return fallback_canvas_spec
        return active_canvas_spec

    def create_page_snapshot(
        self,
        template: PageTemplate,
        *,
        page_id: str,
        base_page: PageSnapshot | None,
        canvas_spec: HostCanvasSpec,
        new_layer_id: LayerIdFactory,
    ) -> PageSnapshot:
        layers = None
        if template.id == "builtin.blank_like_current" and base_page is not None:
            from .comic_panels.blank_page import blank_comic_layers  # noqa: PLC0415

            layers = blank_comic_layers(
                base_page.layers, page_id=page_id, new_layer_id=new_layer_id
            )
        if layers is None:
            layers = self._create_layers_from_template(
                template,
                base_page,
                canvas_spec,
                new_layer_id,
            )
        return PageSnapshot(
            id=page_id,
            name=None,
            index=None,
            layers=layers,
            canvas=self._page_canvas_override_for_template(template),
            page_thumbnail=None,
            metadata=self._build_template_metadata(template),
            view_state=None,
        )

    def save_page_as_template(
        self,
        page: PageSnapshot,
        name: str,
        *,
        canvas_spec: HostCanvasSpec,
        options: TemplateCaptureOptions | None = None,
        page_view_state: Mapping[str, Any] | None = None,
    ) -> PageTemplate:
        template = self._build_template_from_page(
            page,
            name,
            canvas_spec,
            options=options or TemplateCaptureOptions(),
            page_view_state=page_view_state,
        )
        return self._template_store.save_user_template(template)

    def _page_canvas_override_for_template(
        self,
        template: PageTemplate,
    ) -> PersistCanvasSpec | None:
        if template.canvas_mode != "fixed" or template.canvas is None:
            return None
        return PersistCanvasSpec(
            width=template.canvas.width,
            height=template.canvas.height,
            dpi=template.canvas.dpi,
            background_color=template.canvas.background_color,
        )

    def _create_layers_from_template(
        self,
        template: PageTemplate,
        base_page: PageSnapshot | None,
        canvas_spec: HostCanvasSpec,
        new_layer_id: LayerIdFactory,
    ) -> list[LayerSnapshot]:
        if template.id == "builtin.blank_like_current":
            return self._create_blank_layers(base_page, canvas_spec, new_layer_id)
        if not template.layers:
            return self._default_blank_layers(canvas_spec, new_layer_id)
        layers: list[LayerSnapshot] = []
        for layer_template in template.layers:
            layers.append(
                self._create_layer_from_template(
                    layer_template,
                    canvas_spec,
                    new_layer_id,
                )
            )
        return layers

    def _create_layer_from_template(
        self,
        layer_template: LayerTemplate,
        canvas_spec: HostCanvasSpec,
        new_layer_id: LayerIdFactory,
    ) -> LayerSnapshot:
        image = self._blank_image(canvas_spec.width, canvas_spec.height)
        if layer_template.fill == "white":
            image[..., :3] = 255
            image[..., 3] = 255
        if layer_template.raster_png:
            # CT-07 T2: captured artwork, placed at the canvas origin.
            try:
                image = TemplateRasterCodec.fit_to_canvas(
                    TemplateRasterCodec.decode(layer_template.raster_png),
                    canvas_spec.width,
                    canvas_spec.height,
                )
            except Exception:
                logger.warning(
                    "template raster could not be decoded; blank layer used", exc_info=True
                )
        return LayerSnapshot(
            id=new_layer_id(),
            name=layer_template.name,
            image=image,
            visible=bool(layer_template.visible),
            opacity=float(layer_template.opacity),
            blend_mode=str(layer_template.blend_mode),
            locked=bool(layer_template.locked),
            position=(0, 0),
            metadata=(
                dict(layer_template.metadata)
                if isinstance(layer_template.metadata, Mapping)
                else None
            ),
            layer_type=str(layer_template.layer_type),
            vector_objects=self._live_vector_objects(layer_template.vector_objects),
        )

    @staticmethod
    def _live_vector_objects(stored: Sequence[Any]) -> list[Any]:
        """Template payloads hold dicts; the canvas needs live objects.
        Non-dict entries pass through for in-memory templates that never
        crossed the JSON store."""
        dict_entries = [entry for entry in stored or () if isinstance(entry, Mapping)]
        live = deserialize_vector_objects(dict_entries) if dict_entries else []
        passthrough = [entry for entry in stored or () if not isinstance(entry, Mapping)]
        return live + passthrough

    def _create_blank_layers(
        self,
        base_page: PageSnapshot | None,
        canvas_spec: HostCanvasSpec,
        new_layer_id: LayerIdFactory,
    ) -> list[LayerSnapshot]:
        if base_page is None or not base_page.layers:
            return self._default_blank_layers(canvas_spec, new_layer_id)
        layers: list[LayerSnapshot] = []
        for layer in base_page.layers:
            image = self._blank_image(canvas_spec.width, canvas_spec.height)
            layers.append(
                LayerSnapshot(
                    id=new_layer_id(),
                    name=layer.name,
                    image=image,
                    visible=layer.visible,
                    opacity=float(layer.opacity),
                    blend_mode=str(layer.blend_mode),
                    locked=layer.locked,
                    position=layer.position,
                    metadata=dict(layer.metadata) if isinstance(layer.metadata, dict) else None,
                )
            )
        return layers

    def _default_blank_layers(
        self,
        canvas_spec: HostCanvasSpec,
        new_layer_id: LayerIdFactory,
    ) -> list[LayerSnapshot]:
        layers: list[LayerSnapshot] = []
        for name in ("Background", "Layer 1"):
            layers.append(
                LayerSnapshot(
                    id=new_layer_id(),
                    name=name,
                    image=self._blank_image(canvas_spec.width, canvas_spec.height),
                    visible=True,
                    opacity=1.0,
                    blend_mode="normal",
                    locked=False,
                    position=(0, 0),
                    metadata=None,
                )
            )
        return layers

    def _build_template_metadata(self, template: PageTemplate) -> dict[str, Any]:
        metadata: dict[str, Any]
        metadata = dict(template.metadata) if isinstance(template.metadata, Mapping) else {}
        plugins = metadata.get("plugins")
        if isinstance(plugins, Mapping):
            plugins_payload: dict[str, Any] = dict(plugins)
        else:
            plugins_payload = {}
        page_panel_meta = plugins_payload.get("page_panel")
        if isinstance(page_panel_meta, Mapping):
            page_panel_payload: dict[str, Any] = dict(page_panel_meta)
        else:
            page_panel_payload = {}
        page_panel_payload["template_id"] = template.id
        page_panel_payload["template_name"] = template.name
        page_panel_payload["template_source"] = template.source
        plugins_payload["page_panel"] = page_panel_payload
        metadata["plugins"] = plugins_payload
        return metadata

    def _build_template_from_page(
        self,
        page: PageSnapshot,
        name: str,
        canvas_spec: HostCanvasSpec,
        *,
        options: TemplateCaptureOptions | None = None,
        page_view_state: Mapping[str, Any] | None = None,
    ) -> PageTemplate:
        capture = options or TemplateCaptureOptions()
        template_id = self._next_custom_template_id(name)
        include_raster = bool(getattr(capture, "include_raster_artwork", False))
        layers = tuple(
            self._layer_template_from_snapshot(layer, include_raster=include_raster)
            for layer in page.layers
        )
        if not capture.include_vector_objects:
            layers = tuple(replace(layer, vector_objects=()) for layer in layers)
        metadata: dict[str, Any] | None = None
        if capture.include_guides and isinstance(page_view_state, Mapping):
            guides = page_view_state.get("guides")
            if isinstance(guides, list) and guides:
                # The same key add_page_from_template materializes back
                # into the new page's view state (CT-07 T1b).
                metadata = {"view_state": {"guides": [dict(entry) for entry in guides]}}
        if include_raster:
            # CT-07 T2 §11: a 256 px preview so the list never decodes artwork.
            preview = TemplateRasterCodec.preview_png(
                [layer.image for layer in page.layers if getattr(layer, "visible", True)],
                background=tuple(int(v) for v in canvas_spec.background_color),  # type: ignore[arg-type]
            )
            if preview:
                metadata = dict(metadata or {})
                metadata["preview_png"] = TemplateRasterCodec.to_base64(preview)
        return PageTemplate(
            id=template_id,
            name=name,
            category="custom",
            source="user",
            canvas_mode="fixed",
            canvas=canvas_spec,
            layers=layers,
            description=None,
            metadata=metadata,
        )

    @staticmethod
    def estimate_raster_capture_bytes(page: PageSnapshot) -> int:
        """Uncompressed size of the artwork a raster capture would take along."""
        return TemplateRasterCodec.estimate_bytes(
            [
                getattr(layer, "image", None)
                for layer in getattr(page, "layers", ()) or ()
                if str(getattr(layer, "layer_type", "raster")) == "raster"
            ]
        )

    @staticmethod
    def template_preview_png(template: PageTemplate | None) -> bytes | None:
        metadata = getattr(template, "metadata", None)
        if not isinstance(metadata, Mapping):
            return None
        encoded = metadata.get("preview_png")
        return TemplateRasterCodec.from_base64(encoded) if isinstance(encoded, str) else None

    def _layer_template_from_snapshot(
        self, layer: LayerSnapshot, *, include_raster: bool = False
    ) -> LayerTemplate:
        layer_type_raw = str(getattr(layer, "layer_type", "raster")).strip().lower()
        layer_type = layer_type_raw if layer_type_raw in {"raster", "vector"} else "raster"
        vector_objects_raw = getattr(layer, "vector_objects", ())
        # Live VectorObject instances are not JSON serializable; store the
        # same dict form .lstroke uses so the template survives the JSON
        # store (this crash silently ate every vector-page save until
        # 2026-08-03).
        vector_objects = tuple(serialize_vector_objects(tuple(vector_objects_raw or ())))
        return LayerTemplate(
            name=(layer.name or "").strip() or "Layer",
            visible=bool(layer.visible),
            opacity=float(layer.opacity),
            blend_mode=str(layer.blend_mode),
            locked=bool(layer.locked),
            layer_type=layer_type,
            fill=self._detect_layer_fill(layer.image),
            vector_objects=vector_objects,
            metadata=dict(layer.metadata) if isinstance(layer.metadata, Mapping) else None,
            raster_png=(
                TemplateRasterCodec.encode(layer.image)
                if include_raster
                and layer_type == "raster"
                and TemplateRasterCodec.has_artwork(layer.image)
                and self._detect_layer_fill(layer.image) != "white"
                else None
            ),
        )

    def _detect_layer_fill(self, image: NDArray[Any]) -> str:
        arr = np.asarray(image)
        if arr.ndim != 3 or arr.size == 0:
            return "transparent"
        if arr.shape[2] < 4:
            return "transparent"
        if np.all(arr[:, :, :3] == 255) and np.all(arr[:, :, 3] == 255):
            return "white"
        return "transparent"

    def _next_custom_template_id(self, name: str) -> str:
        prefix = "custom."
        slug = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
        if not slug:
            slug = "template"

        existing = {template.id for template in self._template_store.list_templates()}
        max_slug_length = max(1, 64 - len(prefix))
        slug = slug[:max_slug_length]
        candidate = f"{prefix}{slug}"
        if candidate not in existing:
            return candidate

        for idx in range(2, 1000):
            suffix = f"_{idx}"
            allowed = max(1, 64 - len(prefix) - len(suffix))
            candidate = f"{prefix}{slug[:allowed]}{suffix}"
            if candidate not in existing:
                return candidate

        while True:
            candidate = f"{prefix}{uuid4().hex[:8]}"
            if candidate not in existing:
                return candidate

    def _blank_image(self, width: int, height: int) -> NDArray[np.uint8]:
        return np.zeros((height, width, 4), dtype=np.uint8)


__all__ = ["PageTemplateManager"]
