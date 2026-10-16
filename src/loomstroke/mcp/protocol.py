"""JSON-RPC 2.0 router for LoomStroke MCP methods."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

from . import prompts, resources, tools
from .types import (
    JSON_RPC_VERSION,
    PROTOCOL_VERSION,
    SERVER_NAME,
    SERVER_VERSION,
    McpStructuredError,
    ReasonCode,
)


class McpProtocolRouter:
    """Small JSON-RPC router that is independent from stdio/HTTP transports."""

    def __init__(self, service: Any) -> None:
        self.service = service
        self.initialized = False
        self._initialize_received = False

    async def handle_json(self, payload: str | bytes | Mapping[str, Any] | list[Any]) -> Any:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError as exc:
                return _error_response(
                    None,
                    McpStructuredError(
                        ReasonCode.INVALID_ARGUMENT,
                        "Invalid JSON-RPC payload.",
                        code=-32700,
                        details={"error": str(exc)},
                    ),
                )
        if isinstance(payload, list):
            responses = [await self._handle_one(item) for item in payload]
            return [item for item in responses if item is not None]
        return await self._handle_one(payload)

    async def _handle_one(self, request: Any) -> dict[str, Any] | None:  # noqa: PLR0911
        if not isinstance(request, Mapping):
            return _error_response(
                None,
                McpStructuredError(
                    ReasonCode.INVALID_ARGUMENT,
                    "JSON-RPC request must be an object.",
                    code=-32600,
                ),
            )
        request_id = request.get("id")
        is_notification = "id" not in request
        if request.get("jsonrpc") != JSON_RPC_VERSION:
            return _maybe_error(
                request_id,
                is_notification,
                McpStructuredError(
                    ReasonCode.INVALID_ARGUMENT,
                    "Unsupported JSON-RPC version.",
                    code=-32600,
                ),
            )
        method = request.get("method")
        if not isinstance(method, str) or not method:
            return _maybe_error(
                request_id,
                is_notification,
                McpStructuredError(
                    ReasonCode.INVALID_ARGUMENT,
                    "JSON-RPC method is required.",
                    code=-32600,
                ),
            )
        params = request.get("params", {})
        try:
            if method == "initialize" and is_notification:
                raise _invalid_params("initialize must be a request.")
            if method in {"notifications/initialized", "initialized"} and not is_notification:
                raise _invalid_params("initialized must be a notification.")
            result = await self._dispatch(method, params)
        except McpStructuredError as exc:
            return _maybe_error(request_id, is_notification, exc)
        except Exception as exc:
            return _maybe_error(
                request_id,
                is_notification,
                McpStructuredError(
                    ReasonCode.BACKEND_FAILED,
                    "MCP method failed.",
                    details={"method": method, "error": exc.__class__.__name__},
                ),
            )
        if is_notification:
            return None
        return {"jsonrpc": JSON_RPC_VERSION, "id": request_id, "result": result}

    async def _dispatch(self, method: str, params: Any) -> Any:  # noqa: PLR0911, PLR0912
        if method == "initialize":
            if not isinstance(params, Mapping):
                raise _invalid_params("initialize params must be an object.")
            if self._initialize_received:
                raise _invalid_params("This connection has already received initialize.")
            # A client requesting another revision must receive one we support.
            # Empty params remain accepted for existing LoomStroke clients.
            self._initialize_received = True
            return {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {
                    "resources": {"subscribe": False, "listChanged": False},
                    "tools": {"listChanged": False},
                    "prompts": {"listChanged": False},
                },
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    "LoomStroke MCP exposes inspection, preview, export, batch render, "
                    "and guarded generation facade methods."
                ),
            }
        if method in {"notifications/initialized", "initialized"}:
            if not self._initialize_received:
                raise _invalid_params("initialize must precede initialized.")
            self.initialized = True
            return {}
        if method == "ping":
            return {}
        if not self.initialized:
            raise McpStructuredError(
                ReasonCode.CONFLICT,
                "Complete initialize and notifications/initialized before calling methods.",
                details={"policy": "initialization_required"},
            )
        if method == "resources/list":
            return resources.list_resources(self.service)
        if method == "resources/read":
            if not isinstance(params, Mapping):
                raise _invalid_params("resources/read params must be an object.")
            uri = params.get("uri")
            if not isinstance(uri, str):
                raise _invalid_params("'uri' is required.")
            return resources.read_resource(self.service, uri)
        if method == "tools/list":
            return tools.list_tools()
        if method == "tools/call":
            if not isinstance(params, Mapping):
                raise _invalid_params("tools/call params must be an object.")
            name = params.get("name")
            if not isinstance(name, str):
                raise _invalid_params("'name' is required.")
            arguments = params.get("arguments")
            if arguments is not None and not isinstance(arguments, Mapping):
                raise _invalid_params("'arguments' must be an object.")
            return await tools.call_tool(self.service, name, arguments)
        if method == "prompts/list":
            return prompts.list_prompts()
        if method == "prompts/get":
            if not isinstance(params, Mapping):
                raise _invalid_params("prompts/get params must be an object.")
            name = params.get("name")
            if not isinstance(name, str):
                raise _invalid_params("'name' is required.")
            arguments = params.get("arguments")
            if arguments is not None and not isinstance(arguments, Mapping):
                raise _invalid_params("'arguments' must be an object.")
            return prompts.get_prompt(name, arguments)
        raise McpStructuredError(
            ReasonCode.NOT_FOUND,
            "Method not found.",
            code=-32601,
            details={"method": method},
        )


def _invalid_params(message: str) -> McpStructuredError:
    return McpStructuredError(
        ReasonCode.INVALID_ARGUMENT,
        message,
        code=-32602,
    )


def _maybe_error(
    request_id: Any,
    is_notification: bool,
    error: McpStructuredError,
) -> dict[str, Any] | None:
    if is_notification:
        return None
    return _error_response(request_id, error)


def _error_response(request_id: Any, error: McpStructuredError) -> dict[str, Any]:
    return {
        "jsonrpc": JSON_RPC_VERSION,
        "id": request_id,
        "error": error.to_error(),
    }
