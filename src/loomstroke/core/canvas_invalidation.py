"""Renderer-neutral invalidation models for canvas repainting."""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QRect


def _copy_rect(rect: QRect | None) -> QRect | None:
    return QRect(rect) if rect is not None else None


@dataclass(frozen=True, slots=True)
class GpuResourceInvalidation:
    """Backend resource invalidation separate from dirty image pixels."""

    layer_ids: frozenset[str] = field(default_factory=frozenset)
    all_resources: bool = False
    reason: str | None = None

    @classmethod
    def empty(cls) -> GpuResourceInvalidation:
        return cls()

    @classmethod
    def for_all(cls, *, reason: str | None = None) -> GpuResourceInvalidation:
        return cls(all_resources=True, reason=reason)

    @property
    def is_empty(self) -> bool:
        return not self.all_resources and not self.layer_ids

    def merge(self, other: GpuResourceInvalidation) -> GpuResourceInvalidation:
        if self.is_empty:
            return other
        if other.is_empty:
            return self
        reason = other.reason or self.reason
        return GpuResourceInvalidation(
            layer_ids=self.layer_ids | other.layer_ids,
            all_resources=self.all_resources or other.all_resources,
            reason=reason,
        )


@dataclass(frozen=True, slots=True)
class RenderInvalidation:
    """One-shot repaint invalidation consumed by renderers."""

    dirty_rect: QRect | None = None
    full_recompose: bool = False
    mask_dirty_layer_ids: frozenset[str] = field(default_factory=frozenset)
    gpu_resources: GpuResourceInvalidation = field(default_factory=GpuResourceInvalidation.empty)

    @classmethod
    def empty(cls) -> RenderInvalidation:
        return cls()

    @property
    def has_dirty_rect(self) -> bool:
        return self.dirty_rect is not None and not self.dirty_rect.isEmpty()

    @property
    def has_any(self) -> bool:
        return (
            self.full_recompose
            or self.has_dirty_rect
            or bool(self.mask_dirty_layer_ids)
            or not self.gpu_resources.is_empty
        )

    def dirty_rect_copy(self) -> QRect | None:
        return _copy_rect(self.dirty_rect)

    def with_dirty_rect(self, rect: QRect | None) -> RenderInvalidation:
        return RenderInvalidation(
            dirty_rect=_copy_rect(rect),
            full_recompose=self.full_recompose,
            mask_dirty_layer_ids=self.mask_dirty_layer_ids,
            gpu_resources=self.gpu_resources,
        )
