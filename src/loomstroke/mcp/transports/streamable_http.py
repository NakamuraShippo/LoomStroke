"""Local Streamable HTTP transport for MCP JSON-RPC requests."""

from __future__ import annotations

import ipaddress
import json
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from aiohttp import web

from ..protocol import McpProtocolRouter
from ..safety import McpSafetyPolicy, validate_local_bind_host, validate_session_token
from ..types import JSON_RPC_VERSION, PROTOCOL_VERSION, McpStructuredError, ReasonCode

_MAX_SESSIONS = 128


@dataclass(frozen=True, slots=True)
class StreamableHttpState:
    host: str
    port: int
    path: str
    session_token: str
    active_connections: int


class StreamableHttpTransport:
    """Explicit opt-in localhost HTTP transport.

    Construction is inert. A listener is opened only after ``start()``.
    """

    def __init__(
        self,
        router: Any,
        *,
        safety: McpSafetyPolicy,
        host: str = "127.0.0.1",
        port: int = 0,
        path: str = "/mcp",
        allowed_origins: Sequence[str] | None = None,
        active_connection_callback: Callable[[StreamableHttpState], None] | None = None,
    ) -> None:
        self.router = router
        self.safety = safety
        self._allowed_origins_explicit = allowed_origins is not None
        if allowed_origins is not None:
            self.safety.allowed_origins = tuple(allowed_origins)
        self.host = validate_local_bind_host(host)
        self.port = int(port)
        self.path = path if path.startswith("/") else f"/{path}"
        self.active_connection_callback = active_connection_callback
        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None
        self._active_connections = 0
        self._sessions: dict[str, McpProtocolRouter] = {}

    @property
    def state(self) -> StreamableHttpState:
        return StreamableHttpState(
            host=self.host,
            port=self.port,
            path=self.path,
            session_token=self.safety.session_token,
            active_connections=self._active_connections,
        )

    async def start(self) -> StreamableHttpState:
        if self._runner is not None:
            return self.state
        app = web.Application()
        app.router.add_post(self.path, self._handle_post)
        app.router.add_get(self.path, self._handle_get)
        app.router.add_delete(self.path, self._handle_delete)
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        self._runner = runner
        self._site = site
        sockets = getattr(site, "_server", None)
        server_sockets = getattr(sockets, "sockets", None)
        if server_sockets:
            self.port = int(server_sockets[0].getsockname()[1])
        if not self._allowed_origins_explicit:
            self.safety.allowed_origins = (
                f"http://127.0.0.1:{self.port}",
                f"http://localhost:{self.port}",
            )
        self._notify_active_state()
        return self.state

    async def stop(self) -> None:
        runner = self._runner
        if runner is None:
            return
        self._runner = None
        self._site = None
        try:
            await runner.cleanup()
        except AttributeError as exc:
            # aiohttp AppRunner.cleanup can trip over a half-torn-down
            # private server during rapid restart/shutdown. The listener is
            # already detached from this transport; keep stop idempotent.
            if "pre_shutdown" not in str(exc):
                raise
        finally:
            self._sessions.clear()
            self._active_connections = 0
            self._notify_active_state()

    async def _handle_get(self, request: web.Request) -> web.Response:
        try:
            self._validate_request(request)
            self._validate_protocol_version(request)
        except McpStructuredError as exc:
            return _structured_error_response(exc)
        return web.Response(status=405, headers={"Allow": "POST, DELETE"})

    async def _handle_delete(self, request: web.Request) -> web.Response:
        try:
            self._validate_request(request)
            self._validate_protocol_version(request)
            session_id = request.headers.get("Mcp-Session-Id", "")
            self._session_router(session_id)
            del self._sessions[session_id]
            return web.Response(status=204)
        except McpStructuredError as exc:
            return _structured_error_response(exc)

    async def _handle_post(self, request: web.Request) -> web.Response:
        try:
            self._validate_request(request)
            self._validate_protocol_version(request)
            try:
                payload = await request.json()
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise McpStructuredError(
                    ReasonCode.INVALID_ARGUMENT,
                    "Invalid JSON-RPC payload.",
                    code=-32700,
                ) from exc
            if not isinstance(payload, dict):
                raise McpStructuredError(
                    ReasonCode.INVALID_ARGUMENT,
                    "HTTP requires a single JSON-RPC object.",
                    code=-32600,
                )
            session_id = request.headers.get("Mcp-Session-Id", "")
            creating = payload.get("method") == "initialize" and not session_id
            if creating:
                if len(self._sessions) >= _MAX_SESSIONS:
                    return web.Response(status=503, text="MCP session limit reached.")
                router = McpProtocolRouter(self.router.service)
            elif payload.get("method") == "ping" and not session_id:
                router = self.router
            else:
                router = self._session_router(session_id)
            self._active_connections += 1
            self._notify_active_state()
            try:
                response = await router.handle_json(payload)
            finally:
                self._active_connections = max(0, self._active_connections - 1)
                self._notify_active_state()
            if response is None:
                return web.Response(status=202)
            headers = {"MCP-Protocol-Version": PROTOCOL_VERSION}
            if creating and "result" in response:
                session_id = secrets.token_urlsafe(32)
                self._sessions[session_id] = router
                headers["Mcp-Session-Id"] = session_id
            return web.json_response(
                response,
                headers=headers,
            )
        except McpStructuredError as exc:
            return _structured_error_response(exc)

    def _session_router(self, session_id: str) -> McpProtocolRouter:
        if not session_id:
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT, "Mcp-Session-Id is required after initialize."
            )
        router = self._sessions.get(session_id)
        if router is None:
            raise McpStructuredError(ReasonCode.NOT_FOUND, "MCP session expired or unknown.")
        return router

    @staticmethod
    def _validate_protocol_version(request: web.Request) -> None:
        version = request.headers.get("MCP-Protocol-Version")
        if version is not None and version != PROTOCOL_VERSION:
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "Unsupported MCP-Protocol-Version.",
                details={"supported": [PROTOCOL_VERSION]},
            )

    def _validate_request(self, request: web.Request) -> None:
        auth = request.headers.get("Authorization", "")
        token = request.headers.get("Mcp-Session-Token")
        if auth.startswith("Bearer "):
            token = auth.removeprefix("Bearer ").strip()
        if request.headers.get("Origin") is None and _is_loopback_remote(request.remote):
            validate_session_token(token, self.safety.session_token)
            return
        self.safety.validate_http_request(
            origin=request.headers.get("Origin"),
            token=token,
        )

    def _notify_active_state(self) -> None:
        if self.active_connection_callback is not None:
            self.active_connection_callback(self.state)


def _structured_error_response(exc: McpStructuredError) -> web.Response:
    return web.json_response(
        {
            "jsonrpc": JSON_RPC_VERSION,
            "id": None,
            "error": exc.to_error(),
        },
        status={"permission_denied": 403, "not_found": 404}.get(exc.reason_code, 400),
    )


def _is_loopback_remote(remote: str | None) -> bool:
    if not remote:
        return False
    if remote == "localhost":
        return True
    try:
        address = ipaddress.ip_address(remote)
    except ValueError:
        return False
    mapped = getattr(address, "ipv4_mapped", None)
    if mapped is not None:
        return bool(mapped.is_loopback)
    return bool(address.is_loopback)
