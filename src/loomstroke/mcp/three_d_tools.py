"""Typed model assets and command-backed 3D layers using existing neutral scene services."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from loomstroke.core.assets_3d import load_scene
from loomstroke.core.assets_3d.file_io import export_glb, export_gltf, export_obj
from loomstroke.core.commands_layer_3d import SetLayer3DCameraCommand, SetLayer3DDisplayModeCommand
from loomstroke.core.layer_3d_rendering import orbit_camera_state
from loomstroke.plugins.ai_effect._atomic_io import atomic_write_binary
from loomstroke.rendering_3d import (
    PreviewDisplayMode,
    ProjectionImportRequest,
    ProjectionImportService,
)

from .editing_common import check_gui_thread, edit_operation, finite_number, invalid
from .layer_operations import _editable, _layer
from .safety import scrub_secrets
from .types import AssetKind, AssetRecord, McpStructuredError, McpToolSpec, ReasonCode

_TEXT = {"type": "string", "minLength": 1}
_LAYER = {"layerId": _TEXT}
_CAMERA = {
    "type": "object",
    "properties": {
        "yaw_degrees": {"type": "number"},
        "pitch_degrees": {"type": "number", "minimum": -89, "maximum": 89},
        "zoom": {"type": "number", "minimum": 0.05, "maximum": 20},
        "distance": {"type": "number", "exclusiveMinimum": 0},
        "target": {"type": "array", "minItems": 3, "maxItems": 3, "items": {"type": "number"}},
    },
    "additionalProperties": False,
}
_MODE = {"enum": [mode.value for mode in PreviewDisplayMode]}


def _tool(name, description, properties, required, *, read_only=False):
    return McpToolSpec(
        name,
        description,
        {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        read_only=read_only,
        destructive=not read_only,
    )


TOOLS = (
    _tool(
        "loomstroke_import_3d_asset",
        "Register a rooted GLB, glTF, or OBJ asset and optionally create or replace a 3D layer.",
        {
            "path": _TEXT,
            "layerId": _TEXT,
            "name": _TEXT,
            "applyToCanvas": {"type": "boolean", "default": True},
        },
        ["path"],
    ),
    _tool(
        "loomstroke_get_3d_layer",
        "Read 3D layer scene statistics, camera, display mode, and generation provenance.",
        _LAYER,
        ["layerId"],
        read_only=True,
    ),
    _tool(
        "loomstroke_update_3d_layer",
        "Update camera and display mode through one undoable history group.",
        {**_LAYER, "camera": _CAMERA, "displayMode": _MODE, "expectedRevision": _TEXT},
        ["layerId"],
    ),
    _tool(
        "loomstroke_export_3d_model",
        "Atomically export the selected 3D layer as rooted GLB, glTF, or OBJ.",
        {**_LAYER, "path": _TEXT, "overwrite": {"type": "boolean"}},
        ["layerId", "path"],
    ),
    _tool(
        "loomstroke_project_3d_layer",
        "Render the 3D layer camera projection to an explicit raster layer via ApplyImageCommand.",
        {
            **_LAYER,
            "targetLayerId": _TEXT,
            "width": {"type": "integer", "minimum": 1, "maximum": 4096},
            "height": {"type": "integer", "minimum": 1, "maximum": 4096},
            "expectedRevision": _TEXT,
        },
        ["layerId", "targetLayerId"],
    ),
)


def register_model_asset(service: Any, path: Any, *, related_layer_id=None, metadata=None) -> dict:
    """Register only validated model bytes; callable by generation result adapters too."""
    asset_id = f"scene3d:{uuid.uuid4().hex}"
    mime = {".glb": "model/gltf-binary", ".gltf": "model/gltf+json", ".obj": "model/obj"}[
        path.suffix.lower()
    ]
    asset = AssetRecord(
        asset_id,
        AssetKind.SCENE3D.value,
        "generation" if metadata and metadata.get("jobId") else "import",
        mime_type=mime,
        related_layer_id=related_layer_id,
        metadata={"format": path.suffix[1:].lower(), **dict(metadata or {})},
    )
    service._asset_records[asset_id] = asset
    service._asset_payloads[asset_id] = path.read_bytes()
    return asset.to_schema()


def _scene_layer(canvas: Any, layer_id: Any) -> Any:
    layer = _layer(canvas, layer_id)
    if layer.layer_type != "3d" or layer.scene_3d is None:
        raise invalid("The layer must contain a 3D scene.", "layerId")
    return layer


def _describe(layer: Any) -> dict:
    scene = layer.scene_3d
    camera = layer.camera_3d
    if camera is not None and not isinstance(camera, Mapping):
        camera = asdict(camera)
    return scrub_secrets(
        {
            "layerId": layer.id,
            "sceneId": scene.id,
            "meshCount": len(scene.meshes),
            "nodeCount": len(scene.nodes),
            "camera": camera,
            "displayMode": layer.display_mode_3d,
            "generation": layer.gen_3d,
        }
    )


def _camera(values: Any, current: Any, scene: Any) -> dict:
    if not isinstance(values, Mapping) or set(values) - _CAMERA["properties"].keys():
        raise invalid("Invalid camera fields.", "camera")
    camera = asdict(orbit_camera_state(current, scene))
    for key, value in values.items():
        if key == "target":
            if not isinstance(value, list | tuple) or len(value) != 3:
                raise invalid("Camera target requires three coordinates.")
            camera[key] = [finite_number(item, "target") for item in value]
        else:
            bounds = {
                "pitch_degrees": (-89, 89),
                "zoom": (0.05, 20),
                "distance": (0.000001, None),
            }.get(key, (None, None))
            camera[key] = finite_number(value, key, minimum=bounds[0], maximum=bounds[1])
    return camera


async def _import_asset(service: Any, name: str, args: dict) -> dict:
    path = service.safety.validate_input_path(
        args["path"], allowed_suffixes=(".glb", ".gltf", ".obj")
    )
    if path.stat().st_size > 64 * 1024 * 1024:
        raise invalid("3D model exceeds 64 MiB.", "path")
    try:
        scene = load_scene(path)
    except (ValueError, OSError) as exc:
        raise invalid("The 3D model cannot be loaded.", "path") from exc
    apply = args.get("applyToCanvas", True)
    if not isinstance(apply, bool):
        raise invalid("'applyToCanvas' must be a boolean.")
    async with edit_operation(service, name, args, require_canvas=apply) as canvas:
        layer_id = args.get("layerId")
        if apply:
            if layer_id:
                _editable(canvas, _layer(canvas, layer_id))
                if not canvas.set_3d_layer_scene(layer_id, scene=scene, name=args.get("name")):
                    raise McpStructuredError(ReasonCode.BACKEND_FAILED, "3D layer update failed.")
            else:
                layer_id = canvas.add_3d_layer(name=args.get("name") or path.stem, scene=scene).id
        asset = register_model_asset(service, path, related_layer_id=layer_id)
        return {"asset": asset, "layerId": layer_id, "appliedToCanvas": apply}


async def _export_model(service: Any, name: str, args: dict, layer: Any) -> dict:
    path = service.safety.validate_output_path(
        args["path"],
        overwrite=args.get("overwrite", False),
        allowed_suffixes=(".glb", ".gltf", ".obj"),
    )
    async with edit_operation(service, name, args):
        path = service.safety.validate_output_path(
            args["path"],
            overwrite=args.get("overwrite", False),
            allowed_suffixes=(".glb", ".gltf", ".obj"),
        )
        exporter = {".glb": export_glb, ".gltf": export_gltf, ".obj": export_obj}[
            path.suffix.lower()
        ]
        atomic_write_binary(path, lambda temporary: exporter(layer.scene_3d, temporary))
        return {"path": str(path), "format": path.suffix[1:], "bytes": path.stat().st_size}


async def _update_layer(service: Any, name: str, args: dict, layer: Any) -> dict:
    canvas = service.canvas_service
    camera = _camera(args["camera"], layer.camera_3d, layer.scene_3d) if "camera" in args else None
    mode = args.get("displayMode")
    if mode is not None and mode not in _MODE["enum"]:
        raise invalid("Invalid display mode.")
    if camera is None and mode is None:
        raise invalid("Provide camera or displayMode.")
    async with edit_operation(service, name, args):
        _editable(canvas, layer)
        changed = False
        canvas.begin_transaction("Update 3D layer")
        try:
            if camera is not None:
                changed |= bool(canvas.submit(SetLayer3DCameraCommand(layer.id, camera)))
            if mode is not None:
                changed |= bool(canvas.submit(SetLayer3DDisplayModeCommand(layer.id, mode)))
        finally:
            canvas.end_transaction()
        return {"changed": changed, **_describe(layer)}


async def _project_layer(service: Any, name: str, args: dict, layer: Any) -> dict:
    canvas = service.canvas_service
    target = _layer(canvas, args.get("targetLayerId"))
    _editable(canvas, target)
    if target.layer_type != "raster":
        raise invalid("Projection target must be a raster layer.")
    canvas_width, canvas_height = canvas.get_canvas_size()
    width = finite_number(args.get("width", canvas_width), "width", minimum=1, maximum=4096)
    height = finite_number(args.get("height", canvas_height), "height", minimum=1, maximum=4096)
    if not width.is_integer() or not height.is_integer():
        raise invalid("Projection dimensions must be integers.")
    request = ProjectionImportRequest(
        width=int(width),
        height=int(height),
        target_layer_id=target.id,
        camera=orbit_camera_state(layer.camera_3d, layer.scene_3d),
        display_mode=PreviewDisplayMode(layer.display_mode_3d),
    )
    async with edit_operation(service, name, args):
        _editable(canvas, target)
        result = ProjectionImportService().import_to_canvas(layer.scene_3d, canvas, request)
        return {
            "appliedToCanvas": result.submitted,
            "layerId": target.id,
            "fallbacks": list(result.preview.fallbacks),
        }


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    check_gui_thread()
    args = dict(arguments)
    if name == "loomstroke_import_3d_asset":
        return await _import_asset(service, name, args)
    canvas = service.canvas_service
    if canvas is None:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE, "An editable canvas is required."
        )
    layer = _scene_layer(canvas, args.get("layerId"))
    if name == "loomstroke_get_3d_layer":
        return _describe(layer)
    if name != "loomstroke_export_3d_model":
        _editable(canvas, layer)
    handlers = {
        "loomstroke_export_3d_model": _export_model,
        "loomstroke_update_3d_layer": _update_layer,
        "loomstroke_project_3d_layer": _project_layer,
    }
    if name not in handlers:
        raise invalid("Unknown 3D operation.")
    return await handlers[name](service, name, args, layer)
