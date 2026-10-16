"""Presentation-only groups for parameter-heavy tools; SDK metadata stays unchanged."""

from loomstroke.ui.pen_settings_sections import PEN_SETTING_SECTIONS

TOOL_SETTING_SECTIONS = {
    "basic_pen": PEN_SETTING_SECTIONS,
    "basic_eraser": (
        ("basic", "Basic Settings", ("size", "opacity", "hardness")),
        ("stroke", "Stroke Quality", ("spacing", "anti_alias")),
        ("vector", "Vector Erasing", ("vector_erase_mode",)),
    ),
    "basic_fill_bucket": (
        ("basic", "Basic Settings", ("fill_mode", "reference_mode", "color_tolerance")),
        ("region", "Region and Gaps", ("area_expansion", "gap_fill_size", "gap_close_px")),
        ("output", "Output", ("fill_target",)),
    ),
    "basic_gradient": (
        ("basic", "Basic Settings", ("gradient_type", "color_a", "color_b")),
        (
            "stops",
            "Color Stops",
            (
                "middle_stops",
                "stop_1_pos",
                "stop_1_color",
                "stop_2_pos",
                "stop_2_color",
                "stop_3_pos",
                "stop_3_color",
            ),
        ),
        ("quality", "Output Quality", ("dither",)),
    ),
    "loomstroke.basic.selection.rect": (
        ("basic", "Basic Settings", ("selection_mode", "operation", "pixel_perfect")),
        ("edges", "Selection Edges", ("feather_enabled", "feather_radius", "mask_radius")),
        (
            "transform",
            "Transform",
            (
                "transform_x",
                "transform_y",
                "transform_scale_x",
                "transform_scale_y",
                "transform_rotation",
            ),
        ),
    ),
    "vector_pen_precise": (
        ("basic", "Basic Settings", ("edit_mode", "stroke_width", "stroke_color")),
        ("fill", "Fill", ("fill_enabled", "fill_color")),
        ("path", "Path Shape", ("auto_close", "anchor_density", "handle_smoothness")),
    ),
    "vector_path_tool": (
        ("basic", "Basic Settings", ("stroke_width", "stroke_color", "fill_enabled")),
        ("fill", "Fill", ("fill_color",)),
        ("path", "Path Shape", ("shift_mode", "auto_close_tolerance_px")),
    ),
    "text_tool": (
        ("basic", "Basic Settings", ("font_size_pt", "word_wrap")),
        ("spacing", "Text Spacing", ("line_height", "tracking")),
        (
            "padding",
            "Frame Padding",
            ("padding_top", "padding_right", "padding_bottom", "padding_left"),
        ),
    ),
    "text_tool_vertical": (
        ("basic", "Basic Settings", ("font_size_pt", "word_wrap")),
        ("direction", "Writing Direction", ("vertical_flow", "rotate_half_width")),
        ("spacing", "Text Spacing", ("line_height", "tracking")),
        (
            "padding",
            "Frame Padding",
            ("padding_top", "padding_right", "padding_bottom", "padding_left"),
        ),
    ),
    "effect_pen": (
        (
            "basic",
            "Basic Settings",
            ("effect", "size", "opacity", "hardness", "stamp_density", "flow", "smoothing"),
        ),
        ("stroke", "Stroke Shape", ("recontour", "recontour_smoothness")),
        ("pressure", "Pressure Response", ("pressure_sensitive", "tilt_angle")),
        ("tone", "Tone and Shading", ("tone_range", "tone_gamma")),
    ),
    "sam3_ai_select": (
        ("basic", "Basic Settings", ("prompt_text", "mode", "source_scope", "apply_operation")),
        (
            "detection",
            "Detection",
            ("max_candidates", "min_score", "mask_threshold", "split_disconnected_instances"),
        ),
        ("preview", "Preview", ("show_all_instances", "preview_color_mode", "overlay_opacity")),
        (
            "interaction",
            "Click Behavior",
            ("click_auto_run", "click_accumulate_auto_apply", "candidate_click_auto_apply"),
        ),
        (
            "edges",
            "Selection Edges",
            (
                "auto_mask_cleanup",
                "combine_mode",
                "apply_edge_feather_px",
                "apply_edge_smooth_px",
                "apply_edge_tightness_px",
            ),
        ),
        (
            "export",
            "Export",
            (
                "export_padding_px",
                "export_source",
                "sheet_spacing_px",
                "sheet_power_of_two",
                "sheet_format",
            ),
        ),
    ),
}


def sections_for_tool(metadata):
    """Known tools use curated groups; long plugin forms retain all unknown fields."""
    if metadata.tool_id in TOOL_SETTING_SECTIONS:
        available = {spec.key for spec in metadata.properties}
        return tuple(
            (section, title, tuple(key for key in keys if key in available))
            for section, title, keys in TOOL_SETTING_SECTIONS[metadata.tool_id]
        )
    if len(metadata.properties) < 7:
        return ()
    common = [spec.key for spec in metadata.properties if spec.is_common]
    primary = common[:3] or [spec.key for spec in metadata.properties[:3]]
    return (("basic", "Basic Settings", tuple(primary)),)
