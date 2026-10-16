"""Advanced brush controls exposed through the existing Tool Properties panel."""

from ..sdk import PropertySpec, PropertyType


class PenDynamicsProperties:
    @staticmethod
    def build(settings) -> list[PropertySpec]:
        from loomstroke.ui.translations import translated_literal_domain  # noqa: PLC0415

        labels = {
            "scatter": "Scatter",
            "size_variation": "Size Variation",
            "angle_variation": "Angle Variation",
            "color_variation": "Color Variation",
            "texture_strength": "Paper Texture",
            "texture_scale": "Paper Grain Size",
            "color_mixing": "Color Mixing",
            "variation_seed": "Variation Seed",
        }
        result = []
        for key, value in settings.values().items():
            lower, upper = (
                (1, 64) if key == "texture_scale" else (0, 3) if key == "scatter" else (0, 1)
            )
            result.append(
                PropertySpec(
                    key=key,
                    display_name=translated_literal_domain(
                        "BasicPen", labels[key], use_fallback=True
                    ),
                    type=PropertyType.INT if key == "variation_seed" else PropertyType.FLOAT,
                    default_value=value,
                    min_value=lower,
                    max_value=2**31 - 1 if key == "variation_seed" else upper,
                    step=1 if key in {"variation_seed", "texture_scale"} else 0.05,
                )
            )
        return result
