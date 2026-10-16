"""Explicit preset-backed, asset-only generation for generic ProductionRecipe requests."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import inspect
import json
import uuid
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice
from PySide6.QtGui import QImage

from loomstroke.plugins.ai_effect.gateway_protocol import ExecutionRequest, SlotImage

from .editing_common import invalid
from .extension_services import get_services, require_service
from .generation_parameter_validation import validate_parameter_values
from .production_gateway_job import wait_asset_only_job
from .types import AssetKind, AssetRecord, McpStructuredError, ReasonCode


def validate_production_request(service: Any, request: Mapping[str, Any]) -> dict[str, Any]:
    """Local preflight; called before the runner queues a generic generation job."""
    constraints = request.get("constraints") or {}
    if not isinstance(constraints, Mapping):
        raise invalid("Recipe constraints must be an object.")
    target = constraints.get("generation")
    if not isinstance(target, Mapping):
        raise invalid(
            "Recipe constraints.generation must explicitly select a preset and prompt parameter."
        )
    if set(target) - {
        "presetId",
        "promptParameterId",
        "parameters",
        "targetServerId",
        "endpointUrl",
        "slotAssets",
    }:
        raise invalid("Unsupported production generation target fields.")
    library = require_service(service, "ai_effect_preset_library")
    registry = require_service(service, "ai_effect_gateway_registry")
    try:
        preset = library.get(target.get("presetId"))
        gateway = registry.get(preset.gateway_id)
    except (LookupError, TypeError) as exc:
        raise McpStructuredError(
            ReasonCode.NOT_FOUND, "The production preset or gateway is unavailable."
        ) from exc
    manifest = copy.deepcopy(preset.manifest)
    gateway_file = copy.deepcopy(preset.primary_gateway_file())
    validation = gateway.validate_gateway_file(gateway_file)
    if not validation.ok:
        raise invalid("The selected production gateway file is invalid.")
    values = _parameters(manifest, target, constraints, request.get("brief"))
    _validate_prompt_binding(gateway, gateway_file, target["promptParameterId"])
    _apply_target(service, gateway, gateway_file, target)
    slots = _slots(service, manifest, target)
    return {
        "preset": preset,
        "gateway": gateway,
        "manifest": manifest,
        "gatewayFile": gateway_file,
        "parameters": values,
        "slots": slots,
    }


def _parameters(manifest: dict, target: Mapping, constraints: Mapping, brief: Any) -> dict:
    parameters = {entry["internal_id"]: entry for entry in manifest.get("parameters", [])}
    prompt_id = target.get("promptParameterId")
    if not isinstance(prompt_id, str) or prompt_id not in parameters:
        raise invalid("The production promptParameterId must name an exposed preset parameter.")
    if parameters[prompt_id].get("type") not in {"string", "text", "multiline"}:
        raise invalid("The production prompt parameter must accept text.")
    values = {key: entry.get("default") for key, entry in parameters.items()}
    overrides = target.get("parameters") or {}
    if not isinstance(overrides, Mapping) or set(overrides) - parameters.keys():
        raise invalid("Production parameters must reference exposed preset parameters.")
    values.update(copy.deepcopy(dict(overrides)))
    if isinstance(brief, Mapping) and brief:
        try:
            brief = json.dumps(dict(brief), ensure_ascii=False, sort_keys=True, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise invalid("A structured production brief must contain JSON values.") from exc
    if not isinstance(brief, str) or not brief.strip():
        raise invalid("A non-empty text or structured production brief is required.")
    semantic = {key: value for key, value in constraints.items() if key != "generation"}
    values[prompt_id] = brief.strip()
    if semantic:
        values[prompt_id] += "\nConstraints: " + json.dumps(
            semantic, ensure_ascii=False, sort_keys=True
        )
    validate_parameter_values(parameters, values)
    return values


def _slots(service: Any, manifest: dict, target: Mapping) -> dict[str, SlotImage]:
    declared = {entry["internal_id"]: entry for entry in manifest.get("input_slots", [])}
    bindings = target.get("slotAssets") or {}
    if not isinstance(bindings, Mapping) or set(bindings) - declared.keys():
        raise invalid("Production slotAssets must reference exposed preset input slots.")
    if any(entry.get("required") and key not in bindings for key, entry in declared.items()):
        raise invalid("Required production image slots need explicit asset bindings.")
    slots = {}
    for slot_id, asset_id in bindings.items():
        if not isinstance(asset_id, str) or asset_id not in service._asset_records:
            raise McpStructuredError(ReasonCode.NOT_FOUND, "Production slot asset not found.")
        data = service._asset_payloads.get(asset_id)
        if not isinstance(data, bytes) or len(data) > 64 * 1024 * 1024:
            raise invalid("Production slot asset is unavailable or too large.")
        image = QImage.fromData(data)
        if image.isNull() or max(image.width(), image.height()) > 16384:
            raise invalid("Production slot asset must be a valid raster image.")
        png = QByteArray()
        buffer = QBuffer(png)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer, "PNG"):
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED, "Production slot image encoding failed."
            )
        slots[slot_id] = SlotImage(bytes(png), image.width(), image.height())
    return slots


def _apply_target(service: Any, gateway: Any, gateway_file: dict, target: Mapping) -> None:
    for source, dest in (("targetServerId", "target_server_id"), ("endpointUrl", "endpoint_url")):
        if source in target:
            if gateway.gateway_id != "comfyui":
                raise invalid("Server targets apply only to ComfyUI presets.")
            gateway_file[dest] = target[source]
    if target.get("endpointUrl"):
        from .comfyui_management_tools import _url  # noqa: PLC0415

        gateway_file["endpoint_url"] = _url(target["endpointUrl"])
    if target.get("targetServerId"):
        servers = require_service(service, "comfyui_registry")
        if servers.get(target["targetServerId"]) is None:
            raise McpStructuredError(ReasonCode.NOT_FOUND, "Production ComfyUI server not found.")


def _validate_prompt_binding(gateway: Any, gateway_file: dict, prompt_id: str) -> None:
    if gateway.gateway_id in {"gptimage2", "nanobanana"}:
        ref = (gateway_file.get("param_mappings") or {}).get(prompt_id)
        if not isinstance(ref, Mapping) or ref.get("param") != "prompt":
            raise invalid("Production prompt parameter must map to the gateway prompt.")
    elif gateway.gateway_id == "comfyui":
        ref = (gateway_file.get("node_mappings") or {}).get(prompt_id)
        workflow = gateway_file.get("workflow") or {}
        if (
            not isinstance(ref, Mapping)
            or not isinstance(ref.get("input"), str)
            or str(ref.get("node_id")) not in workflow
        ):
            raise invalid("Production prompt parameter must map to a workflow node input.")


def _raster_asset(service: Any, output: Any, job_id: str) -> dict:
    data = bytes(output.png_bytes)
    image = QImage.fromData(data)
    if image.isNull():
        raise McpStructuredError(ReasonCode.BACKEND_FAILED, "Generation returned an invalid image.")
    asset_id = f"production:{uuid.uuid4().hex}"
    record = AssetRecord(
        asset_id,
        AssetKind.GENERATED_OUTPUT.value,
        "generation",
        "image/png",
        dimensions={"width": image.width(), "height": image.height()},
        metadata={"jobId": job_id, "outputId": output.internal_id},
    )
    service._asset_records[asset_id] = record
    service._asset_payloads[asset_id] = data
    return record.to_schema()


class ProductionGenerationAdapter:
    """Preserve the live layer callback and add explicitly targeted recipe generation."""

    _loomstroke_production_generation_supported = True

    def __init__(self, service: Any, layer_submitter: Any):
        self.service = service
        self.layer_submitter = layer_submitter

    def capabilities(self) -> dict[str, bool]:
        providers = get_services(self.service)
        registry = providers.get("ai_effect_gateway_registry")
        library = providers.get("ai_effect_preset_library")
        production = (
            registry is not None
            and library is not None
            and any(
                preset.gateway_id in registry
                and not getattr(registry.get(preset.gateway_id), "_disposed", False)
                for preset in library.list_all()
            )
        )
        return {
            "available": self._layer_available() or production,
            "productionCandidates": production,
            "layerOnly": not production,
        }

    def _layer_available(self) -> bool:
        if not callable(self.layer_submitter):
            return False
        owner = getattr(self.layer_submitter, "__self__", None)
        observer = getattr(owner, "_mcp_generation_capabilities", None)
        return observer().get("available") is True if callable(observer) else True

    def validate_production_request(self, request: Mapping[str, Any]) -> None:
        validate_production_request(self.service, request)

    async def __call__(self, arguments: Mapping[str, Any]) -> Mapping[str, Any]:
        if arguments.get("source") != "production_recipe_runner":
            if not callable(self.layer_submitter):
                raise McpStructuredError(
                    ReasonCode.CAPABILITY_UNAVAILABLE, "Layer generation is unavailable."
                )
            result = self.layer_submitter(arguments)
            return await result if inspect.isawaitable(result) else result
        service = self.service
        service.safety.require_external_services()
        prepared = validate_production_request(service, arguments)
        request_id = f"recipe:{uuid.uuid4().hex}"
        parameters = prepared["parameters"]
        param_hash = hashlib.sha256(
            json.dumps(parameters, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        request = ExecutionRequest(
            prepared["gatewayFile"],
            prepared["manifest"],
            parameters,
            prepared["slots"],
            param_hash,
            "",
            request_id,
        )
        service.safety.require_external_services()
        handle = await prepared["gateway"].submit(request)
        try:
            result, output_connection = await wait_asset_only_job(service, handle)
        except asyncio.CancelledError:
            if service.safety.external_services_allowed():
                await asyncio.shield(handle.cancel())
            raise
        if result.status != "completed":
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED,
                "Production generation did not complete.",
                details={"status": result.status},
            )
        assets = [_raster_asset(service, output, handle.job_id) for output in result.output_images]
        if result.output_models:
            from .generation_model_outputs import register_model_outputs  # noqa: PLC0415

            assets.extend(
                await register_model_outputs(
                    service, handle, result.output_models, connection=output_connection
                )
            )
        if not assets:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED, "Production generation returned no output assets."
            )
        first = assets[0]
        return {
            "status": "completed",
            "assetId": first["assetId"],
            "assets": assets,
            "dimensions": first.get("dimensions"),
            "appliedToCanvas": False,
            "externalJobId": handle.job_id,
            "metadata": {
                "presetId": prepared["preset"].preset_id,
                "gatewayId": prepared["gateway"].gateway_id,
                "outputCount": len(assets),
            },
        }


def install_production_generation_adapter(service: Any) -> ProductionGenerationAdapter:
    existing = getattr(service, "_generation_submitter", None)
    if isinstance(existing, ProductionGenerationAdapter):
        return existing
    adapter = ProductionGenerationAdapter(service, existing)
    service._generation_submitter = adapter
    return adapter
