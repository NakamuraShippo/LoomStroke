"""Read-only availability diagnostics for the MCP painting surface."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from PySide6.QtCore import QCoreApplication
from PySide6.QtGui import QGuiApplication

from . import editing_tools
from .extension_readiness import extension_tool_readiness
from .extension_services import get_services
from .types import McpStructuredError, ReasonCode

logger = logging.getLogger(__name__)


def _observe(provider: Any) -> dict[str, Any]:
    try:
        state = provider()
    except Exception as exc:
        logger.warning("MCP document state observer failed (%s).", type(exc).__name__)
        return {
            "available": False,
            "present": False,
            "blockedReasonCode": "document_state_observer_failed",
        }
    if not isinstance(state, Mapping):
        return {"available": False, "present": False, "blockedReasonCode": "document_state_invalid"}
    return {
        **state,
        "available": state.get("available") is True,
        "present": state.get("available") is True and state.get("present") is True,
    }


DOCUMENT_NAMES = {
    "loomstroke_new_document",
    "loomstroke_open_document",
    "loomstroke_save_document",
    "loomstroke_close_document",
}
BRUSH_LIBRARY_NAMES = {
    "loomstroke_list_brushes",
    "loomstroke_get_brush_settings",
    "loomstroke_set_brush_settings",
    "loomstroke_apply_brush_preset",
}


def document_state(service: Any) -> dict[str, Any]:
    """Observe existing state without attaching services or creating a canvas."""
    provider = getattr(service, "document_state_provider", None)
    if callable(provider):
        return _observe(provider)
    handler = getattr(service, "document_operation_handler", None)
    owner_state = getattr(getattr(handler, "__self__", None), "state", None)
    if callable(owner_state):
        return _observe(owner_state)
    controller = getattr(service, "_mcp_headless_document_controller", None)
    if controller is not None and controller.canvas is service.canvas_service:
        return controller.state()
    headless = (
        not str(getattr(service, "mode", "")).startswith("live")
        and getattr(service, "_snapshot_provider", None) is None
    )
    qt_available = QCoreApplication.instance() is None or isinstance(
        QCoreApplication.instance(), QGuiApplication
    )
    return {
        "available": callable(handler) or (headless and qt_available),
        "present": headless and not callable(handler) and service.canvas_service is not None,
        "dirty": None,
        "path": None,
        "saveInFlight": False,
    }


def known_document_closed(service: Any) -> bool:
    """Respect authoritative lifecycle state while preserving old injected services."""
    handler = getattr(service, "document_operation_handler", None)
    observed = (
        callable(getattr(service, "document_state_provider", None))
        or callable(getattr(getattr(handler, "__self__", None), "state", None))
        or getattr(service, "_mcp_headless_document_controller", None) is not None
    )
    return observed and document_state(service).get("present") is not True


def guard_existing_edit(service: Any, name: str, arguments: Mapping[str, Any]) -> None:
    """Prevent earlier canvas tools from bypassing the new close boundary."""
    affected = name in {
        "loomstroke_apply_canvas_changeset",
        "loomstroke_apply_ai_effect_preset",
        "loomstroke_set_ai_effect_layer_state",
        "loomstroke_sam3_apply_candidate_as_selection",
        "loomstroke_sam3_apply_candidate_as_layer_mask",
    }
    if name == "loomstroke_submit_generation":
        affected = arguments.get("applyToCanvas", True) is not False
    if affected and known_document_closed(service):
        raise McpStructuredError(ReasonCode.CAPABILITY_UNAVAILABLE, "No editable document is open.")


def build_editing_readiness(service: Any) -> dict[str, Any]:
    state = document_state(service)
    canvas_ready = service.canvas_service is not None and state.get("present") is True
    provider = getattr(service, "tool_manager_provider", None)
    brush_ready = callable(provider) and provider() is not None
    tools = []
    extensions = extension_tool_readiness(
        service,
        (spec for spec in editing_tools.TOOLS if spec.name not in editing_tools.P0_TOOL_NAMES),
        services=get_services(service),
    )
    for spec in editing_tools.TOOLS:
        if spec.name in extensions:
            tools.append(extensions[spec.name])
            continue
        blocks: list[dict[str, str]] = []
        if spec.name in DOCUMENT_NAMES:
            if not state.get("available"):
                blocks.append(
                    {
                        "code": "document_workflow_unavailable",
                        "message": "The document workflow is not attached.",
                    }
                )
            if state.get("saveInFlight"):
                blocks.append(
                    {"code": "save_in_progress", "message": "A document save is in progress."}
                )
            if (
                spec.name in {"loomstroke_open_document", "loomstroke_save_document"}
                and not service.safety.roots
            ):
                blocks.append(
                    {"code": "roots_required", "message": "Configure MCP roots for document files."}
                )
            if spec.name == "loomstroke_save_document" and not canvas_ready:
                blocks.append(
                    {"code": "document_unavailable", "message": "No editable document is open."}
                )
        elif spec.name in BRUSH_LIBRARY_NAMES:
            if not brush_ready:
                blocks.append(
                    {
                        "code": "brush_library_unavailable",
                        "message": "The live brush library is not connected.",
                    }
                )
        elif not canvas_ready:
            blocks.append(
                {"code": "document_unavailable", "message": "No editable document is open."}
            )
        if (
            not spec.read_only
            and service.safety.requires_confirmation(spec.name)
            and service.safety.confirmation_callback is None
        ):
            blocks.append(
                {
                    "code": "confirmation_unavailable",
                    "message": "A confirmation callback is required.",
                }
            )
        tools.append(
            {
                "tool": spec.name,
                "ready": not blocks,
                "state": "blocked" if blocks else "ready",
                "readOnly": spec.read_only,
                "fileWrite": spec.name == "loomstroke_save_document",
                "externalService": False,
                "currentDocumentAffects": not spec.read_only,
                "blockedReasons": blocks,
                "notes": [],
            }
        )
    return {
        "schemaVersion": "mcp.editing_readiness.v1",
        "document": state,
        "canvasEditingAvailable": canvas_ready,
        "documentWorkflowAvailable": bool(state.get("available")),
        "brushLibraryAvailable": bool(brush_ready),
        "tools": tools,
        "limits": {
            "strokePoints": 2048,
            "strokeRegionPixels": 4194304,
            "gradientPixels": 16777216,
            "bucketCanvasPixels": 1048576,
            "bucketReferencePixels": 1048576,
            "fillConnectivity": [8],
            "scaledPatternPixels": 16777216,
            "strokeTools": ["basic_pen", "basic_eraser"],
            "patternRotation": False,
            "fillReferenceModes": ["layer"],
            "customTipStroke": True,
            "stampBrush": {
                "points": 2048,
                "stamps": 8192,
                "regionPixels": 4194304,
                "tipPixels": 4194304,
            },
            "advancedFill": {
                "tool": "loomstroke_fill_advanced",
                "canvasPixels": 16777216,
                "referencePixels": 16777216,
                "connectivity": [4, 8],
                "referenceModes": ["layer", "merged"],
                "patternRotation": True,
            },
        },
    }
