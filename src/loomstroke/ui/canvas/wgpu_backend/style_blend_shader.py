"""Reuse the canvas blend functions, including native four-ink complements."""

from loomstroke.core.appearance.style_blending import STYLE_BLEND_MODES

from .native_cmyk_shader import NativeCmykShader
from .shader_library import BLEND_COMPOSITOR_WGSL, WgpuShaderLibrary


def style_mode_id(mode: str) -> int:
    return WgpuShaderLibrary.BLEND_MODE_IDS.get(mode, 0) if mode in STYLE_BLEND_MODES else 0


RGB_STYLE_BLEND = (
    BLEND_COMPOSITOR_WGSL[
        BLEND_COMPOSITOR_WGSL.index("fn rgb2hsl(") : BLEND_COMPOSITOR_WGSL.index("@fragment")
    ]
    + """
fn style_color(source: vec3f, destination: vec3f) -> vec3f {
    return blend_color(source,destination,i32(params.blending.x));
}
fn style_over(below: vec4f, above: vec4f) -> vec4f {
    let a=above.a+below.a*(1.-above.a);
    let color=style_color(above.rgb,below.rgb);
    let value=above.rgb*above.a*(1.-below.a)+below.rgb*below.a*(1.-above.a)
              +color*above.a*below.a;
    return vec4f(value/max(a,.000001),a);
}
"""
)

NATIVE_STYLE_BLEND = NativeCmykShader.code().split("struct Params", 1)[0] + """
fn style_color(source: vec4f, destination: vec4f) -> vec4f {
    return vec4f(1.)-blend_color(vec4f(1.)-source,vec4f(1.)-destination,i32(params.blending.x));
}
"""
