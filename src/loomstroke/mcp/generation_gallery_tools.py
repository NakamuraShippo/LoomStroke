"""Reuse and regenerate the application's existing Gallery records."""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Mapping
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.rendering_3d.projection import qimage_to_rgba_array

from .editing_common import check_gui_thread, edit_operation, invalid
from .extension_services import get_services, require_service
from .layer_edit_command import LayerEditCommand
from .layer_operations import _editable, _layer
from .types import AssetKind, AssetRecord, McpStructuredError, McpToolSpec, ReasonCode

_TEXT = {"type": "string", "minLength": 1}
_JOB = {"jobId": _TEXT}


def _tool(name, description, properties, *, external=False):
    return McpToolSpec(
        name,
        description,
        {
            "type": "object",
            "properties": properties,
            "required": ["jobId"],
            "additionalProperties": False,
        },
        destructive=True,
        external=external,
    )


TOOLS = (
    _tool(
        "loomstroke_restore_generation_to_panel",
        "Restore a Gallery record to its existing provider panel.",
        _JOB,
    ),
    _tool(
        "loomstroke_regenerate_gallery_record",
        "Regenerate a Gallery record through its registered generation provider.",
        _JOB,
        external=True,
    ),
    _tool(
        "loomstroke_reuse_gallery_asset",
        "Register a Gallery image as an asset and optionally transfer to an explicit or new layer.",
        {
            **_JOB,
            "layerId": _TEXT,
            "applyToCanvas": {"type": "boolean", "default": False},
            "expectedRevision": _TEXT,
        },
    ),
)


def _record(service: Any, job_id: Any) -> tuple[Any, Any]:
    if not isinstance(job_id, str) or not job_id:
        raise invalid("'jobId' must be a non-empty string.")
    store = require_service(service, "generation_history")
    record = store.get_record(job_id)
    if record is None:
        raise McpStructuredError(ReasonCode.NOT_FOUND, "Gallery record not found.")
    return store, record


async def _reuse_asset(service: Any, name: str, args: dict, store: Any, record: Any) -> dict:
    if not record.image_path:
        raise McpStructuredError(ReasonCode.NOT_FOUND, "Gallery record has no saved image.")
    path = store.resolve_path(record.image_path).resolve()
    if not path.is_relative_to(store.base_path.resolve()) or not path.is_file():
        raise McpStructuredError(ReasonCode.PERMISSION_DENIED, "Invalid Gallery image path.")
    if path.stat().st_size > 64 * 1024 * 1024:
        raise invalid("Gallery image exceeds 64 MiB.")
    data = path.read_bytes()
    image = QImage.fromData(data)
    if image.isNull() or max(image.width(), image.height()) > 16384:
        raise invalid("Gallery image is invalid or too large.")
    apply = args.get("applyToCanvas", False)
    if not isinstance(apply, bool):
        raise invalid("'applyToCanvas' must be a boolean.")
    layer_id = args.get("layerId")
    if layer_id and not apply:
        raise invalid("'layerId' requires applyToCanvas=true.")
    async with edit_operation(service, name, args, require_canvas=apply) as canvas:
        applied = False
        if apply and layer_id:
            layer = _layer(canvas, layer_id)
            _editable(canvas, layer)
            if layer.layer_type != "raster":
                raise invalid("Gallery transfer target must be a raster layer.")
            applied = bool(
                canvas.apply_image(
                    qimage_to_rgba_array(image),
                    metadata={"target_layer_id": layer.id, "source": "mcp_gallery"},
                )
            )
        elif apply:
            controller = getattr(service, "_mcp_headless_document_controller", None)
            document = (
                get_services(service).get("document_service")
                or getattr(getattr(service, "host_api", None), "document", None)
                or getattr(controller, "_document", None)
            )
            if document is None:
                raise McpStructuredError(
                    ReasonCode.CAPABILITY_UNAVAILABLE, "Document image import is unavailable."
                )

            def import_image(_canvas):
                imported = document.import_layer(str(path), as_new=True)
                if not imported:
                    raise McpStructuredError(
                        ReasonCode.BACKEND_FAILED, "Gallery image import failed."
                    )
                return {"layerId": imported}

            command = LayerEditCommand([], import_image)
            applied = bool(canvas.submit(command))
            layer_id = command.result.get("layerId")
        asset_id = f"gallery:{uuid.uuid4().hex}"
        asset = AssetRecord(
            asset_id,
            AssetKind.RASTER.value,
            "gallery",
            mime_type="image/png",
            dimensions={"width": image.width(), "height": image.height()},
            related_layer_id=layer_id,
            metadata={"jobId": record.job_id, "serviceId": record.service_id},
        )
        service._asset_records[asset_id] = asset
        service._asset_payloads[asset_id] = data
        return {"asset": asset.to_schema(), "layerId": layer_id, "appliedToCanvas": applied}


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    check_gui_thread()
    args = dict(arguments)
    store, record = _record(service, args.get("jobId"))
    if name == "loomstroke_reuse_gallery_asset":
        return await _reuse_asset(service, name, args, store, record)
    provider = store.get_provider(record.service_id)
    if provider is None:
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE, "The Gallery generation provider is unavailable."
        )
    if name == "loomstroke_regenerate_gallery_record":
        service.safety.require_external_services()
        await service.safety.require_confirmation(
            "loomstroke_submit_generation",
            {"sourceJobId": record.job_id, "gateway": record.service_id},
        )
        service.safety.require_external_services()
        job_id = await provider.regenerate(record)
        if not job_id:
            raise McpStructuredError(
                ReasonCode.BACKEND_FAILED, "Gallery regeneration produced no job."
            )
        return {"jobId": job_id, "sourceJobId": record.job_id, "status": "submitted"}
    if name != "loomstroke_restore_generation_to_panel":
        raise invalid("Unknown Gallery operation.")
    async with edit_operation(service, name, args, require_canvas=False):
        restored = provider.restore_to_panel(record)
        if inspect.isawaitable(restored):
            restored = await restored
        return {"jobId": record.job_id, "restored": restored is True}
