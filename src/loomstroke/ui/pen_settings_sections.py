"""Presentation-only categories for the existing Basic Pen setting keys."""

PEN_SETTING_SECTIONS = (
    (
        "basic",
        "Basic Settings",
        ("size", "opacity", "hardness", "flow", "stamp_density", "smoothing"),
    ),
    (
        "stroke",
        "Stroke Shape",
        ("taper_start_length", "taper_end_length", "recontour", "recontour_smoothness"),
    ),
    (
        "pressure",
        "Pressure Response",
        (
            "pressure_sensitive",
            "size_pressure_gamma",
            "size_pressure_minimum",
            "opacity_pressure_enabled",
            "opacity_pressure_gamma",
            "opacity_pressure_minimum",
            "tilt_angle",
        ),
    ),
    ("tone", "Tone and Shading", ("tone_range", "tone_gamma")),
    ("texture", "Texture and Mixing", ("texture_strength", "texture_scale", "color_mixing")),
    (
        "variation",
        "Scatter and Variation",
        ("scatter", "size_variation", "angle_variation", "color_variation", "variation_seed"),
    ),
)


def pen_section_for_key(key: str) -> str:
    return next((section for section, _title, keys in PEN_SETTING_SECTIONS if key in keys), "other")
