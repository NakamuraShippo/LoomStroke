"""Ordered layer styles of one layer; render order is fixed regardless of list order."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any, ClassVar

from .appearance_registry import AppearanceRegistry
from .layer_style_spec import LayerStyleSpec

__all__ = ["LayerStyleStack"]


@dataclass(frozen=True, slots=True)
class LayerStyleStack:
    styles: tuple[LayerStyleSpec, ...] = ()
    schema_version: int = 1

    SCHEMA_VERSION: ClassVar[int] = 1
    MAX_STYLES: ClassVar[int] = 32

    @property
    def is_empty(self) -> bool:
        return not self.styles

    def get(self, style_id: str) -> LayerStyleSpec | None:
        return next((s for s in self.styles if s.style_id == style_id), None)

    def enabled(self) -> tuple[LayerStyleSpec, ...]:
        return tuple(s for s in self.styles if s.enabled)

    def render_order(self) -> tuple[LayerStyleSpec, ...]:
        order = AppearanceRegistry.STYLE_RENDER_ORDER
        return tuple(
            sorted(
                self.enabled(),
                key=lambda s: order.index(s.kind) if s.kind in order else len(order),
            )
        )

    def with_style(self, style: LayerStyleSpec) -> LayerStyleStack:
        styles = list(self.styles)
        for index, existing in enumerate(styles):
            if existing.style_id == style.style_id:
                styles[index] = style
                break
        else:
            styles.append(style)
        return replace(self, styles=tuple(styles[: self.MAX_STYLES]))

    def without(self, style_id: str) -> LayerStyleStack:
        return replace(self, styles=tuple(s for s in self.styles if s.style_id != style_id))

    def moved(self, style_id: str, delta: int) -> LayerStyleStack:
        styles = list(self.styles)
        index = next((i for i, s in enumerate(styles) if s.style_id == style_id), None)
        if index is None:
            return self
        target = max(0, min(len(styles) - 1, index + delta))
        style = styles.pop(index)
        styles.insert(target, style)
        return replace(self, styles=tuple(styles))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "styles": [style.to_dict() for style in self.styles],
        }

    @classmethod
    def from_dict(cls, data: Any) -> LayerStyleStack:
        if not isinstance(data, Mapping):
            return cls()
        raw = data.get("styles", [])
        styles = cls._parse(raw if isinstance(raw, Iterable) else [])
        version = data.get("schema_version", 1)
        return cls(styles, version if isinstance(version, int) and version >= 1 else 1)

    @classmethod
    def _parse(cls, raw: Iterable[Any]) -> tuple[LayerStyleSpec, ...]:
        styles: list[LayerStyleSpec] = []
        for entry in raw:
            style = LayerStyleSpec.from_dict(entry)
            if style is not None:
                styles.append(style)
        return tuple(styles[: cls.MAX_STYLES])
