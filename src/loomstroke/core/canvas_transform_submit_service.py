"""Raster transform command submit helpers for ``SimpleCanvasService``."""

from __future__ import annotations

from types import MethodType
from typing import Any


def _target_layer_for_command(canvas: Any, command: Any) -> Any | None:
    layer_id = getattr(command, "layer_id", None)
    if layer_id:
        get_layer = getattr(canvas, "_get_layer_by_id", None)
        if callable(get_layer):
            return get_layer(layer_id)
        get_layer = getattr(canvas, "get_layer_by_id", None)
        if callable(get_layer):
            return get_layer(layer_id)
        return None

    get_active = getattr(canvas, "_get_active_layer", None)
    if callable(get_active):
        return get_active()
    return getattr(canvas, "active_layer", None)


def _apply_qimage_transform(command: Any, canvas: Any) -> bool:
    layer = _target_layer_for_command(canvas, command)
    if layer is None or layer.image is None:
        return False

    transform_image = getattr(canvas, "_transform_image", None)
    if not callable(transform_image):
        return False
    transformed = transform_image().transformed_image(
        layer.image,
        getattr(command, "transform_type", ""),
        getattr(command, "params", {}),
    )
    if transformed is None:
        return False

    layer.image = transformed
    layer.texture_version += 1
    command._executed = True  # noqa: SLF001
    return True


def _revert_qimage_transform(command: Any, canvas: Any) -> bool:
    backup = getattr(command, "_backup_image", None)
    if backup is None:
        return False

    layer = _target_layer_for_command(canvas, command)
    if layer is None:
        return False

    layer.image = backup.copy()
    layer.texture_version += 1
    command._executed = False  # noqa: SLF001
    return True


class CanvasTransformSubmitService:
    """Submit raster transform commands and preserve canvas post-processing order."""

    def __init__(self, owner: Any) -> None:
        self._owner = owner

    def submit(self, cmd: Any) -> bool:
        layer_id = getattr(cmd, "layer_id", None)
        if layer_id:
            layer = self._owner._get_layer_by_id(layer_id)
        else:
            layer = self._owner._get_active_layer()
        if layer is None or layer.image is None:
            return False

        backup = layer.image.copy()
        ttype = getattr(cmd, "transform_type", "")
        params = getattr(cmd, "params", {})

        transformed = self._owner._transform_image().transformed_image(layer.image, ttype, params)
        if transformed is None:
            return False
        layer.image = transformed

        layer.texture_version += 1
        self._owner._invalidate_layer_cache()

        cmd._backup_image = backup  # noqa: SLF001
        cmd._executed = True  # noqa: SLF001
        cmd.apply = MethodType(_apply_qimage_transform, cmd)
        cmd.revert = MethodType(_revert_qimage_transform, cmd)

        self._owner._record_history_command("Transform", cmd)
        self._owner._recompose()
        return True
