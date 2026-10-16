"""stdio proxy that forwards MCP JSON-RPC to a live LoomStroke HTTP endpoint."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import aiohttp

from .transports.stdio import StdioTransport
from .types import JSON_RPC_VERSION, PROTOCOL_VERSION, McpStructuredError, ReasonCode

DEFAULT_TOKEN_ENV = "LOOMSTROKE_MCP_SESSION_TOKEN"
logger = logging.getLogger(__name__)


class LiveHttpProxyRouter:
    """JSON-RPC router-shaped proxy for Claude Desktop stdio MCP clients."""

    def __init__(
        self,
        *,
        url: str,
        session_token: str,
        timeout_seconds: float = 30.0,
        protocol_version: str = PROTOCOL_VERSION,
    ) -> None:
        self.url = str(url)
        self.session_token = str(session_token)
        self.timeout_seconds = float(timeout_seconds)
        self.protocol_version = str(protocol_version)
        self._session_id: str | None = None

    async def handle_json(self, payload: str | bytes | Mapping[str, Any] | list[Any]) -> Any:
        try:
            request_payload = _normalize_payload(payload)
        except json.JSONDecodeError as exc:
            return _error_for_payload(
                None,
                McpStructuredError(
                    ReasonCode.INVALID_ARGUMENT,
                    "Invalid JSON-RPC payload.",
                    code=-32700,
                    details={"error": exc.__class__.__name__},
                ),
            )

        try:
            response_payload = await self._post_json(request_payload)
        except TimeoutError:
            return _error_for_payload(
                request_payload,
                _transport_error(
                    "Timed out while connecting to the LoomStroke live MCP endpoint.",
                    url=self.url,
                    error="TimeoutError",
                ),
            )
        except aiohttp.ClientError as exc:
            return _error_for_payload(
                request_payload,
                _transport_error(
                    "Could not connect to the LoomStroke live MCP endpoint.",
                    url=self.url,
                    error=exc.__class__.__name__,
                ),
            )
        except _ProxyHttpError as exc:
            return _error_for_payload(
                request_payload,
                McpStructuredError(
                    exc.reason_code,
                    exc.message,
                    details=exc.details,
                ),
            )

        return response_payload

    async def _post_json(self, payload: Any) -> Any:
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "MCP-Protocol-Version": self.protocol_version,
            "Authorization": f"Bearer {self.session_token}",
        }
        initializing = isinstance(payload, Mapping) and payload.get("method") == "initialize"
        if self._session_id and not initializing:
            headers["Mcp-Session-Id"] = self._session_id
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.post(
                self.url,
                json=payload,
                headers=headers,
            ) as response,
        ):
            text = await response.text()
            if not text.strip():
                if response.status == 202:
                    return None
                if 200 <= response.status < 300:
                    return None
                raise _ProxyHttpError(
                    "LoomStroke live MCP endpoint returned an empty error response.",
                    details={
                        "status": response.status,
                        "url": _redact_url(self.url),
                    },
                )
            try:
                body = json.loads(text)
            except json.JSONDecodeError as exc:
                raise _ProxyHttpError(
                    "LoomStroke live MCP endpoint returned non-JSON content.",
                    details={
                        "status": response.status,
                        "url": _redact_url(self.url),
                        "contentType": response.headers.get("Content-Type", ""),
                        "error": exc.__class__.__name__,
                    },
                ) from exc
            if 200 <= response.status < 300:
                if initializing and isinstance(body, Mapping) and "result" in body:
                    self._session_id = response.headers.get("Mcp-Session-Id")
                return body
            if response.status == 404:
                self._session_id = None
            if _looks_like_json_rpc(body):
                if isinstance(body, dict) and isinstance(payload, Mapping) and "error" in body:
                    if "id" not in payload:
                        return None
                    body = {**body, "id": payload["id"]}
                return body
            raise _ProxyHttpError(
                "LoomStroke live MCP endpoint rejected the proxied request.",
                details={
                    "status": response.status,
                    "url": _redact_url(self.url),
                },
            )

    async def close(self) -> None:
        """Release the live HTTP session when the stdio client exits."""
        session_id, self._session_id = self._session_id, None
        if session_id is None:
            return
        try:
            async with (
                aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=self.timeout_seconds)
                ) as session,
                session.delete(
                    self.url,
                    headers={
                        "Authorization": f"Bearer {self.session_token}",
                        "Mcp-Session-Id": session_id,
                        "MCP-Protocol-Version": self.protocol_version,
                    },
                ) as response,
            ):
                await response.read()
        except (aiohttp.ClientError, TimeoutError) as exc:
            logger.debug("MCP session cleanup unavailable: %s", type(exc).__name__)


class _ProxyHttpError(Exception):
    def __init__(
        self,
        message: str,
        *,
        reason_code: ReasonCode | str = ReasonCode.CAPABILITY_UNAVAILABLE,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.reason_code = reason_code
        self.details = dict(details or {})


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Expose a live LoomStroke HTTP MCP endpoint as a stdio MCP server. "
            "Use this for clients that support stdio MCP but reject HTTP MCP entries."
        )
    )
    parser.add_argument(
        "--url",
        required=True,
        help="Live LoomStroke MCP endpoint URL, for example http://127.0.0.1:37645/mcp.",
    )
    parser.add_argument(
        "--session-token",
        default="",
        help="Live MCP session token. Prefer --token-env to avoid command-line exposure.",
    )
    parser.add_argument(
        "--token-env",
        default=DEFAULT_TOKEN_ENV,
        help=(
            "Environment variable containing the live MCP session token. "
            f"Default: {DEFAULT_TOKEN_ENV}."
        ),
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP request timeout in seconds.",
    )
    return parser


async def async_main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    session_token = _resolve_session_token(args, parser)
    router = LiveHttpProxyRouter(
        url=args.url,
        session_token=session_token,
        timeout_seconds=args.timeout,
    )
    try:
        await StdioTransport(router).serve()
    finally:
        await router.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


def _resolve_session_token(args: argparse.Namespace, parser: argparse.ArgumentParser) -> str:
    if args.session_token:
        return str(args.session_token)
    token_env = str(args.token_env or "")
    if token_env:
        token = os.environ.get(token_env, "")
        if token:
            return token
    parser.error(
        "A live MCP session token is required. Pass --session-token or set "
        f"{DEFAULT_TOKEN_ENV} and keep --token-env at its default."
    )
    raise AssertionError("argparse.error should exit")


def _normalize_payload(payload: str | bytes | Mapping[str, Any] | list[Any]) -> Any:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8")
    if isinstance(payload, str):
        return json.loads(payload)
    return payload


def _transport_error(message: str, *, url: str, error: str) -> McpStructuredError:
    return McpStructuredError(
        ReasonCode.CAPABILITY_UNAVAILABLE,
        message,
        details={
            "url": _redact_url(url),
            "error": error,
            "hint": "Start LoomStroke live MCP in Preferences and verify the endpoint URL.",
        },
    )


def _error_for_payload(payload: Any, error: McpStructuredError) -> Any:
    if isinstance(payload, list):
        responses = [
            _json_rpc_error(item.get("id"), error)
            for item in payload
            if isinstance(item, Mapping) and "id" in item
        ]
        return responses or None
    if isinstance(payload, Mapping):
        if "id" not in payload:
            return None
        return _json_rpc_error(payload.get("id"), error)
    return _json_rpc_error(None, error)


def _json_rpc_error(request_id: Any, error: McpStructuredError) -> dict[str, Any]:
    return {
        "jsonrpc": JSON_RPC_VERSION,
        "id": request_id,
        "error": error.to_error(),
    }


def _looks_like_json_rpc(value: Any) -> bool:
    if isinstance(value, Mapping):
        return value.get("jsonrpc") == JSON_RPC_VERSION
    if isinstance(value, list):
        return all(isinstance(item, Mapping) for item in value)
    return False


def _redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return "<invalid-url>"
    try:
        port = parts.port
    except ValueError:
        port = None
    host = parts.hostname or ""
    if not host and parts.netloc:
        host = parts.netloc.rsplit("@", 1)[-1]
    if port is not None:
        host = f"{host}:{port}"
    query = "<redacted>" if parts.query else ""
    return urlunsplit((parts.scheme, host, parts.path, query, ""))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
