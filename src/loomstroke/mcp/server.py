"""Command-line entry point for LoomStroke MCP server modes."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from .protocol import McpProtocolRouter
from .service import LoomStrokeMcpService
from .transports.stdio import StdioTransport
from .transports.streamable_http import StreamableHttpTransport


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run LoomStroke MCP server transports.")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--stdio", action="store_true", help="Run Content-Length framed stdio.")
    mode.add_argument("--http", action="store_true", help="Run local Streamable HTTP transport.")
    parser.add_argument("--document", action="append", default=[], help="Headless .lstroke path.")
    parser.add_argument("--root", action="append", default=[], help="Allowed root for file writes.")
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="HTTP bind host; only 127.0.0.1 is allowed.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=0,
        help="HTTP port. 0 selects an ephemeral port.",
    )
    parser.add_argument(
        "--origin",
        action="append",
        default=[],
        help="Allowed HTTP Origin. Repeat for multiple origins.",
    )
    parser.add_argument(
        "--session-token",
        default=None,
        help="Explicit HTTP bearer token. The value is not printed by default.",
    )
    parser.add_argument(
        "--print-session-token",
        action="store_true",
        help="Print the HTTP bearer token for local development. Avoid shared logs.",
    )
    return parser


def _http_bootstrap_payload(
    state: Any,
    service: LoomStrokeMcpService,
    *,
    print_session_token: bool,
    session_token_source: str,
) -> dict[str, Any]:
    return {
        "transport": "streamable_http",
        "host": state.host,
        "port": state.port,
        "path": state.path,
        "sessionToken": state.session_token if print_session_token else "<redacted>",
        "sessionTokenPrinted": bool(print_session_token),
        "sessionTokenSource": session_token_source,
        "allowedOrigins": list(service.safety.allowed_origins),
    }


async def async_main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    service = LoomStrokeMcpService(mode="live_http" if args.http else "stdio", roots=args.root)
    if args.session_token:
        service.safety.session_token = str(args.session_token)
    for document in args.document:
        service.load_lstroke(Path(document), strict=True)
    router = McpProtocolRouter(service)
    if args.stdio:
        await StdioTransport(router).serve()
        return 0

    transport = StreamableHttpTransport(
        router,
        safety=service.safety,
        host=args.host,
        port=args.port,
        allowed_origins=tuple(args.origin) if args.origin else None,
    )
    state = await transport.start()
    print(
        json.dumps(
            _http_bootstrap_payload(
                state,
                service,
                print_session_token=bool(args.print_session_token),
                session_token_source="provided" if args.session_token else "generated",
            ),
            ensure_ascii=False,
        ),
        file=sys.stderr,
        flush=True,
    )
    try:
        await asyncio.Event().wait()
    finally:
        await transport.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
