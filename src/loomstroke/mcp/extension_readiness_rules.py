"""Declarative dependencies of the additive editing tools, with input variants."""

from __future__ import annotations

from dataclasses import dataclass, replace


@dataclass(frozen=True, slots=True)
class Requirements:
    dependencies: tuple[str, ...] = ()
    canvas: bool = True
    roots: bool = False
    file_read: bool = False
    file_write: bool = False
    external: bool = False
    mutation: bool = True
    notes: tuple[str, ...] = ()
    conditional: bool = False


RULES: dict[str, dict[str, Requirements]] = {}


def _tools(names: str, **values: object) -> None:
    rule = Requirements(**values)
    for name in names.split():
        RULES["loomstroke_" + name] = {"default": rule}


def _actions(name: str, actions: str, base: Requirements, **overrides: Requirements) -> None:
    RULES["loomstroke_" + name] = {
        action: overrides.get(action, base) for action in actions.split()
    }


_tools("draw_stroke fill draw_gradient fill_pattern fill_advanced")
_tools("get_history_state get_selection list_vectors get_vector get_3d_layer", mutation=False)
_tools(
    "undo redo update_layer move_layer group_layers ungroup_layer set_layer_mask "
    "delete_layer clear_layer merge_layers rasterize_layer edit_selection update_vector "
    "delete_vector update_text update_parametric_shape transform update_3d_layer project_3d_layer"
)
_tools(
    "list_brushes get_brush_settings", canvas=False, dependencies=("tool_manager",), mutation=False
)
_tools("set_brush_settings apply_brush_preset", canvas=False, dependencies=("tool_manager",))
_tools("draw_stamp_stroke", dependencies=("brush_store",))
_tools("color_state", canvas=False, dependencies=("host_color",), mutation=False)
_tools("set_active_color", canvas=False, dependencies=("host_color",))
_tools("eyedropper", dependencies=("host_color",))
_tools("new_document close_document", canvas=False, dependencies=("document_workflow",))
_tools(
    "open_document", canvas=False, dependencies=("document_workflow",), roots=True, file_read=True
)
_tools("save_document", dependencies=("document_workflow",), roots=True, file_write=True)
_tools("effect_catalog", canvas=False, mutation=False)

_canvas = Requirements()
_read_canvas = replace(_canvas, mutation=False)
_actions(
    "manage_effect", "list get create update delete", _canvas, list=_read_canvas, get=_read_canvas
)
_actions("effect_mask", "add remove enable from_selection from_asset", _canvas)
_actions(
    "manage_canvas",
    "get resize resample set_dpi set_background flip rotate",
    _canvas,
    get=_read_canvas,
)
_profile = Requirements(canvas=False, mutation=False)
_actions(
    "canvas_profile",
    "list register assign",
    _canvas,
    list=_profile,
    register=Requirements(roots=True, file_read=True, file_write=False),
)

_pages = Requirements(dependencies=("page_service",))
_read_pages = replace(_pages, mutation=False, canvas=False)
_actions(
    "manage_page", "list add duplicate delete rename move set_active", _pages, list=_read_pages
)
_templates = replace(
    _pages,
    file_write=True,
    notes=("Template library edits persist without document Undo, matching the GUI.",),
)
_actions(
    "page_templates",
    "list preview capture duplicate rename delete set_category",
    _templates,
    list=_read_pages,
    preview=replace(_read_pages, file_read=True),
)
_book = replace(_pages, dependencies=("page_service", "document_service"))
_actions(
    "manage_book",
    "get set_layout set_page_attributes move_spread",
    _book,
    get=replace(_book, canvas=False, mutation=False),
    set_layout=replace(_book, dependencies=(*_book.dependencies, "book_metadata_port")),
)
_masters = replace(_book, dependencies=(*_book.dependencies, "masters_metadata_port"))
_actions(
    "manage_masters",
    "list capture rename delete apply unlink",
    _book,
    list=replace(_book, canvas=False, mutation=False),
    capture=_masters,
    rename=_masters,
    delete=_masters,
)

_tools(
    "export_image",
    roots=True,
    file_write=True,
    notes=(
        "Raster encoding runs on a worker. PSD/SVG preserve only the existing GUI export subset.",
    ),
)
_tools(
    "export_print_pdf export_webtoon export_digital_package",
    roots=True,
    file_write=True,
    dependencies=("page_service", "document_metadata|document_service"),
    notes=(
        "Uses existing GUI runners. Each page is limited to 16 MP, at most 5,000 selected pages.",
        "PDF is RGB/MediaBox, without PDF/X. Webtoon permits at most 10,000 slices.",
    ),
)

_palette = Requirements(dependencies=("palette_repository",), canvas=False, file_write=True)
_actions(
    "palette",
    "list get upsert delete add_swatch update_swatch delete_swatch reorder import export",
    _palette,
    list=replace(_palette, mutation=False, file_write=False),
    get=replace(_palette, mutation=False, file_write=False),
    export=replace(_palette, roots=True),
)
# 'import' is a Python keyword, so its action override is assigned explicitly.
RULES["loomstroke_palette"]["import"] = replace(_palette, roots=True, file_read=True)
_view = Requirements(dependencies=("viewport",), canvas=False)
_actions(
    "view",
    "get set pan fit reset",
    _view,
    get=replace(_view, mutation=False),
    fit=replace(_view, canvas=True),
)
_reference = Requirements(dependencies=("reference_viewer",), canvas=False)
_actions(
    "reference",
    "list add select remove opacity",
    _reference,
    list=replace(_reference, mutation=False),
    add=replace(_reference, roots=True, file_read=True),
)
_brush = Requirements(dependencies=("brush_store",), canvas=False, file_write=True)
_actions(
    "brush_asset",
    "list get import_tip import_abr from_selection update delete export_tip",
    _brush,
    list=replace(_brush, mutation=False, file_write=False),
    get=replace(_brush, mutation=False, file_read=True, file_write=False),
    import_tip=replace(_brush, roots=True, file_read=True),
    import_abr=replace(_brush, roots=True, file_read=True),
    from_selection=replace(_brush, canvas=True),
    export_tip=replace(_brush, roots=True),
)
_tools("list_shapes", dependencies=("shape_manager",), mutation=False)
_tools("register_shape", dependencies=("shape_manager",), file_write=True)
RULES["loomstroke_place_shape"] = {
    "library": Requirements(
        dependencies=("shape_manager",),
        notes=("kind=library (default): requires the Shape Library.",),
    ),
    "basic": Requirements(notes=("kind=basic: uses bundled parametric definitions.",)),
}
RULES["loomstroke_link_text_frames"] = {
    "page": Requirements(notes=("scope=page (default): links existing page-local frames.",)),
    "document": Requirements(
        dependencies=("pages",), notes=("scope=document: needs document story ports.",)
    ),
}
_styles = Requirements(dependencies=("pages",))
_actions("speaker_styles", "read update apply", _styles, read=replace(_styles, mutation=False))

_presets = ("ai_effect_gateway_registry", "ai_effect_preset_library")
_tools("inspect_generation_workflow", dependencies=(_presets[0],), canvas=False, mutation=False)
_tools(
    "import_generation_preset export_generation_preset",
    dependencies=_presets,
    canvas=False,
    roots=True,
    file_read=True,
    file_write=True,
)
RULES["loomstroke_write_generation_preset"] = {
    "register": Requirements(
        dependencies=_presets,
        canvas=False,
        roots=True,
        file_write=True,
        notes=("register=true (default): also installs into the existing preset library.",),
    ),
    "file_only": Requirements(
        dependencies=(_presets[0],),
        canvas=False,
        roots=True,
        file_write=True,
        notes=("register=false: writes a validated rooted file without library registration.",),
    ),
}
_tools(
    "list_llm_models get_llm_status get_llm_result",
    canvas=False,
    mutation=False,
    notes=(
        "Reads configured model IDs or an existing local job; "
        "no gateway request or credential read.",
    ),
)
_tools(
    "submit_llm",
    canvas=False,
    dependencies=("llm_gateway",),
    external=True,
    notes=(
        "imagePaths attachments require MCP roots; model/credentials are checked "
        "only by the requested gateway call.",
    ),
)
_llm = RULES["loomstroke_submit_llm"]["default"]
RULES["loomstroke_submit_llm"] = {
    "text": replace(_llm, notes=(*_llm.notes, "No imagePaths: rooted file access is unnecessary.")),
    "images": replace(
        _llm,
        roots=True,
        file_read=True,
        notes=(*_llm.notes, "Nonempty imagePaths: rooted attachments are read."),
    ),
}
_tools(
    "cancel_llm",
    canvas=False,
    external=True,
    notes=(
        "Requires an existing local job with a retained gateway; terminal jobs do not contact it.",
    ),
    conditional=True,
)
_tools(
    "restore_generation_to_panel regenerate_gallery_record",
    canvas=False,
    dependencies=("generation_history",),
    conditional=True,
    notes=(
        "The selected Gallery record must have a connected generation provider; "
        "no provider/panel is created by readiness.",
    ),
)
RULES["loomstroke_regenerate_gallery_record"]["default"] = replace(
    RULES["loomstroke_regenerate_gallery_record"]["default"], external=True
)
RULES["loomstroke_reuse_gallery_asset"] = {
    "asset_only": Requirements(
        dependencies=("generation_history",),
        canvas=False,
        file_read=True,
        notes=(
            "applyToCanvas=false (default): reads a saved image from the existing Gallery store.",
        ),
    ),
    "existing_layer": Requirements(
        dependencies=("generation_history",),
        file_read=True,
        notes=("applyToCanvas=true with layerId: applies to an editable raster layer.",),
    ),
    "new_layer": Requirements(
        dependencies=("generation_history", "document_service"),
        file_read=True,
        notes=("applyToCanvas=true without layerId: uses document image import.",),
    ),
}
_tools("list_comfyui_servers", canvas=False, dependencies=("comfyui_registry",), mutation=False)
_tools(
    "configure_comfyui_servers",
    canvas=False,
    dependencies=("comfyui_registry", "writable_settings"),
    file_write=True,
)
_tools(
    "set_default_comfyui_server cancel_comfyui_queue_job",
    canvas=False,
    dependencies=("comfyui_registry",),
    external=True,
    notes=(
        "The requested endpoint and queue job are checked only at execution; "
        "no connection probe is performed here.",
    ),
)
_tools(
    "get_comfyui_dependencies list_comfyui_queue",
    canvas=False,
    dependencies=("comfyui_registry",),
    external=True,
    mutation=False,
    notes=("No endpoint or remote node/model probe is performed by readiness.",),
)
_tools(
    "stop_comfyui_repeat",
    canvas=False,
    external=True,
    dependencies=("comfyui_native_author|comfyui_panel",),
)

RULES["loomstroke_import_3d_asset"] = {
    "asset_only": Requirements(
        canvas=False,
        roots=True,
        file_read=True,
        notes=("applyToCanvas=false: register a rooted model without editing a document.",),
    ),
    "canvas": Requirements(
        roots=True,
        file_read=True,
        notes=("applyToCanvas=true (default): imports or replaces a 3D layer with Undo.",),
    ),
}
_tools("export_3d_model", roots=True, file_write=True)

_sam = Requirements(dependencies=("sam_assets",), canvas=False, file_read=True, file_write=True)
_tools(
    "ai_select_combine_masks ai_select_refine_mask_asset ai_select_prepare_text_protection "
    "ai_select_prepare_manga_inpaint",
    canvas=False,
    dependencies=("sam_assets",),
    file_read=True,
    file_write=True,
    notes=(
        "Local saved-asset processing; explicit canvasWidth/Height are required "
        "when no canvas is available. No inference.",
    ),
)
_tools(
    "ai_select_analyze_protection",
    canvas=False,
    dependencies=("sam_assets",),
    file_read=True,
    mutation=False,
)
_tools(
    "ai_select_refine_candidate",
    canvas=False,
    dependencies=("sam_assets", "sam_refine_runtime"),
    file_read=True,
    file_write=True,
    notes=("Uses only the latest cached SAM readiness observation; no new model/worker probe.",),
)
RULES["loomstroke_ai_select_create_source_snapshot"] = {
    scope: replace(
        _sam,
        canvas=scope != "imported_asset",
        notes=(f"scope={scope}: source assets are persisted without inference.",),
    )
    for scope in ("active_layer", "visible_composite", "selection_bounds", "page", "imported_asset")
}
_tools(
    "ai_select_extract_to_layer ai_select_apply_clipping_mask ai_select_create_candidate_group "
    "ai_select_bind_mask_to_ai_effect_slot",
    dependencies=("sam_assets",),
    file_read=True,
)
_tools(
    "ai_select_export_cutout ai_select_export_sprite_atlas",
    dependencies=("sam_assets",),
    roots=True,
    file_read=True,
    file_write=True,
)
_tools(
    "sam31_health sam31_runtime_info",
    canvas=False,
    dependencies=("sam31_manager",),
    mutation=False,
    notes=(
        "The health tool can contact an already-running local worker; readiness itself does not.",
    ),
)
_tools(
    "sam31_load_model",
    canvas=False,
    dependencies=("sam31_manager", "sam31_config"),
    notes=(
        "Explicit execution can start/load the configured local worker and allocate GPU memory.",
    ),
)
_tools("sam31_unload_model", canvas=False, dependencies=("sam31_manager",))
_tools(
    "sam31_track_video",
    canvas=False,
    dependencies=("sam_assets", "sam31_manager", "sam31_config"),
    file_read=True,
    file_write=True,
    notes=(
        "Explicit execution can start/load the local worker; "
        "1-32 registered same-sized frames, total 64 MP.",
    ),
)
_tools(
    "sam31_export_video_masks",
    canvas=False,
    dependencies=("sam_assets",),
    roots=True,
    file_read=True,
    file_write=True,
)
