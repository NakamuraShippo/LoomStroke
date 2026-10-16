"""MCP stdio transport with client framing compatibility."""

from __future__ import annotations

import asyncio
import json
import sys
from typing import Any, BinaryIO


class StdioTransport:
    """Run a JSON-RPC router over MCP stdio framing."""

    def __init__(
        self,
        router: Any,
        *,
        stdin: BinaryIO | None = None,
        stdout: BinaryIO | None = None,
    ) -> None:
        self.router = router
        self.stdin = stdin or sys.stdin.buffer
        self.stdout = stdout or sys.stdout.buffer
        self._response_framing = "content_length"

    async def serve(self) -> None:
        while True:
            message = await self.read_message()
            if message is None:
                return
            response = await self.router.handle_json(message)
            if response is not None:
                await self.write_message(response)

    async def read_message(self) -> bytes | None:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._read_message_sync)

    async def write_message(self, message: Any) -> None:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self._write_message_sync, message)

    def _read_message_sync(self) -> bytes | None:
        first = self.stdin.readline()
        while first and not first.strip():
            first = self.stdin.readline()
        if not first:
            return None
        if first.lstrip().startswith(b"{") or first.lstrip().startswith(b"["):
            self._response_framing = "json_line"
            return first.strip()

        self._response_framing = "content_length"
        headers: dict[str, str] = {}
        line = first
        while line:
            decoded = line.decode("ascii", errors="replace").strip()
            if not decoded:
                break
            if ":" in decoded:
                key, value = decoded.split(":", 1)
                headers[key.lower()] = value.strip()
            line = self.stdin.readline()
        length_raw = headers.get("content-length")
        if length_raw is None:
            raise ValueError("MCP stdio message missing Content-Length")
        try:
            length = int(length_raw)
        except ValueError as exc:
            raise ValueError("Invalid MCP Content-Length") from exc
        if length < 0:
            raise ValueError("Invalid MCP Content-Length")
        return self.stdin.read(length)

    def _write_message_sync(self, message: Any) -> None:
        payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        if self._response_framing == "json_line":
            self.stdout.write(payload)
            self.stdout.write(b"\n")
            self.stdout.flush()
            return
        header = f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii")
        self.stdout.write(header)
        self.stdout.write(payload)
        self.stdout.flush()
