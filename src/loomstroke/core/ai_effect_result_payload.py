"""Owned PNG and placement values for one existing RGB AI result layer."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from inspect import getattr_static
from typing import Any

from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage

from .canvas_layer_image_service import AIEffectLayerImageService
from .canvas_layer_model import _Layer
from .comic_panels.ai_image_context import place_panel_output
from .comic_panels.image_placement import PLACEMENT_KEY
from .command_types import LayerTransform
from .layer_command_commit_context import _LayerCommandContext
from .page_history_memory import estimate_metadata_payload_bytes

_RESULT_IMAGE_FORMATS = (
    QImage.Format.Format_ARGB32_Premultiplied,
    QImage.Format.Format_ARGB32,
    QImage.Format.Format_RGBA8888,
)


def _standard_result_canvas(canvas: Any) -> bool:
    from .canvas_service_simple_filter_api import _DEFAULT_AI_RESULT_HOOKS  # noqa: PLC0415

    return all(
        getattr_static(canvas, name, None) is original
        for name, original in _DEFAULT_AI_RESULT_HOOKS.items()
    )


def _namespace_signature(value: Any, seen: set[int]) -> Any:
    kind = type(value)
    if any(kind is expected for expected in (str, int, bool, type(None))):
        return kind, value
    if kind is float:
        return kind, value.hex()
    if not any(kind is expected for expected in (dict, list, tuple)) or id(value) in seen:
        raise ValueError("Unsupported AI result placement metadata")
    seen.add(id(value))
    try:
        if kind is dict:
            return kind, tuple(
                (_namespace_signature(key, seen), _namespace_signature(item, seen))
                for key, item in value.items()
            )
        return kind, tuple(_namespace_signature(item, seen) for item in value)
    finally:
        seen.remove(id(value))


def _placement(metadata: Any) -> tuple[bool, Any]:
    if metadata is not None and type(metadata) is not dict:
        raise ValueError("AI result requires standard layer metadata")
    present = metadata is not None and PLACEMENT_KEY in metadata
    value = metadata[PLACEMENT_KEY] if metadata is not None and present else None
    _namespace_signature(value, set())
    return present, deepcopy(value)


def _encode_png(image: QImage | None) -> bytes | None:
    if image is None:
        return None
    if not isinstance(image, QImage) or image.isNull():
        raise ValueError("AI result has no encodable previous image")
    buffer = QBuffer()
    if not buffer.open(QIODevice.OpenModeFlag.WriteOnly):
        raise ValueError("AI result previous image encoding failed")
    try:
        # The installed PySide binding accepts str despite the bytes-only stub.
        if not image.save(buffer, "PNG"):  # type: ignore[call-overload]
            raise ValueError("AI result previous image encoding failed")
        return bytes(buffer.data().data())
    finally:
        buffer.close()


@dataclass(frozen=True)
class _ResultImage:
    png: bytes | None
    offset: tuple[int, int]
    transform: LayerTransform
    placement_present: bool
    placement: Any
    image_format: QImage.Format | None

    @classmethod
    def capture(cls, layer: _Layer, png: bytes | None) -> _ResultImage:
        present, value = _placement(layer.metadata)
        if type(layer.transform) is not LayerTransform:
            raise ValueError("AI result requires a standard layer transform")
        image_format = layer.image.format() if layer.image is not None else None
        return cls(
            png, (layer.offset_x, layer.offset_y), layer.transform, present, value, image_format
        )

    @property
    def retained_bytes(self) -> int:
        return len(self.png or b"") + estimate_metadata_payload_bytes(self.placement, seen=set())


class _LiveResult:
    """A synchronous attempt's identity proof, never retained by history."""

    def __init__(self, canvas: Any, layer: _Layer, scope: _LayerCommandContext) -> None:
        self.canvas, self.layer, self.scope = canvas, layer, scope
        self.image, self.state, self.metadata = layer.image, layer.ai_effect, layer.metadata
        if self.image is not None and (
            not isinstance(self.image, QImage) or self.image.format() not in _RESULT_IMAGE_FORMATS
        ):
            raise ValueError("AI result history requires a supported RGB8 image format")
        self.offset, self.transform = (layer.offset_x, layer.offset_y), layer.transform
        self.versions = layer.texture_version, layer.transform_version
        self.parent_id = layer.parent_id
        self.locks = layer.locked, layer.alpha_lock
        self.placement = _placement(layer.metadata)
        self.signature = _namespace_signature(self.placement[1], set())

    def current(self) -> bool:
        layer = self.layer
        present, value = _placement(layer.metadata)
        return (
            self.scope.current()
            and _standard_result_canvas(self.canvas)
            and all(item.native_cmyk is None for item in self.canvas._flatten_layers())
            and layer.ai_effect is self.state
            and layer.image is self.image
            and layer.metadata is self.metadata
            and layer.transform is self.transform
            and (layer.offset_x, layer.offset_y) == self.offset
            and (layer.texture_version, layer.transform_version) == self.versions
            and layer.parent_id == self.parent_id
            and (layer.locked, layer.alpha_lock) == self.locks
            and layer.native_cmyk is None
            and layer.layer_type == "ai_effect"
            and present is self.placement[0]
            and _namespace_signature(value, set()) == self.signature
        )


class _PreparedResult:
    """Prepare outside live content, then retain only this call's write receipt."""

    def __init__(self, before: _LiveResult, desired: _ResultImage, image: QImage | None) -> None:
        self.before, self.desired, self.image = before, desired, image
        self.installed: _LiveResult | None = None

    def install(self) -> bool:
        if not self.before.current():
            return False
        layer, desired = self.before.layer, self.desired
        metadata = dict(layer.metadata or {})
        if desired.placement_present:
            metadata[PLACEMENT_KEY] = deepcopy(desired.placement)
        else:
            metadata.pop(PLACEMENT_KEY, None)
        layer.image = self.image
        layer.offset_x, layer.offset_y = desired.offset
        layer.transform = desired.transform
        layer.metadata = metadata if metadata or layer.metadata is not None else None
        layer.texture_version += 1
        layer.transform_version += 1
        self.installed = _LiveResult(self.before.canvas, layer, self.before.scope)
        self.before.canvas._invalidate_layer_cache()
        return self.installed.current()

    def rollback(self) -> bool:
        if self.installed is None:
            return self.before.current()
        if not self.installed.current():
            return False
        layer, before = self.before.layer, self.before
        layer.image, layer.metadata = before.image, before.metadata
        layer.offset_x, layer.offset_y = before.offset
        layer.transform = before.transform
        layer.texture_version += 1
        layer.transform_version += 1
        return True


def _prepare_result(
    canvas: Any, layer: _Layer, scope: _LayerCommandContext, png: bytes
) -> tuple[tuple[_ResultImage, _ResultImage], _PreparedResult]:
    before = _LiveResult(canvas, layer, scope)
    image = AIEffectLayerImageService._decode_ai_effect_layer_png(layer.id, png)
    if image is None or not before.current():
        raise ValueError("AI result target changed or PNG is invalid")
    previous_png = _encode_png(before.image)
    old = _ResultImage.capture(layer, previous_png)
    detached = replace(layer, image=image)
    if not place_panel_output(canvas, detached, before.image):
        detached.offset_x = detached.offset_y = 0
    after = _ResultImage.capture(detached, bytes(png))
    if not before.current():
        raise ValueError("AI result target changed during preparation")
    return (old, after), _PreparedResult(before, after, image)


def _prepare_result_restore(
    canvas: Any,
    layer: _Layer,
    scope: _LayerCommandContext,
    desired: _ResultImage,
    *,
    original_format: bool,
) -> _PreparedResult:
    before = _LiveResult(canvas, layer, scope)
    image = None
    if desired.png is not None:
        if original_format:
            decoded = QImage()
            image_format = desired.image_format
            if (
                image_format is None
                or image_format not in _RESULT_IMAGE_FORMATS
                or not decoded.loadFromData(desired.png)
                or decoded.isNull()
            ):
                raise ValueError("AI result previous PNG restore failed")
            image = decoded.convertToFormat(image_format)
        else:
            image = AIEffectLayerImageService._decode_ai_effect_layer_png(layer.id, desired.png)
    if (desired.png is not None and image is None) or not before.current():
        raise ValueError("AI result restore target changed or PNG is invalid")
    return _PreparedResult(before, desired, image)
