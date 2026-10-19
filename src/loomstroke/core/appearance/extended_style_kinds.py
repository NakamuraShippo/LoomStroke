"""Schemas for the six additional built-in layer styles."""

from .appearance_kind import AppearanceKind
from .appearance_param import AppearanceParam
from .relief_style_parameters import IDENTITY_PROFILE

_BLACK = [0, 0, 0, 255]
_WHITE = [255, 255, 255, 255]


def _opacity() -> AppearanceParam:
    return AppearanceParam("opacity", "float", 1.0, "Opacity", 0.0, 1.0, step=0.05)


def _relief() -> tuple[AppearanceParam, ...]:
    return (
        AppearanceParam("size", "int", 6, "Size", 1, 64, step=1),
        AppearanceParam("depth", "float", 3.0, "Depth", 0.0, 10.0, step=0.1),
        AppearanceParam("soften", "int", 0, "Soften", 0, 32, step=1),
        AppearanceParam("angle", "float", 135.0, "Light direction", 0.0, 360.0, step=1.0),
        AppearanceParam("elevation", "float", 45.0, "Elevation", 0.0, 90.0, step=1.0),
        AppearanceParam("lights", "lights", [], "Light sources"),
        AppearanceParam("profile", "curve", IDENTITY_PROFILE, "Profile"),
        AppearanceParam("highlight_color", "color", _WHITE, "Highlight color"),
        AppearanceParam("shadow_color", "color", _BLACK, "Shadow color"),
        _opacity(),
    )


def extended_style_kinds() -> tuple[AppearanceKind, ...]:
    return (
        AppearanceKind(
            "gaussian_blur",
            "style",
            "Gaussian Blur",
            (
                AppearanceParam("radius", "int", 4, "Radius", 0, 64, step=1),
                _opacity(),
            ),
        ),
        AppearanceKind(
            "inner_shadow",
            "style",
            "Inner Shadow",
            (
                AppearanceParam("offset_x", "float", 4.0, "Offset X", -256.0, 256.0, step=1.0),
                AppearanceParam("offset_y", "float", 4.0, "Offset Y", -256.0, 256.0, step=1.0),
                AppearanceParam("blur", "int", 4, "Blur", 0, 64, step=1),
                AppearanceParam("color", "color", _BLACK, "Color"),
                _opacity(),
            ),
        ),
        AppearanceKind(
            "inner_glow",
            "style",
            "Inner Glow",
            (
                AppearanceParam("size", "int", 6, "Size", 1, 64, step=1),
                AppearanceParam("color", "color", [255, 255, 160, 255], "Color"),
                _opacity(),
            ),
        ),
        AppearanceKind(
            "gradient_overlay",
            "style",
            "Gradient Overlay",
            (
                AppearanceParam("shape", "enum", "linear", "Shape", choices=("linear", "radial")),
                AppearanceParam("color_start", "color", _BLACK, "Start color"),
                AppearanceParam("color_end", "color", _WHITE, "End color"),
                AppearanceParam("angle", "float", 0.0, "Angle", -360.0, 360.0, step=1.0),
                AppearanceParam("scale", "float", 1.0, "Scale", 0.01, 4.0, step=0.05),
                AppearanceParam("center_x", "float", 0.5, "Center X", 0.0, 1.0, step=0.01),
                AppearanceParam("center_y", "float", 0.5, "Center Y", 0.0, 1.0, step=0.01),
                _opacity(),
            ),
        ),
        AppearanceKind(
            "bevel_emboss",
            "style",
            "Bevel / Emboss",
            (
                AppearanceParam(
                    "type", "enum", "inner", "Type", choices=("inner", "outer", "emboss", "pillow")
                ),
                AppearanceParam("invert", "bool", False, "Invert"),
                *_relief(),
            ),
        ),
        AppearanceKind(
            "three_d",
            "style",
            "3D",
            (
                *_relief(),
                AppearanceParam("ambient", "float", 0.3, "Ambient", 0.0, 1.0, step=0.05),
                AppearanceParam("diffuse", "float", 0.7, "Diffuse", 0.0, 1.0, step=0.05),
                AppearanceParam("specular", "float", 0.4, "Specular", 0.0, 1.0, step=0.05),
                AppearanceParam("shininess", "float", 24.0, "Shininess", 1.0, 128.0, step=1.0),
            ),
        ),
    )
