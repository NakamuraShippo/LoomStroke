"""Only the mask payload retained by a qualified mask history Command."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from PySide6.QtGui import QColor, QImage

from .commands_layer_lookup import find_layer
from .events import Events


@dataclass
class _MaskValue:
    image: QImage | None
    enabled: bool

    @classmethod
    def capture(cls, layer: Any) -> _MaskValue:
        image = layer.layer_mask
        return cls(QImage(image) if image is not None else None, bool(layer.mask_enabled))

    def install(self, layer: Any) -> None:
        layer.layer_mask = QImage(self.image) if self.image is not None else None
        layer.mask_enabled = self.enabled

    def matches(self, layer: Any) -> bool:
        image = layer.layer_mask
        return bool(layer.mask_enabled) == self.enabled and (
            image is None
            if self.image is None
            else isinstance(image, QImage) and image.cacheKey() == self.image.cacheKey()
        )


class _MaskCommandState:
    """COW masks, not live layer/document/native pixel references."""

    def __init__(self, kind: str, before: _MaskValue, after: _MaskValue) -> None:
        self.kind, self.before, self.after = kind, before, after
        self.applied_once = False

    def restore(self, canvas: Any, command: Any, *, use_after: bool) -> bool:
        layer = find_layer(canvas, command._layer_id)
        if layer is None or (not use_after and not command._executed):
            return False
        # Page hydration and patch history can recreate a live QImage. Take
        # back its current payload instead of replaying a stale prepared copy.
        if use_after:
            if self.applied_once:
                self.before = _MaskValue.capture(layer)
            self.after.install(layer)
        else:
            self.after = _MaskValue.capture(layer)
            self.before.install(layer)
        canvas._invalidate_layer_cache()
        command._executed = use_after
        self.applied_once = True
        if not command._mask_submitting:
            self.notify(canvas, layer, use_after=use_after)
            canvas._recompose()
        return True

    def notify(self, canvas: Any, layer: Any, *, use_after: bool) -> None:
        if not use_after:
            change = "mask_removed" if self.before.image is None else "mask_restored"
        else:
            change = {"add": "mask_added", "remove": "mask_removed", "set": "mask_set"}[self.kind]
        canvas._publish_layer_event(Events.LAYER_CHANGED, layer=layer, change_type=change)


def _prepare_mask_state(
    canvas: Any, command: Any, layer: Any, kind: str
) -> _MaskCommandState | None:
    if layer.layer_mask is not None and not isinstance(layer.layer_mask, QImage):
        return None
    before = _MaskValue.capture(layer)
    if kind == "add":
        if layer.layer_mask is not None:
            return None
        image = QImage(canvas._width, canvas._height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(255, 255, 255, 255))
        if image.isNull():
            return None
        after = _MaskValue(image, True)
    elif kind == "remove":
        if layer.layer_mask is None:
            return None
        after = _MaskValue(None, bool(layer.mask_enabled))
    else:
        image = command._mask
        if (
            not isinstance(image, QImage)
            or image.isNull()
            or image.width() != canvas._width
            or image.height() != canvas._height
        ):
            return None
        after = _MaskValue(QImage(image), command._mask_enabled)
    return _MaskCommandState(kind, before, after)


def _retained_mask_bytes(command: Any) -> int:
    images = [getattr(command, name, None) for name in ("_mask", "_old_mask", "_saved_mask")]
    state = command._mask_state
    if isinstance(state, _MaskCommandState):
        images.extend((state.before.image, state.after.image))
    seen: set[int] = set()
    total = 0
    for image in images:
        if isinstance(image, QImage) and image.cacheKey() not in seen:
            seen.add(image.cacheKey())
            total += image.sizeInBytes()
    return total
