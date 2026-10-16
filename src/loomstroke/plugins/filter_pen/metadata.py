"""Filter Pen's tool UI schema and translation boundary."""

from __future__ import annotations

from loomstroke.core.filter_pen.compatibility import bind_legacy_names as _bind_legacy_names
from loomstroke.core.filters import FilterRegistry
from loomstroke.plugins.sdk import (
    PropertySpec,
    PropertyType,
    ToolActionGroup,
    ToolActionSpec,
    ToolDescriptor,
    ToolMetadata,
)
from loomstroke.ui.translations import TranslatedLiteral, translated_literal_domain

from .settings import FilterPenSettings
from .translations import JA_LABELS, filter_text


def tr(text: str) -> str:
    return translated_literal_domain("FilterPen", text, use_fallback=True)


def descriptor() -> ToolDescriptor:
    return ToolDescriptor(
        id="effect_pen",
        display_name=tr("Filter Pen"),
        category="brush",
        cursor="crosshair",
        order=2,
        shortcut_description=tr("Activate Filter Pen"),
    )


def metadata(settings: FilterPenSettings) -> ToolMetadata:
    values = settings.to_dict()

    def number(
        key: str, label: str, low: float, high: float, step: float, *, common: bool = False
    ) -> PropertySpec:
        return PropertySpec(
            key=key,
            display_name=tr(label),
            type=PropertyType.FLOAT,
            min_value=low,
            max_value=high,
            default_value=values[key],
            step=step,
            is_common=common,
        )

    def toggle(key: str, label: str) -> PropertySpec:
        return PropertySpec(
            key=key,
            display_name=tr(label),
            type=PropertyType.BOOL,
            default_value=values[key],
        )

    return ToolMetadata(
        tool_id="effect_pen",
        display_name=tr("Filter Pen"),
        category="brush",
        tags=["brush", "effect", "filter"],
        group_id="brush.basic",
        group_label=tr("Brushes"),
        group_order=10,
        properties=[
            PropertySpec(
                key="effect",
                display_name=tr("Filter"),
                type=PropertyType.ENUM,
                default_value=filter_name(settings.filter_type),
                # Keep English itemData while the panel translates the stored domain.
                enum_items=[
                    TranslatedLiteral(
                        name, name, "FilterPen" if name in JA_LABELS else "FilterPanel"
                    )
                    for _filter_id, name, _category in FilterRegistry.list_filters()
                ],
            ),
            number("size", "Size", 1, 1000, 1, common=True),
            number("opacity", "Filter Strength", 0, 1, 0.01, common=True),
            number("hardness", "Hardness", 0, 1, 0.01, common=True),
            number("stamp_density", "Stamp Density", 0, 1, 0.01),
            number("flow", "Flow", 0, 1, 0.01),
            number("tone_range", "Tone Range", 0, 1, 0.05),
            number("tone_gamma", "Tone Gamma", 0.1, 2, 0.05),
            number("smoothing", "Stabilization", 0, 1, 0.05),
            toggle("pressure_sensitive", "Pressure Sensitive"),
            toggle("tilt_angle", "Tilt Shading"),
            toggle("recontour", "Recontour"),
            number("recontour_smoothness", "RecontourSmoothness (next stroke)", 0, 100, 1),
        ],
        action_groups=[
            ToolActionGroup(
                id="effect_pen.settings",
                label=tr("Filter"),
                actions=[ToolActionSpec("effect_settings", tr("Filter Settings…"))],
            )
        ],
    )


def filter_name(filter_id: str | None = None, *, effect_id: str | None = None) -> str:
    if effect_id is not None:
        if filter_id is not None and filter_id != effect_id:
            raise TypeError("Conflicting filter_id and effect_id")
        filter_id = effect_id
    if filter_id is None:
        raise TypeError("filter_name() requires filter_id")
    return next(
        (
            name
            for registered_id, name, _ in FilterRegistry.list_filters()
            if registered_id == filter_id
        ),
        filter_id,
    )


def resolve_filter(value: str) -> str | None:
    if FilterRegistry.get(value) is not None:
        return value
    for filter_id, name, _category in FilterRegistry.list_filters():
        if value in (name, filter_text(name)):
            return filter_id
    return None


# Compatibility aliases for existing imports.
effect_name = filter_name
resolve_effect = resolve_filter


_bind_legacy_names(
    __name__,
    {
        "EffectRegistry": "FilterRegistry",
        "EffectPenSettings": "FilterPenSettings",
        "effect_text": "filter_text",
        "effect_name": "filter_name",
        "resolve_effect": "resolve_filter",
    },
)
