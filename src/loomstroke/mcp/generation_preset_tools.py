"""Local workflow authoring and validated .lsfx library operations."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from loomstroke.plugins.ai_effect._atomic_io import atomic_write_binary
from loomstroke.plugins.ai_effect.lsfx_io import read_lsfx, write_lsfx_atomic
from loomstroke.plugins.ai_effect.manifest_validator import validate_manifest

from .editing_common import check_gui_thread, edit_operation, invalid
from .safety import scrub_secrets
from .types import McpStructuredError, McpToolSpec, ReasonCode

_OBJECT = {"type": "object"}
_TEXT = {"type": "string", "minLength": 1}
_FILE = {"path": _TEXT, "overwrite": {"type": "boolean", "default": False}}


def _tool(name: str, description: str, properties: dict, required: list, *, read_only=False):
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
        "loomstroke_inspect_generation_workflow",
        "Validate a gateway file and discover parameters, slots, and outputs locally.",
        {"gatewayId": _TEXT, "gatewayFile": _OBJECT},
        ["gatewayId", "gatewayFile"],
        read_only=True,
    ),
    _tool(
        "loomstroke_write_generation_preset",
        "Create or edit a validated .lsfx preset under MCP roots, optionally register it.",
        {**_FILE, "manifest": _OBJECT, "gatewayFile": _OBJECT, "register": {"type": "boolean"}},
        ["path", "manifest", "gatewayFile"],
    ),
    _tool(
        "loomstroke_import_generation_preset",
        "Validate and install a rooted .lsfx file into the existing user preset library.",
        _FILE,
        ["path"],
    ),
    _tool(
        "loomstroke_export_generation_preset",
        "Export an installed preset to a rooted .lsfx file preserving workflow and preview assets.",
        {**_FILE, "presetId": _TEXT},
        ["path", "presetId"],
    ),
)


def _providers(service: Any) -> tuple[Any, Any]:
    from .extension_services import get_services  # noqa: PLC0415

    providers = get_services(service)
    registry = providers.get("ai_effect_gateway_registry")
    library = providers.get("ai_effect_preset_library")
    if registry is None:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE, "Gateway registry is unavailable."
        )
    return registry, library


def _gateway(registry: Any, gateway_id: Any) -> Any:
    if not isinstance(gateway_id, str) or not gateway_id:
        raise invalid("'gatewayId' must be a non-empty string.", "gatewayId")
    try:
        return registry.get(gateway_id)
    except LookupError as exc:
        raise McpStructuredError(ReasonCode.NOT_FOUND, "Gateway is not registered.") from exc


def _validate(registry: Any, manifest: Any, gateway_file: Any) -> Any:
    if not isinstance(manifest, dict) or not isinstance(gateway_file, dict):
        raise invalid("Manifest and gatewayFile must be JSON objects.")
    result = validate_manifest(manifest)
    if not result.ok:
        raise invalid(f"Invalid preset manifest: {result.reason}.", "manifest")
    gateway = _gateway(registry, manifest.get("gateway"))
    result = gateway.validate_gateway_file(gateway_file)
    if not result.ok:
        raise invalid(f"Invalid gateway file: {result.reason}.", "gatewayFile")
    mappings = gateway_file.get("node_mappings")
    if gateway.gateway_id == "comfyui":
        required = {
            entry["internal_id"]
            for section in ("input_slots", "parameters", "outputs")
            for entry in manifest.get(section, [])
        }
        if not isinstance(mappings, dict) or not required.issubset(mappings):
            raise invalid("The workflow must map every declared slot, parameter, and output.")
    return gateway


def _describe(preset: Any, *, registered: bool) -> dict[str, Any]:
    return scrub_secrets(
        {
            "presetId": preset.preset_id,
            "manifest": copy.deepcopy(preset.manifest),
            "gatewayFile": copy.deepcopy(preset.primary_gateway_file()),
            "registered": registered,
            "path": str(preset.lsfx_path),
        }
    )


def _library(library: Any) -> Any:
    if library is None:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE, "Preset library is unavailable."
        )
    return library


def _install(library: Any, source: Any, *, overwrite: bool) -> Any:
    """Preserve the current library file if the legacy move/copy import fails."""
    incoming = read_lsfx(source)
    target = library._user_dir / source.name
    previous = library.get(incoming.preset_id) if incoming.preset_id in library else None
    if not overwrite and (
        previous is not None or (target.exists() and target.resolve() != source.resolve())
    ):
        raise McpStructuredError(
            ReasonCode.CONFLICT, "Preset or library file exists; require overwrite=true."
        )
    retained = {}
    for path in (target, getattr(previous, "lsfx_path", None)):
        if path is not None and path.is_file():
            retained[path] = path.read_bytes()
    try:
        return library.import_lsfx(source, overwrite=overwrite)
    except Exception:
        for path, data in retained.items():
            atomic_write_binary(
                path, lambda temporary, payload=data: temporary.write_bytes(payload)
            )
        raise


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    check_gui_thread()
    registry, library = _providers(service)
    args = copy.deepcopy(dict(arguments))
    for field in ("register", "overwrite"):
        if field in args and not isinstance(args[field], bool):
            raise invalid(f"'{field}' must be a boolean.", field)
    if name == "loomstroke_inspect_generation_workflow":
        gateway = _gateway(registry, args.get("gatewayId"))
        gateway_file = args.get("gatewayFile")
        if not isinstance(gateway_file, dict):
            raise invalid("'gatewayFile' must be a JSON object.", "gatewayFile")
        validation = gateway.validate_gateway_file(gateway_file)
        candidates = None
        if validation.ok:
            candidates = asdict(
                gateway.detect_candidates(gateway.extract_raw_workflow(gateway_file))
            )
        return scrub_secrets({"validation": asdict(validation), "candidates": candidates})
    if name == "loomstroke_import_generation_preset":
        source = service.safety.validate_input_path(args["path"], allowed_suffixes=(".lsfx",))
        preset = read_lsfx(source)
        _validate(registry, preset.manifest, preset.primary_gateway_file())
        _library(library)
        async with edit_operation(service, name, args, require_canvas=False):
            source = service.safety.validate_input_path(args["path"], allowed_suffixes=(".lsfx",))
            preset = read_lsfx(source)
            _validate(registry, preset.manifest, preset.primary_gateway_file())
            installed = _install(library, source, overwrite=args.get("overwrite", False))
            return _describe(installed, registered=True)
    target = service.safety.validate_output_path(
        args["path"], overwrite=args.get("overwrite", False), allowed_suffixes=(".lsfx",)
    )
    if name == "loomstroke_export_generation_preset":
        try:
            preset = _library(library).get(args["presetId"])
        except LookupError as exc:
            raise McpStructuredError(ReasonCode.NOT_FOUND, "Preset is not registered.") from exc
        async with edit_operation(service, name, args, require_canvas=False):
            target = service.safety.validate_output_path(
                args["path"], overwrite=args.get("overwrite", False), allowed_suffixes=(".lsfx",)
            )
            write_lsfx_atomic(
                target,
                preset.manifest,
                preset.gateway_files,
                icon_bytes=preset.icon_image,
                preview_bytes=preset.preview_image,
            )
            return _describe(read_lsfx(target), registered=False)
    if name != "loomstroke_write_generation_preset":
        raise invalid("Unknown preset operation.")
    return await _write_preset(service, name, args, registry, library, target)


async def _write_preset(
    service: Any, name: str, args: dict, registry: Any, library: Any, target: Any
) -> dict:
    manifest, gateway_file = args.get("manifest"), args.get("gatewayFile")
    _validate(registry, manifest, gateway_file)
    register = args.get("register", True)
    if register:
        _library(library)
        if manifest["preset_id"] in library and not args.get("overwrite", False):
            raise McpStructuredError(
                ReasonCode.CONFLICT, "Preset already exists; require overwrite=true."
            )
        library_target = library._user_dir / target.name
        if (
            library_target.exists()
            and library_target.resolve() != target.resolve()
            and not args.get("overwrite", False)
        ):
            raise McpStructuredError(
                ReasonCode.CONFLICT, "Library filename already exists; require overwrite=true."
            )
    async with edit_operation(service, name, args, require_canvas=False):
        target = service.safety.validate_output_path(
            args["path"], overwrite=args.get("overwrite", False), allowed_suffixes=(".lsfx",)
        )
        previous = (
            library.get(manifest["preset_id"])
            if register and manifest["preset_id"] in library
            else None
        )
        write_lsfx_atomic(
            target,
            manifest,
            {manifest["gateway"]: gateway_file},
            icon_bytes=getattr(previous, "icon_image", None),
            preview_bytes=getattr(previous, "preview_image", None),
        )
        preset = read_lsfx(target)
        if register:
            preset = _install(library, target, overwrite=args.get("overwrite", False))
        return _describe(preset, registered=register)
