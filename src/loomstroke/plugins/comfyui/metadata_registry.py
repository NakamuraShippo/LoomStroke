"""Metadata registry for binding canvas/layer metadata to ComfyUI parameters.

Provides a registry of named metadata entries (e.g. layer bounds, canvas size)
that can be resolved at workflow submission time and injected into node inputs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from loomstroke.host_api import HostAPI

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResolveContext:
    """Context passed to resolvers when computing metadata values."""

    layer_id: str | None = None


@dataclass(frozen=True)
class MetadataEntry:
    """Definition of a single metadata value available for binding."""

    key: str  # e.g. "layer.bounds.x"
    label: str  # e.g. "Bounds X"
    category: str  # e.g. "Layer", "Canvas"
    value_type: str  # "int" or "float"
    needs_layer: bool  # True if resolver requires a layer_id


@dataclass(frozen=True)
class MetadataBinding:
    """Binding of a metadata entry to a specific node parameter."""

    entry_key: str
    layer_id: str | None = None


_Resolver = Callable[["HostAPI", ResolveContext], int | float | None]


class MetadataRegistry:
    """Registry of metadata entries and their resolvers."""

    def __init__(self) -> None:
        self._entries: dict[str, MetadataEntry] = {}
        self._resolvers: dict[str, _Resolver] = {}

    def register(self, entry: MetadataEntry, resolver: _Resolver) -> None:
        self._entries[entry.key] = entry
        self._resolvers[entry.key] = resolver

    def categories(self) -> list[str]:
        seen: dict[str, None] = {}
        for entry in self._entries.values():
            seen.setdefault(entry.category, None)
        return list(seen)

    def entries_for(self, category: str, value_type: str | None = None) -> list[MetadataEntry]:
        result: list[MetadataEntry] = []
        for entry in self._entries.values():
            if entry.category != category:
                continue
            if value_type and entry.value_type != value_type:
                continue
            result.append(entry)
        return result

    def get_entry(self, key: str) -> MetadataEntry | None:
        return self._entries.get(key)

    def resolve(
        self,
        key: str,
        host_api: HostAPI,
        ctx: ResolveContext | None = None,
    ) -> int | float | None:
        resolver = self._resolvers.get(key)
        if resolver is None:
            logger.warning("No resolver for metadata key: %s", key)
            return None
        try:
            return resolver(host_api, ctx or ResolveContext())
        except Exception:
            logger.error("Failed to resolve metadata '%s'", key, exc_info=True)
            return None


# ------------------------------------------------------------------
# Built-in resolvers
# ------------------------------------------------------------------


def _resolve_layer_bounds_component(
    host_api: HostAPI, ctx: ResolveContext, index: int
) -> int | None:
    bounds = host_api.canvas.get_layer_content_bounds(ctx.layer_id)
    if bounds is None:
        return None
    return bounds[index]


def _resolve_canvas_width(host_api: HostAPI, _ctx: ResolveContext) -> int | None:
    w, _h = host_api.canvas.get_canvas_size()
    return int(w) if w else None


def _resolve_canvas_height(host_api: HostAPI, _ctx: ResolveContext) -> int | None:
    _w, h = host_api.canvas.get_canvas_size()
    return int(h) if h else None


# Selection bounds ---------------------------------------------------


def _resolve_selection_bounds_component(
    host_api: HostAPI, _ctx: ResolveContext, index: int
) -> int | None:
    sel = host_api.canvas.get_selection()
    if sel is None or sel.bounds is None:
        return None
    return sel.bounds[index]


# Foreground color ---------------------------------------------------


def _resolve_fg_color_component(host_api: HostAPI, _ctx: ResolveContext, index: int) -> int | None:
    color = host_api.get_active_color()
    if color is None:
        return None
    # RGBA is 0.0-1.0 float — convert to 0-255 int
    return round(color.rgba[index] * 255)


# Visible area (viewport) -------------------------------------------


def _resolve_visible_area_component(
    host_api: HostAPI, _ctx: ResolveContext, index: int
) -> int | None:
    main_window = host_api.ui.get_main_window()
    if main_window is None:
        return None
    canvas_widget = getattr(main_window, "canvas_widget", None)
    if canvas_widget is None:
        return None
    viewport = getattr(canvas_widget, "viewport", None)
    if viewport is None or not hasattr(viewport, "get_visible_rect"):
        return None
    rect = viewport.get_visible_rect()
    if rect.isEmpty():
        return None
    values = (int(rect.x()), int(rect.y()), int(rect.width()), int(rect.height()))
    return values[index]


# ------------------------------------------------------------------
# Singleton & registration
# ------------------------------------------------------------------

_global_registry: MetadataRegistry | None = None


def get_metadata_registry() -> MetadataRegistry:
    """Return the global MetadataRegistry, creating it on first call."""
    global _global_registry  # noqa: PLW0603
    if _global_registry is None:
        _global_registry = MetadataRegistry()
        _register_builtins(_global_registry)
    return _global_registry


def _register_builtins(registry: MetadataRegistry) -> None:
    # Layer bounds
    for idx, (suffix, label) in enumerate(
        [("x", "Bounds X"), ("y", "Bounds Y"), ("w", "Bounds W"), ("h", "Bounds H")]
    ):
        registry.register(
            MetadataEntry(
                key=f"layer.bounds.{suffix}",
                label=label,
                category="Layer",
                value_type="int",
                needs_layer=True,
            ),
            resolver=lambda api, ctx, _i=idx: _resolve_layer_bounds_component(api, ctx, _i),
        )

    # Canvas dimensions
    registry.register(
        MetadataEntry(
            key="canvas.width",
            label="Width",
            category="Canvas",
            value_type="int",
            needs_layer=False,
        ),
        resolver=_resolve_canvas_width,
    )
    registry.register(
        MetadataEntry(
            key="canvas.height",
            label="Height",
            category="Canvas",
            value_type="int",
            needs_layer=False,
        ),
        resolver=_resolve_canvas_height,
    )

    # Selection bounds
    for idx, (suffix, label) in enumerate(
        [("x", "Bounds X"), ("y", "Bounds Y"), ("w", "Bounds W"), ("h", "Bounds H")]
    ):
        registry.register(
            MetadataEntry(
                key=f"selection.bounds.{suffix}",
                label=label,
                category="Selection",
                value_type="int",
                needs_layer=False,
            ),
            resolver=lambda api, ctx, _i=idx: _resolve_selection_bounds_component(api, ctx, _i),
        )

    # Foreground color (0-255 int)
    for idx, (suffix, label) in enumerate([("r", "Red"), ("g", "Green"), ("b", "Blue")]):
        registry.register(
            MetadataEntry(
                key=f"color.fg.{suffix}",
                label=label,
                category="Color",
                value_type="int",
                needs_layer=False,
            ),
            resolver=lambda api, ctx, _i=idx: _resolve_fg_color_component(api, ctx, _i),
        )

    # Visible area (viewport)
    for idx, (suffix, label) in enumerate(
        [("x", "X"), ("y", "Y"), ("w", "Width"), ("h", "Height")]
    ):
        registry.register(
            MetadataEntry(
                key=f"viewport.visible.{suffix}",
                label=label,
                category="Viewport",
                value_type="int",
                needs_layer=False,
            ),
            resolver=lambda api, ctx, _i=idx: _resolve_visible_area_component(api, ctx, _i),
        )
