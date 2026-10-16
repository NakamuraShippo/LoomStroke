"""Tool-properties presentation of the independent pen pressure curves."""

from __future__ import annotations

from ..sdk import PropertySpec, PropertyType
from .pressure_response_settings import PressureResponseSettings


class PressureResponseProperties:
    @staticmethod
    def build(settings: PressureResponseSettings) -> list[PropertySpec]:
        from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

        specs = []
        labels = {
            "size_pressure_gamma": "Size Pressure Curve",
            "size_pressure_minimum": "Minimum Pressure Size",
            "opacity_pressure_enabled": "Opacity Pressure",
            "opacity_pressure_gamma": "Opacity Pressure Curve",
            "opacity_pressure_minimum": "Minimum Pressure Opacity",
        }
        for key, value in settings.values().items():
            specs.append(
                PropertySpec(
                    key=key,
                    display_name=translated_literal_domain(
                        "BasicPen", labels[key], use_fallback=True
                    ),
                    type=PropertyType.BOOL if isinstance(value, bool) else PropertyType.FLOAT,
                    default_value=value,
                    min_value=0.1 if key.endswith("gamma") else 0.0,
                    max_value=4.0 if key.endswith("gamma") else 1.0,
                    step=0.05,
                )
            )
        return specs
