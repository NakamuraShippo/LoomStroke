"""Specialize spatial/color kernels and mapping before native driver compilation."""

from loomstroke.core.appearance.style_blending import STYLE_BLEND_MODES

from .extended_layer_style_shader import EXTENDED_RGB_WGSL, STYLE_MATH_WGSL
from .layer_style_shader import LAYER_STYLE_WGSL
from .relief_style_shader import RELIEF_WGSL
from .style_backdrop_shader import RGB_BACKDROP_WGSL, STYLE_LOCAL_WGSL
from .style_blend_shader import RGB_STYLE_BLEND, style_mode_id


def _body(code: str, marker: str) -> str:
    start = code.index("{", code.index(marker)) + 1
    depth = 1
    for end in range(start, len(code)):
        depth += (code[end] == "{") - (code[end] == "}")
        if depth == 0:
            return code[start:end]
    raise ValueError(marker)


def layer_style_kernel(op: int, document: bool = False) -> str:
    header = LAYER_STYLE_WGSL.split("@fragment", 1)[0]
    local = STYLE_LOCAL_WGSL + """
fn style_sample(t: texture_2d<f32>,p: vec2i) -> vec4f {
    return load(t,vec2i(floor(style_local(p)+vec2f(.5))));
}
"""
    declarations = f"let p=vec2i(position.xy); let op={op}; let radius=i32(params.operation.y);"
    helpers = ""
    if op in {0, 1, 2, 3}:
        body = _body(LAYER_STYLE_WGSL, f"if op == {op} {{")
        declarations += "let base=load(source,p);"
    elif op in {6, 12, 13}:
        body = {
            6: "return vec4f(load(source,p).a);",
            12: "return style_sample(source,p);",
            13: "return vec4f(0.);",
        }[op]
        helpers = local if op == 12 else ""
    elif op in {7, 10}:
        body = _body(EXTENDED_RGB_WGSL, "if op == 7 || op == 10")
        helpers = (
            "fn style_weight(radius: i32,offset: i32) -> f32 {"
            + _body(STYLE_MATH_WGSL, "fn style_weight")
            + "}"
        )
    else:
        helpers = RGB_STYLE_BLEND + RGB_BACKDROP_WGSL
        declarations += """
    var base=load(source,p);
    if params.blending.y>.5 && params.blending.z<.5 {base=style_sample(source,p);}
    var old=load(previous,p);
    style_prepare(p);
"""
        if op == 4:
            body = _body(LAYER_STYLE_WGSL, "@fragment")
            body = body[body.index("    let mode = i32(params.operation.z);") :]
        elif op in {8, 14}:
            body = _body(EXTENDED_RGB_WGSL, "if op == 8 || op == 14")
        elif op == 9:
            body = _body(EXTENDED_RGB_WGSL, "if op == 9")
            helpers += (
                "fn style_gradient(p: vec2i) -> f32 {"
                + _body(STYLE_MATH_WGSL, "fn style_gradient").replace(
                    "STYLE_RADIAL", "params.operation.z>.5"
                )
                + "}"
            )
        elif op == 11:
            body = _body(EXTENDED_RGB_WGSL, "fn extended_rgb")
            body = body[body.index("    let relief = style_relief") :]
            helpers += EXTENDED_RGB_WGSL[: EXTENDED_RGB_WGSL.index("fn extended_rgb")]
            helpers += RELIEF_WGSL
        else:
            raise ValueError(f"Unknown RGB style kernel {op}")
    code = header + helpers + """
@fragment fn fs_main(@builtin(position) position: vec4f) -> @location(0) vec4f {
""" + declarations + body + "}"
    if "fn blend_color" in code:
        original = _body(code, "fn blend_color")
        cases = "".join(
            f"case {mode}: {{" + _body(code, f"if (mode == {mode})") + "}"
            for mode in sorted({style_mode_id(name) for name in STYLE_BLEND_MODES})
        )
        code = code.replace(original, "switch mode {" + cases + "default: {return s;}}", 1)
    if "fn style_local" in code:
        local_body = _body(code, "fn style_local")
        mapped = local_body[local_body.index("    let pixel=") :]
        code = code.replace(local_body, mapped if document else "return vec2f(p);", 1)
    # Dynamic mapping branches inside repeated alpha/relief texture loads make
    # the D3D12 compiler recurse excessively. Both paths use the same layout.
    return code.replace("params.blending.y", "1." if document else "0.")
