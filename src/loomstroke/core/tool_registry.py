"""Registry state for tool plugins and tool metadata."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from typing import Any

from loomstroke.plugins.common.tool_icons import resolve_tool_icon_path
from loomstroke.plugins.sdk.tool import (
    PropertySpec,
    PropertyType,
    ToolActionGroup,
    ToolActionSpec,
    ToolDescriptor,
    ToolMetadata,
    ToolPlugin,
    ToolPreset,
)


@dataclass
class ToolState:
    """State of a registered tool."""

    plugin: ToolPlugin
    descriptor: ToolDescriptor
    is_active: bool = False
    is_enabled: bool = False
    settings: dict[str, Any] = field(default_factory=dict)


_DEFAULT_BRUSH_PROPERTIES: tuple[PropertySpec, ...] = (
    PropertySpec(
        key="size",
        display_name="Size",
        type=PropertyType.FLOAT,
        min_value=1.0,
        max_value=512.0,
        default_value=24.0,
        step=1.0,
        is_common=True,
    ),
    PropertySpec(
        key="opacity",
        display_name="Opacity",
        type=PropertyType.FLOAT,
        min_value=0.0,
        max_value=1.0,
        default_value=1.0,
        step=0.01,
        is_common=True,
    ),
    PropertySpec(
        key="hardness",
        display_name="Hardness",
        type=PropertyType.FLOAT,
        min_value=0.0,
        max_value=1.0,
        default_value=0.5,
        step=0.01,
        is_common=True,
    ),
    PropertySpec(
        key="spacing",
        display_name="Spacing",
        type=PropertyType.FLOAT,
        min_value=0.01,
        max_value=5.0,
        default_value=0.3,
        step=0.01,
        is_common=True,
    ),
    PropertySpec(
        key="pressure_sensitive",
        display_name="Pressure Sensitive",
        type=PropertyType.BOOL,
        default_value=True,
        is_common=True,
    ),
)

_DEFAULT_FILL_PROPERTIES: tuple[PropertySpec, ...] = (
    PropertySpec(
        key="tolerance",
        display_name="Tolerance",
        type=PropertyType.INT,
        min_value=0,
        max_value=255,
        default_value=32,
    ),
    PropertySpec(
        key="opacity",
        display_name="Opacity",
        type=PropertyType.FLOAT,
        min_value=0.0,
        max_value=1.0,
        default_value=1.0,
        step=0.01,
    ),
    PropertySpec(
        key="sample_mode",
        display_name="Sample",
        type=PropertyType.ENUM,
        enum_items=["Current Layer", "All Layers", "Merged"],
        default_value="Current Layer",
    ),
    PropertySpec(
        key="contiguous",
        display_name="Contiguous",
        type=PropertyType.BOOL,
        default_value=True,
    ),
)

_DEFAULT_SELECTION_PROPERTIES: tuple[PropertySpec, ...] = (
    PropertySpec(
        key="selection_mode",
        display_name="Mode",
        type=PropertyType.ENUM,
        enum_items=["Rectangle", "Ellipse", "Lasso", "Magic Wand"],
        default_value="Rectangle",
    ),
    PropertySpec(
        key="operation",
        display_name="Operation",
        type=PropertyType.ENUM,
        enum_items=["Replace", "Add", "Subtract", "Intersect"],
        default_value="Replace",
    ),
    PropertySpec(
        key="feather",
        display_name="Feather",
        type=PropertyType.INT,
        min_value=0,
        max_value=100,
        default_value=0,
    ),
    PropertySpec(
        key="antialias",
        display_name="Anti alias",
        type=PropertyType.BOOL,
        default_value=True,
    ),
)

_DEFAULT_BRUSH_PRESETS: tuple[ToolPreset, ...] = (
    ToolPreset("Sketch", {"size": 8.0, "opacity": 0.6, "hardness": 0.3}),
    ToolPreset("Inking", {"size": 14.0, "opacity": 1.0, "hardness": 0.9}),
)


def _clone_property_specs(specs: Iterable[PropertySpec]) -> list[PropertySpec]:
    return [replace(spec) for spec in specs]


def _clone_presets(presets: Iterable[ToolPreset]) -> list[ToolPreset]:
    return [ToolPreset(preset.name, dict(preset.values)) for preset in presets]


def _clone_action_groups(groups: Iterable[ToolActionGroup]) -> list[ToolActionGroup]:
    cloned: list[ToolActionGroup] = []
    for group in groups:
        actions = [
            ToolActionSpec(
                key=action.key,
                label=action.label,
                kind=action.kind,
                enabled_when=action.enabled_when,
            )
            for action in group.actions
        ]
        cloned.append(ToolActionGroup(id=group.id, label=group.label, actions=actions))
    return cloned


def _apply_icon_override(tool_id: str, icon_svg: str | None) -> str | None:
    resolved = resolve_tool_icon_path(tool_id)
    if resolved is not None and resolved.is_file():
        return str(resolved)
    return icon_svg


def _default_metadata_from_descriptor(descriptor: ToolDescriptor) -> ToolMetadata:
    category = (descriptor.category or "brush").lower()
    if category in {"brush", "draw"}:
        props = _clone_property_specs(_DEFAULT_BRUSH_PROPERTIES)
        presets = _clone_presets(_DEFAULT_BRUSH_PRESETS)
    elif category in {"fill", "bucket"}:
        props = _clone_property_specs(_DEFAULT_FILL_PROPERTIES)
        presets = []
    elif category in {"selection", "select"}:
        props = _clone_property_specs(_DEFAULT_SELECTION_PROPERTIES)
        presets = []
    else:
        props = []
        presets = []

    icon_svg = _apply_icon_override(descriptor.id, descriptor.icon_svg)

    return ToolMetadata(
        tool_id=descriptor.id,
        display_name=descriptor.display_name,
        category=descriptor.category or "tools",
        tags=[descriptor.category or "tools"],
        properties=props,
        presets=presets,
        icon_svg=icon_svg,
        group_id=(descriptor.category or descriptor.id),
        group_label=(descriptor.category or descriptor.display_name).title(),
        group_order=descriptor.order,
        action_groups=[],
    )


class ToolRegistry:
    """Owns registered tool state, metadata, presets, and active-tool identity."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolState] = {}
        self._metadata: dict[str, ToolMetadata] = {}
        self._active_tool_id: str | None = None
        self._active_tool: ToolPlugin | None = None

    @property
    def tools(self) -> dict[str, ToolState]:
        return self._tools

    @property
    def metadata(self) -> dict[str, ToolMetadata]:
        return self._metadata

    @property
    def active_tool_id(self) -> str | None:
        return self._active_tool_id

    @property
    def active_tool(self) -> ToolPlugin | None:
        return self._active_tool

    def has_tool(self, tool_id: str) -> bool:
        return tool_id in self._tools

    def get_state(self, tool_id: str) -> ToolState | None:
        return self._tools.get(tool_id)

    def register_state(self, tool_id: str, state: ToolState) -> None:
        self._tools[tool_id] = state

    def remove_state(self, tool_id: str) -> None:
        del self._tools[tool_id]

    def activate_tool(self, tool_id: str) -> ToolState:
        state = self._tools[tool_id]
        self._active_tool_id = tool_id
        self._active_tool = state.plugin
        return state

    def clear_active_tool(self) -> None:
        self._active_tool_id = None
        self._active_tool = None

    def register_tool_metadata(self, metadata: ToolMetadata) -> None:
        cloned = ToolMetadata(
            tool_id=metadata.tool_id,
            display_name=metadata.display_name,
            category=metadata.category,
            tags=list(metadata.tags),
            properties=_clone_property_specs(metadata.properties),
            presets=_clone_presets(metadata.presets),
            icon_svg=metadata.icon_svg,
            group_id=metadata.group_id,
            group_label=metadata.group_label,
            group_order=metadata.group_order,
            action_groups=_clone_action_groups(metadata.action_groups),
        )
        cloned.icon_svg = _apply_icon_override(cloned.tool_id, cloned.icon_svg)
        self._metadata[cloned.tool_id] = cloned
        self.apply_metadata_defaults(cloned, self._tools.get(cloned.tool_id))

    def iter_tool_metadata(self) -> Iterable[ToolMetadata]:
        return tuple(self._metadata.values())

    def list_tool_categories(self) -> list[str]:
        categories = {meta.category for meta in self._metadata.values()}
        return sorted(categories)

    def get_tool_metadata(self, tool_id: str) -> ToolMetadata | None:
        return self._metadata.get(tool_id)

    def get_tool_presets(self, tool_id: str) -> list[ToolPreset]:
        metadata = self._metadata.get(tool_id)
        if not metadata:
            return []
        return _clone_presets(metadata.presets)

    def get_preset_values(self, tool_id: str, preset_name: str) -> dict[str, Any] | None:
        metadata = self._metadata.get(tool_id)
        if not metadata:
            return None
        for preset in metadata.presets:
            if preset.name == preset_name:
                return dict(preset.values)
        return None

    def save_preset(self, tool_id: str, preset: ToolPreset, *, overwrite: bool = True) -> None:
        metadata = self._metadata.get(tool_id)
        if not metadata:
            return
        existing_index = next(
            (
                idx
                for idx, candidate in enumerate(metadata.presets)
                if candidate.name == preset.name
            ),
            None,
        )
        if existing_index is not None:
            if overwrite:
                metadata.presets[existing_index] = ToolPreset(preset.name, dict(preset.values))
            return
        metadata.presets.append(ToolPreset(preset.name, dict(preset.values)))

    def delete_preset(self, tool_id: str, preset_name: str) -> None:
        metadata = self._metadata.get(tool_id)
        if not metadata:
            return
        metadata.presets = [preset for preset in metadata.presets if preset.name != preset_name]

    def apply_metadata_defaults(self, metadata: ToolMetadata, state: ToolState | None) -> None:
        if not state:
            return
        for spec in metadata.properties:
            if spec.default_value is None:
                continue
            state.settings.setdefault(spec.key, spec.default_value)

    def ensure_metadata(self, descriptor: ToolDescriptor) -> ToolMetadata:
        metadata = self._metadata.get(descriptor.id)
        if metadata is None:
            metadata = _default_metadata_from_descriptor(descriptor)
            self._metadata[descriptor.id] = metadata
        self.apply_metadata_defaults(metadata, self._tools.get(descriptor.id))
        return metadata
