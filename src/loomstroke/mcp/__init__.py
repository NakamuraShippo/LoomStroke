"""Model Context Protocol facade for LoomStroke.

The MCP package is intentionally inert on import: it does not start a server,
open a socket, create a wgpu device, or touch live UI canvas state.
"""

from __future__ import annotations

from .protocol import McpProtocolRouter
from .service import LoomStrokeMcpService
from .types import PROTOCOL_VERSION, SERVER_NAME, SERVER_VERSION, McpStructuredError

__all__ = [
    "PROTOCOL_VERSION",
    "SERVER_NAME",
    "SERVER_VERSION",
    "LoomStrokeMcpService",
    "McpProtocolRouter",
    "McpStructuredError",
]
