"""Finite-support style damage and source halos in layer-local texture pixels."""

from typing import Any

from PySide6.QtCore import QRect

from .types import WgpuRenderTargetHandle


def style_work_rect(
    source: Any, padding: int, dirty: QRect | None = None
) -> tuple[QRect, QRect | None]:
    bounds = QRect(-padding, -padding, source.width + 2 * padding, source.height + 2 * padding)
    if dirty is None:
        return bounds, None
    damage = dirty.adjusted(-padding, -padding, padding, padding).intersected(bounds)
    if damage.isEmpty():
        return bounds, None
    return damage.adjusted(-padding, -padding, padding, padding), damage


def copy_style_patch(
    encoder: Any,
    old: WgpuRenderTargetHandle,
    target: WgpuRenderTargetHandle,
    patch: WgpuRenderTargetHandle,
    work: QRect,
    damage: QRect,
    padding: int,
) -> None:
    encoder.copy_texture_to_texture(
        {"texture": old.texture}, {"texture": target.texture}, (old.width, old.height, 1)
    )
    encoder.copy_texture_to_texture(
        {"texture": patch.texture, "origin": (damage.x() - work.x(), damage.y() - work.y(), 0)},
        {"texture": target.texture, "origin": (damage.x() + padding, damage.y() + padding, 0)},
        (damage.width(), damage.height(), 1),
    )
