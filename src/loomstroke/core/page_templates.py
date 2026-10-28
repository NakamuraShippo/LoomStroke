"""Page template model and storage helpers."""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..host_api import CanvasSpec as HostCanvasSpec
from ..host_api import PageTemplateSummary
from ..io.atomic_export_writer import write_atomically
from ..platform.user_dirs import default_user_root
from .template_raster_codec import TemplateRasterCodec

logger = logging.getLogger(__name__)

_TEMPLATE_FILE_NAME = "page_templates.json"
_TEMPLATE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_ALLOWED_CANVAS_MODES = {"inherit_active", "document_default", "fixed"}
_ALLOWED_LAYER_TYPES = {"raster", "vector"}
_ALLOWED_FILL_MODES = {"transparent", "white"}


@dataclass(frozen=True, slots=True)
class LayerTemplate:
    """Layer definition used when creating a page from a template."""

    name: str
    visible: bool = True
    opacity: float = 1.0
    blend_mode: str = "normal"
    locked: bool = False
    layer_type: str = "raster"
    fill: str = "transparent"
    vector_objects: tuple[Any, ...] = ()
    metadata: Mapping[str, Any] | None = None
    #: CT-07 T2: captured artwork as PNG bytes (``None`` = blank pixels).
    raster_png: bytes | None = None


@dataclass(frozen=True, slots=True)
class TemplateCaptureOptions:
    """What ``Save Current Page as Template...`` takes along (CT-07 T2).

    ``include_raster_artwork`` stores raster layer pixels as PNG inside the
    template (off by default: the library is JSON and bloats quickly —
    the capture dialog shows the estimate and warns).
    """

    include_vector_objects: bool = True
    include_guides: bool = False
    include_raster_artwork: bool = False


@dataclass(frozen=True, slots=True)
class PageTemplate:
    """Template definition for creating new pages."""

    id: str
    name: str
    category: str
    source: str
    canvas_mode: str
    canvas: HostCanvasSpec | None = None
    layers: tuple[LayerTemplate, ...] = ()
    description: str | None = None
    metadata: Mapping[str, Any] | None = None


class PageTemplateStore:
    """Loads and provides page templates from built-in and user sources."""

    def __init__(
        self,
        template_file: Path | None = None,
        *,
        payload_override: Mapping[str, Any] | None = None,
    ) -> None:
        self._template_file = template_file or self.default_template_file()
        self._payload_override = payload_override
        self._templates: dict[str, PageTemplate] = {tpl.id: tpl for tpl in _builtin_templates()}
        self.reload()

    @staticmethod
    def default_template_file() -> Path:
        """Return the default template definition file path."""

        root = default_user_root(create=True)
        settings_dir = root / "settings"
        settings_dir.mkdir(parents=True, exist_ok=True)
        return settings_dir / _TEMPLATE_FILE_NAME

    def reload(self) -> None:
        """Reload templates from disk and refresh in-memory cache."""

        try:
            payload = (
                self._load_user_payload_for_write()
                if self._payload_override is None
                else copy.deepcopy(self._payload_override)
            )
            templates = self._templates_from_payload(payload)
        except Exception:
            logger.warning("Page template reload failed; the confirmed library remains active")
            return
        self._templates = templates

    def list_templates(self) -> Sequence[PageTemplate]:
        """Return all available templates."""

        return tuple(copy.deepcopy(template) for template in self._templates.values())

    def list_summaries(self) -> Sequence[PageTemplateSummary]:
        """Return lightweight summaries suitable for UI rendering."""

        return tuple(
            PageTemplateSummary(
                id=template.id,
                name=template.name,
                category=template.category,
                source=template.source,
                description=template.description,
            )
            for template in self._templates.values()
        )

    def get(self, template_id: str) -> PageTemplate | None:
        """Return a template by id."""

        return copy.deepcopy(self._templates.get(template_id))

    def save_user_template(
        self,
        template: PageTemplate,
        *,
        overwrite: bool = False,
    ) -> PageTemplate:
        """Persist a user template to disk and refresh the in-memory cache."""

        if self._payload_override is not None:
            raise RuntimeError("payload_override mode is read-only")

        normalized = _normalize_user_template(copy.deepcopy(template))
        if normalized.id in _builtin_template_ids():
            raise ValueError(f"template id is reserved: {normalized.id}")

        payload = self._load_user_payload_for_write()
        filtered: list[Any] = []
        for entry in payload.get("templates", []):
            if not isinstance(entry, Mapping) or str(entry.get("id", "")).strip() != normalized.id:
                filtered.append(entry)
                continue
            if not overwrite:
                raise ValueError(f"template already exists: {normalized.id}")

        filtered.append(_template_to_payload(normalized))
        payload["templates"] = filtered
        self._write_user_payload(payload, expected_id=normalized.id)
        saved = self.get(normalized.id)
        if saved is None:
            raise RuntimeError(f"failed to reload saved template: {normalized.id}")
        return saved

    # ------------------------------------------------------------------
    # CT-07 T2: user-template library operations.  All three are library
    # operations, not document edits — no Command, no undo (spec 07 §9).
    def delete_user_template(self, template_id: str) -> bool:
        """Remove a user template; built-in ids are untouchable."""
        if self._payload_override is not None:
            raise RuntimeError("payload_override mode is read-only")
        if template_id in _builtin_template_ids():
            raise ValueError(f"built-in templates cannot be deleted: {template_id}")
        payload = self._load_user_payload_for_write()
        entries = payload.get("templates", [])
        kept = [
            entry
            for entry in entries
            if not isinstance(entry, Mapping) or str(entry.get("id", "")).strip() != template_id
        ]
        if len(kept) == len(entries):
            return False
        payload["templates"] = kept
        self._write_user_payload(payload)
        return True

    def rename_user_template(self, template_id: str, new_name: str) -> PageTemplate:
        """Rename a user template in place (id stays stable)."""
        if template_id in _builtin_template_ids():
            raise ValueError(f"built-in templates cannot be renamed: {template_id}")
        existing = self.get(template_id)
        if existing is None or existing.source == "builtin":
            raise ValueError(f"user template not found: {template_id}")
        name = str(new_name).strip()
        if not name:
            raise ValueError("template name must not be empty")
        renamed = replace(existing, name=name)
        return self.save_user_template(renamed, overwrite=True)

    def set_user_template_category(self, template_id: str, category: str) -> PageTemplate:
        """Re-file a user template (CT-07 T2 §5.2 category); id stays stable."""
        if template_id in _builtin_template_ids():
            raise ValueError(f"built-in templates cannot be re-filed: {template_id}")
        existing = self.get(template_id)
        if existing is None or existing.source == "builtin":
            raise ValueError(f"user template not found: {template_id}")
        normalized = str(category).strip()
        if not normalized:
            raise ValueError("template category must not be empty")
        return self.save_user_template(replace(existing, category=normalized), overwrite=True)

    def duplicate_template(self, template_id: str, *, new_name: str | None = None) -> PageTemplate:
        """Copy any template (built-in included) into a new user template."""
        source = self.get(template_id)
        if source is None:
            raise ValueError(f"template not found: {template_id}")
        name = (new_name or f"{source.name} (Copy)").strip()
        if not name:
            raise ValueError("template name must not be empty")
        copy = replace(
            source,
            id=self._next_copy_id(template_id),
            name=name,
            source="user",
            category="custom" if source.source == "builtin" else source.category,
        )
        return self.save_user_template(copy)

    def _next_copy_id(self, template_id: str) -> str:
        base = f"{template_id}.copy"[:56]
        candidate = base
        counter = 2
        while self.get(candidate) is not None or candidate in _builtin_template_ids():
            candidate = f"{base}{counter}"
            counter += 1
        return candidate

    def _write_user_payload(
        self, payload: dict[str, Any], *, expected_id: str | None = None
    ) -> None:
        """Validate a candidate before replacing disk and confirming its cache."""
        version_raw = payload.get("version", 1)
        try:
            version = max(1, int(version_raw))
        except Exception:
            version = 1
        encoded = (
            json.dumps(
                {"version": version, "templates": payload.get("templates", [])},
                ensure_ascii=False,
                indent=2,
            )
            + "\n"
        )
        expected = json.loads(encoded)
        verified: dict[str, PageTemplate] | None = None

        def encode(temporary: Path) -> None:
            nonlocal verified
            with temporary.open("w", encoding="utf-8") as handle:
                if handle.write(encoded) != len(encoded):
                    raise OSError("Page template write did not complete")
                handle.flush()
                os.fsync(handle.fileno())
            payload_readback = json.loads(temporary.read_text(encoding="utf-8"))
            if not _same_wire_value(payload_readback, expected):
                raise OSError("Page template readback did not match the candidate")
            verified = self._templates_from_payload(payload_readback)
            if expected_id is not None and expected_id not in verified:
                raise RuntimeError(f"failed to reload saved template: {expected_id}")

        write_atomically(self._template_file, encode)
        if verified is None:
            raise RuntimeError("Page template candidate was not verified")
        self._templates = verified

    def _load_user_payload_for_write(self) -> dict[str, Any]:
        path = self._template_file
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {"version": 1, "templates": []}
        payload = json.loads(text)
        if not isinstance(payload, Mapping):
            raise ValueError("Template payload must contain a JSON object")
        if "templates" in payload and not isinstance(payload["templates"], list):
            raise ValueError("Template payload templates must be a list")
        return dict(payload)

    def _templates_from_payload(self, payload: Mapping[str, Any]) -> dict[str, PageTemplate]:
        templates: dict[str, PageTemplate] = {tpl.id: tpl for tpl in _builtin_templates()}
        if not isinstance(payload, Mapping):
            logger.warning("Template payload must contain a JSON object")
            return templates

        raw_templates = payload.get("templates")
        if not isinstance(raw_templates, list):
            return templates

        for idx, entry in enumerate(raw_templates):
            if not isinstance(entry, Mapping):
                logger.warning("Skipping non-object template entry at index %s", idx)
                continue
            template = _template_from_payload(entry, source="user", index=idx)
            if template is None:
                continue
            if template.id in templates:
                logger.warning(
                    "Ignoring user template id '%s' because it conflicts with existing template",
                    template.id,
                )
                continue
            templates[template.id] = template
        return templates


def _same_wire_value(left: Any, right: Any) -> bool:
    """Compare decoded JSON while retaining legacy nonfinite number support."""
    if isinstance(left, dict) and isinstance(right, dict):
        return left.keys() == right.keys() and all(
            _same_wire_value(left[key], right[key]) for key in left
        )
    if isinstance(left, list) and isinstance(right, list):
        return len(left) == len(right) and all(
            _same_wire_value(a, b) for a, b in zip(left, right, strict=True)
        )
    if (
        isinstance(left, float)
        and isinstance(right, float)
        and math.isnan(left)
        and math.isnan(right)
    ):
        return True
    if isinstance(left, bool) != isinstance(right, bool):
        return False
    return bool(left == right)


def _builtin_templates() -> tuple[PageTemplate, ...]:
    # Lazy: comic_document_profile imports LayerTemplate from this module.
    from .comic_document_profile import builtin_comic_page_templates  # noqa: PLC0415

    blanks: tuple[PageTemplate, ...] = (
        PageTemplate(
            id="builtin.blank_like_current",
            name="Blank (Like Current Page)",
            category="basic",
            source="builtin",
            canvas_mode="inherit_active",
            description="Use current page canvas and keep layer structure with blank pixels.",
        ),
        PageTemplate(
            id="builtin.blank_default_layers",
            name="Blank (Default Layers)",
            category="basic",
            source="builtin",
            canvas_mode="document_default",
            layers=(
                LayerTemplate(name="Background", fill="white"),
                LayerTemplate(name="Layer 1", fill="transparent"),
            ),
            description="Create a page with default Background/Layer 1 layers.",
        ),
    )
    return blanks + tuple(builtin_comic_page_templates())


def _builtin_template_ids() -> set[str]:
    return {template.id for template in _builtin_templates()}


def _normalize_user_template(template: PageTemplate) -> PageTemplate:
    template_id = str(template.id).strip()
    if not _TEMPLATE_ID_PATTERN.fullmatch(template_id):
        raise ValueError(f"invalid template id: {template_id!r}")

    name = str(template.name).strip() or template_id
    category = str(template.category).strip() or "custom"
    description = template.description.strip() if isinstance(template.description, str) else None

    canvas_mode_raw = str(template.canvas_mode).strip()
    canvas_mode = (
        canvas_mode_raw if canvas_mode_raw in _ALLOWED_CANVAS_MODES else "document_default"
    )
    if canvas_mode == "fixed" and template.canvas is None:
        raise ValueError("canvas_mode=fixed requires canvas")

    layers = tuple(
        LayerTemplate(
            name=(layer.name or "").strip() or f"Layer {idx + 1}",
            visible=bool(layer.visible),
            opacity=_clamp_float(layer.opacity, default=1.0, min_value=0.0, max_value=1.0),
            blend_mode=str(layer.blend_mode).strip() or "normal",
            locked=bool(layer.locked),
            layer_type=(
                str(layer.layer_type).strip().lower()
                if str(layer.layer_type).strip().lower() in _ALLOWED_LAYER_TYPES
                else "raster"
            ),
            fill=(
                str(layer.fill).strip().lower()
                if str(layer.fill).strip().lower() in _ALLOWED_FILL_MODES
                else "transparent"
            ),
            vector_objects=tuple(layer.vector_objects or ()),
            metadata=dict(layer.metadata) if isinstance(layer.metadata, Mapping) else None,
            raster_png=bytes(layer.raster_png) if layer.raster_png else None,
        )
        for idx, layer in enumerate(template.layers)
    )
    metadata = dict(template.metadata) if isinstance(template.metadata, Mapping) else None

    return PageTemplate(
        id=template_id,
        name=name,
        category=category,
        source="user",
        canvas_mode=canvas_mode,
        canvas=template.canvas,
        layers=layers,
        description=description,
        metadata=metadata,
    )


def _template_to_payload(template: PageTemplate) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": template.id,
        "name": template.name,
        "category": template.category,
        "canvas_mode": template.canvas_mode,
    }
    if isinstance(template.description, str) and template.description.strip():
        payload["description"] = template.description.strip()
    if template.canvas is not None:
        payload["canvas"] = {
            "width": int(template.canvas.width),
            "height": int(template.canvas.height),
            "dpi": [float(template.canvas.dpi[0]), float(template.canvas.dpi[1])],
            "background_color": [
                int(template.canvas.background_color[0]),
                int(template.canvas.background_color[1]),
                int(template.canvas.background_color[2]),
                int(template.canvas.background_color[3]),
            ],
        }
    if template.layers:
        payload["layers"] = [_layer_template_to_payload(layer) for layer in template.layers]
    if isinstance(template.metadata, Mapping):
        payload["metadata"] = dict(template.metadata)
    return payload


def _layer_template_to_payload(layer: LayerTemplate) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": layer.name,
        "visible": bool(layer.visible),
        "opacity": float(layer.opacity),
        "blend_mode": str(layer.blend_mode),
        "locked": bool(layer.locked),
        "layer_type": str(layer.layer_type),
        "fill": str(layer.fill),
        "vector_objects": list(layer.vector_objects),
    }
    if isinstance(layer.metadata, Mapping):
        payload["metadata"] = dict(layer.metadata)
    if layer.raster_png:
        payload["raster_png"] = TemplateRasterCodec.to_base64(layer.raster_png)
    return payload


def _template_from_payload(
    payload: Mapping[str, Any],
    *,
    source: str,
    index: int,
) -> PageTemplate | None:
    template_id = str(payload.get("id", "")).strip()
    if not _TEMPLATE_ID_PATTERN.fullmatch(template_id):
        logger.warning("Skipping template[%s]: invalid id '%s'", index, template_id)
        return None

    name = str(payload.get("name", "")).strip() or template_id
    category = str(payload.get("category", "")).strip() or "custom"
    description_raw = payload.get("description")
    description = description_raw.strip() if isinstance(description_raw, str) else None

    canvas_mode_raw = str(payload.get("canvas_mode", "document_default")).strip()
    canvas_mode = (
        canvas_mode_raw if canvas_mode_raw in _ALLOWED_CANVAS_MODES else "document_default"
    )

    canvas = _parse_canvas(payload.get("canvas"))
    if canvas_mode == "fixed" and canvas is None:
        logger.warning("Skipping template[%s]: canvas_mode=fixed requires canvas payload", index)
        return None

    layers = _parse_layers(payload.get("layers"))
    metadata = payload.get("metadata")
    metadata_map = dict(metadata) if isinstance(metadata, Mapping) else None

    return PageTemplate(
        id=template_id,
        name=name,
        category=category,
        source=source,
        canvas_mode=canvas_mode,
        canvas=canvas,
        layers=layers,
        description=description,
        metadata=metadata_map,
    )


def _parse_layers(raw_layers: Any) -> tuple[LayerTemplate, ...]:
    if not isinstance(raw_layers, list):
        return ()
    parsed: list[LayerTemplate] = []
    for idx, raw in enumerate(raw_layers):
        if not isinstance(raw, Mapping):
            logger.warning("Skipping non-object layer template at index %s", idx)
            continue
        name = str(raw.get("name", "")).strip() or f"Layer {idx + 1}"
        visible = bool(raw.get("visible", True))
        opacity = _clamp_float(raw.get("opacity"), default=1.0, min_value=0.0, max_value=1.0)
        blend_mode = str(raw.get("blend_mode", "normal")).strip() or "normal"
        locked = bool(raw.get("locked", False))
        layer_type_raw = str(raw.get("layer_type", "raster")).strip().lower()
        layer_type = layer_type_raw if layer_type_raw in _ALLOWED_LAYER_TYPES else "raster"
        fill_raw = str(raw.get("fill", "transparent")).strip().lower()
        fill = fill_raw if fill_raw in _ALLOWED_FILL_MODES else "transparent"
        vector_objects = tuple(raw.get("vector_objects", ()) or ())
        metadata = raw.get("metadata")
        metadata_map = dict(metadata) if isinstance(metadata, Mapping) else None
        raster_raw = raw.get("raster_png")
        raster_png = (
            TemplateRasterCodec.from_base64(raster_raw) if isinstance(raster_raw, str) else None
        )
        parsed.append(
            LayerTemplate(
                name=name,
                visible=visible,
                opacity=opacity,
                blend_mode=blend_mode,
                locked=locked,
                layer_type=layer_type,
                fill=fill,
                vector_objects=vector_objects,
                metadata=metadata_map,
                raster_png=raster_png or None,
            )
        )
    return tuple(parsed)


def _parse_canvas(raw_canvas: Any) -> HostCanvasSpec | None:
    if not isinstance(raw_canvas, Mapping):
        return None

    width = _positive_int(raw_canvas.get("width"))
    height = _positive_int(raw_canvas.get("height"))
    if width is None or height is None:
        return None

    dpi = raw_canvas.get("dpi", (72.0, 72.0))
    dpi_pair = _tuple2_float(dpi, fallback=(72.0, 72.0))
    background = raw_canvas.get("background_color", (255, 255, 255, 255))
    background_rgba = _tuple4_int(background, fallback=(255, 255, 255, 255))
    return HostCanvasSpec(
        width=width,
        height=height,
        dpi=dpi_pair,
        background_color=background_rgba,
    )


def _positive_int(value: Any) -> int | None:
    try:
        number = int(value)
    except Exception:
        return None
    if number <= 0:
        return None
    return number


def _tuple2_float(value: Any, *, fallback: tuple[float, float]) -> tuple[float, float]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        raw = list(value)
        parsed: list[float] = []
        for item in raw[:2]:
            try:
                parsed.append(float(item))
            except Exception:
                continue
        if len(parsed) == 1:
            parsed.append(parsed[0])
        if len(parsed) >= 2:
            return (parsed[0], parsed[1])
    return fallback


def _tuple4_int(value: Any, *, fallback: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        raw = list(value)
        numbers: list[int] = []
        for item in raw[:4]:
            try:
                numbers.append(int(item))
            except Exception:
                numbers.append(255)
        while len(numbers) < 4:
            numbers.append(255)
        return (
            max(0, min(255, numbers[0])),
            max(0, min(255, numbers[1])),
            max(0, min(255, numbers[2])),
            max(0, min(255, numbers[3])),
        )
    return fallback


def _clamp_float(value: Any, *, default: float, min_value: float, max_value: float) -> float:
    try:
        number = float(value)
    except Exception:
        number = default
    return max(min_value, min(max_value, number))


__all__ = [
    "LayerTemplate",
    "PageTemplate",
    "PageTemplateStore",
]
