"""ComfyUI endpoint settings, dependency probes, and actual backend queue control."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any
from urllib.parse import urlsplit

from loomstroke.plugins.comfyui.server_registry import sync_registry_from_settings
from loomstroke.plugins.comfyui.session_target import active_url, confirm

from .editing_common import check_gui_thread, edit_operation, invalid
from .extension_services import get_services, require_service
from .types import McpStructuredError, McpToolSpec, ReasonCode

_TEXT = {"type": "string", "minLength": 1}
_TARGET = {"serverId": _TEXT}


def _tool(name, description, properties, required=(), *, read_only=False, external=False):
    return McpToolSpec(
        name,
        description,
        {
            "type": "object",
            "properties": properties,
            "required": list(required),
            "additionalProperties": False,
        },
        read_only=read_only,
        destructive=not read_only,
        external=external,
    )


TOOLS = (
    _tool(
        "loomstroke_list_comfyui_servers",
        "List configured credential-free ComfyUI endpoints and the confirmed session destination.",
        {},
        read_only=True,
    ),
    _tool(
        "loomstroke_configure_comfyui_servers",
        "Update existing Preferences endpoint keys and synchronize the real server registry.",
        {
            "localUrl": _TEXT,
            "externalServers": {
                "type": "array",
                "maxItems": 32,
                "items": {
                    "type": "object",
                    "properties": {"id": _TEXT, "name": _TEXT, "url": _TEXT},
                    "required": ["id", "url"],
                    "additionalProperties": False,
                },
            },
        },
    ),
    _tool(
        "loomstroke_set_default_comfyui_server",
        "Connect successfully before explicitly selecting a ComfyUI destination for this session.",
        _TARGET,
        ["serverId"],
        external=True,
    ),
    _tool(
        "loomstroke_get_comfyui_dependencies",
        "Probe available node types, models, extensions, and declared workflow dependencies.",
        {**_TARGET, "gatewayFile": {"type": "object"}},
        read_only=True,
        external=True,
    ),
    _tool(
        "loomstroke_list_comfyui_queue",
        "Read actual running/pending backend job IDs without exposing workflow payloads.",
        _TARGET,
        read_only=True,
        external=True,
    ),
    _tool(
        "loomstroke_cancel_comfyui_queue_job",
        "Cancel one actual pending or running ComfyUI backend prompt ID.",
        {**_TARGET, "promptId": _TEXT},
        ["promptId"],
        external=True,
    ),
    _tool(
        "loomstroke_stop_comfyui_repeat",
        "Stop the existing LoomStroke repeat queue when its panel is active.",
        {},
        external=True,
    ),
)


def _url(raw: Any) -> str:
    if not isinstance(raw, str):
        raise invalid("Endpoint URL must be a string.")
    parsed = urlsplit(raw)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise invalid("Endpoints require HTTP(S) URLs without credentials, query, or fragment.")
    return raw.strip().rstrip("/")


def _servers(registry: Any) -> dict:
    entries = []
    for server in registry.list_all():
        entry = asdict(server)
        # Reject unsafe legacy URLs rather than return embedded credentials.
        try:
            entry["url"] = _url(server.url)
        except McpStructuredError:
            entry["url"] = "<redacted>"
        entries.append(entry)
    target = active_url(registry)
    try:
        target = _url(target) if target else None
    except McpStructuredError:
        target = "<redacted>"
    return {
        "servers": entries,
        "defaultServerId": registry.default_server_id,
        "sessionEndpoint": target,
    }


async def _connection(service: Any, registry: Any, args: Mapping[str, Any]) -> Any:
    service.safety.require_external_services()
    server_id = args.get("serverId")
    target = (
        registry.get(server_id) if server_id else registry.get_by_url(active_url(registry) or "")
    )
    if target is None:
        target = registry.get(server_id or registry.default_server_id)
    if target is None:
        raise McpStructuredError(ReasonCode.NOT_FOUND, "ComfyUI server not found.")
    _url(target.url)
    current = active_url(registry)
    if current and current.rstrip("/") != target.url.rstrip("/"):
        raise McpStructuredError(
            ReasonCode.CONFLICT,
            "Explicitly select the server before querying another session endpoint.",
        )
    connection = await registry.get_or_create_connection(target.id)
    service.safety.require_external_services()
    return connection


def _queue_summary(payload: Any) -> dict:
    if not isinstance(payload, Mapping):
        raise McpStructuredError(ReasonCode.BACKEND_FAILED, "Invalid ComfyUI queue response.")
    result = {}
    for source, target in (("queue_running", "running"), ("queue_pending", "pending")):
        entries = payload.get(source)
        if not isinstance(entries, list):
            raise McpStructuredError(ReasonCode.BACKEND_FAILED, "Invalid ComfyUI queue response.")
        result[target] = [
            {"number": entry[0], "promptId": str(entry[1])}
            for entry in entries
            if isinstance(entry, list | tuple) and len(entry) >= 2
        ]
    return result


async def _configure(service: Any, name: str, args: dict, registry: Any) -> dict:
    settings = getattr(service.safety, "settings", getattr(service, "settings", None))
    if settings is None or (
        not isinstance(settings, Mapping) and not callable(getattr(settings, "set", None))
    ):
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE, "Writable application settings are unavailable."
        )
    values = {}
    if "localUrl" in args:
        values["comfyui.local_url"] = _url(args["localUrl"])
    if "externalServers" in args:
        servers = args["externalServers"]
        if not isinstance(servers, list) or any(not isinstance(item, dict) for item in servers):
            raise invalid("externalServers must be a list of server objects.")
        entries = []
        for entry in servers:
            if (
                set(entry) - {"id", "name", "url"}
                or not isinstance(entry.get("id"), str)
                or not entry["id"]
                or entry["id"] == "local"
            ):
                raise invalid("Invalid external server ID or fields.")
            entries.append(
                {
                    "id": entry["id"],
                    "name": entry.get("name") or entry["id"],
                    "url": _url(entry.get("url")),
                }
            )
        if len({entry["id"] for entry in entries}) != len(entries):
            raise invalid("External server IDs must be unique.")
        values["comfyui.external_servers"] = entries
        values["comfyui.external_url"] = entries[0]["url"] if entries else ""
    if not values:
        raise invalid("Provide localUrl or externalServers.")
    async with edit_operation(service, name, args, require_canvas=False):
        for key, value in values.items():
            if isinstance(settings, dict):
                settings[key] = value
            else:
                settings.set(key, value)
        sync_registry_from_settings(settings, registry)
        return _servers(registry)


async def _stop_repeat(service: Any, name: str, args: dict, registry: Any) -> dict:
    providers = get_services(service)
    author = providers.get("comfyui_native_author")
    if author is not None:
        from .native_comfy_queue import stop_native_repeat  # noqa: PLC0415

        async with edit_operation(service, name, args, require_canvas=False):
            return await stop_native_repeat(author)
    panel = providers.get("comfyui_panel")
    stop = getattr(panel, "_stop_repeat_queue", None)
    if not callable(stop):
        raise McpStructuredError(
            ReasonCode.CAPABILITY_UNAVAILABLE, "No LoomStroke repeat queue panel is active."
        )
    async with edit_operation(service, name, args, require_canvas=False):
        stop()
        return {"repeatStopped": True}


async def _select(service: Any, name: str, args: dict, registry: Any) -> dict:
    service.safety.require_external_services()
    server = registry.get(args.get("serverId"))
    if server is None:
        raise McpStructuredError(ReasonCode.NOT_FOUND, "ComfyUI server not found.")
    _url(server.url)
    await service.safety.require_confirmation(name, {"serverId": server.id})
    service.safety.require_external_services()
    # Explicit probing must not inherit the previously confirmed destination.
    connection = registry.get_connection(server.url)
    if connection is None:
        connection = registry._build_connection(server.url)
        registry._connections[server.url.rstrip("/")] = connection
    if not connection.is_connected() and not await connection.connect():
        raise McpStructuredError(ReasonCode.BACKEND_FAILED, "ComfyUI server connection failed.")
    service.safety.require_external_services()
    confirm(registry, server.url, explicit=True)
    registry.set_default(server.id)
    return {**_servers(registry), "connected": True, "scope": "session"}


async def _dependencies(service: Any, args: dict, connection: Any) -> dict:
    info = await connection.get_object_info()
    service.safety.require_external_services()
    models = await connection.get_models()
    service.safety.require_external_services()
    extensions = await connection.get_extensions()
    nodes = sorted(info or {})
    dependencies = (args.get("gatewayFile") or {}).get("dependencies") or {}
    missing = [
        {"kind": "custom_node", "name": str(node)}
        for node in dependencies.get("custom_nodes", [])
        if str(node) not in nodes
    ]
    return {
        "nodeTypes": nodes,
        "models": models,
        "extensions": extensions,
        "ok": not missing,
        "missing": missing,
    }


async def _queue(service: Any, name: str, args: dict, connection: Any) -> dict:
    service.safety.require_external_services()
    summary = _queue_summary(
        await connection.async_manager.get_json(f"{connection.base_url}/queue")
    )
    if name == "loomstroke_list_comfyui_queue":
        return summary
    if name != "loomstroke_cancel_comfyui_queue_job":
        raise invalid("Unknown ComfyUI management operation.")
    prompt_id = args.get("promptId")
    pending = any(entry["promptId"] == prompt_id for entry in summary["pending"])
    running = any(entry["promptId"] == prompt_id for entry in summary["running"])
    if not pending and not running:
        raise McpStructuredError(ReasonCode.NOT_FOUND, "ComfyUI queue job not found.")
    await service.safety.require_confirmation(name, {"promptId": prompt_id})
    service.safety.require_external_services()
    # Approval can yield while the selected job finishes and another one starts.
    summary = _queue_summary(
        await connection.async_manager.get_json(f"{connection.base_url}/queue")
    )
    service.safety.require_external_services()
    pending = any(entry["promptId"] == prompt_id for entry in summary["pending"])
    running = any(entry["promptId"] == prompt_id for entry in summary["running"])
    if not pending and not running:
        raise McpStructuredError(ReasonCode.NOT_FOUND, "ComfyUI queue job is no longer active.")
    if pending:
        await connection.async_manager.post(
            f"{connection.base_url}/queue", json={"delete": [prompt_id]}
        )
    else:
        await connection.interrupt()
    return {
        "promptId": prompt_id,
        "cancelRequested": True,
        "queue": "pending" if pending else "running",
    }


async def dispatch(service: Any, name: str, arguments: Mapping[str, Any]) -> dict[str, Any]:
    check_gui_thread()
    args = dict(arguments)
    registry = require_service(service, "comfyui_registry")
    if name == "loomstroke_list_comfyui_servers":
        return _servers(registry)
    local = {
        "loomstroke_configure_comfyui_servers": _configure,
        "loomstroke_stop_comfyui_repeat": _stop_repeat,
        "loomstroke_set_default_comfyui_server": _select,
    }
    if name in local:
        return await local[name](service, name, args, registry)
    connection = await _connection(service, registry, args)
    if name == "loomstroke_get_comfyui_dependencies":
        return await _dependencies(service, args, connection)
    return await _queue(service, name, args, connection)
