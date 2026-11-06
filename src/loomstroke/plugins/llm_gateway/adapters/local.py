"""Adapter for local OpenAI-compatible servers (Ollama, LM Studio, llama.cpp, vLLM).

Uses ``/chat/completions`` because most local servers do not implement the
Responses API the OpenAI adapter talks to.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import re
from collections.abc import AsyncIterator, Mapping
from typing import Any

from aiohttp import ClientResponseError, ClientTimeout

from ..errors import AuthenticationError, LLMError
from ..types import ImageAttachment, ProviderPayload
from .base import (
    CancelContext,
    HealthContext,
    ProviderChunk,
    ProviderJob,
    StreamContext,
    SubmitContext,
)

_logger = logging.getLogger(__name__)
# The first request after a server start loads the model, which can take
# far longer than the cloud default.
_MIN_TIMEOUT_SEC = 300.0
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)


def strip_thinking(text: str) -> str:
    """Drop the ``<think>`` trace reasoning models put before the answer."""
    text = _THINK_RE.sub("", text)
    # Some chat templates emit only the closing tag.
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    return text.strip()


class LocalAdapter:
    """ProviderAdapter for a local server's OpenAI-compatible endpoints."""

    def build_payload(
        self,
        *,
        messages: list[dict[str, Any]],
        response_format: Mapping[str, Any] | None,
        attachments: list[ImageAttachment],
        temperature: float,
        max_tokens: int | None,
    ) -> ProviderPayload:
        images = [
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:{attachment.mime};base64,"
                    + base64.b64encode(attachment.data).decode("ascii")
                },
            }
            for attachment in attachments
        ]
        mapped: list[dict[str, Any]] = []
        image_attached = False
        for message in messages:
            role = str(message.get("role", "user"))
            content = str(message.get("content", ""))
            if role == "user" and not image_attached and images:
                mapped.append(
                    {"role": role, "content": [{"type": "text", "text": content}, *images]}
                )
                image_attached = True
            else:
                mapped.append({"role": role, "content": content})
        body: dict[str, Any] = {
            "model": "",
            "messages": mapped or [{"role": "user", "content": ""}],
            "temperature": temperature,
            "stream": False,
        }
        if max_tokens:
            body["max_tokens"] = max_tokens
        if response_format:
            body["response_format"] = {"type": "json_object"}
        return ProviderPayload(body=body, headers={"Content-Type": "application/json"})

    @staticmethod
    def _headers(api_key: str) -> dict[str, str]:
        # Local servers usually need no key; send one only when stored.
        return {"Authorization": f"Bearer {api_key}"} if api_key else {}

    @staticmethod
    def _timeout(ctx_timeout: float) -> ClientTimeout:
        return ClientTimeout(total=max(float(ctx_timeout or 0), _MIN_TIMEOUT_SEC))

    async def submit(self, ctx: SubmitContext) -> ProviderJob:
        headers = {**ctx.payload.headers, **self._headers(ctx.config.api_key)}
        body = dict(ctx.payload.body)
        body["model"] = ctx.config.model
        endpoint = f"{ctx.config.base_url}/chat/completions"
        try:
            async with ctx.session.post(
                endpoint, json=body, headers=headers, timeout=self._timeout(ctx.config.timeout)
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except ClientResponseError as exc:
            if exc.status in {401, 403}:
                raise AuthenticationError("Local LLM server rejected the request") from exc
            raise LLMError(f"Local LLM request failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover - network failure
            raise LLMError(f"Local LLM request error: {exc}") from exc
        return ProviderJob(
            remote_id=str(data.get("id", ctx.job_id)),
            provider="local",
            model=ctx.config.model,
            submitted_at=self._now(),
            metadata={"response": data},
        )

    async def stream(self, ctx: StreamContext) -> AsyncIterator[ProviderChunk]:
        response = ctx.job.metadata.get("response", {}) if ctx.job.metadata else {}
        parts: list[str] = []
        for choice in response.get("choices", []):
            message = choice.get("message") or {}
            parts.append(str(message.get("content") or ""))
        yield ProviderChunk(
            delta=strip_thinking("".join(parts)),
            finish_reason="stop",
            usage=response.get("usage") or None,
        )

    async def cancel(self, ctx: CancelContext) -> None:  # pragma: no cover - no cancel endpoint
        return

    async def health(self, ctx: HealthContext) -> bool:
        try:
            await self.list_models(ctx)
        except Exception:
            _logger.debug("Local LLM health check failed", exc_info=True)
            return False
        return True

    async def list_models(self, ctx: HealthContext) -> list[str]:
        async with ctx.session.get(
            f"{ctx.config.base_url}/models", headers=self._headers(ctx.config.api_key)
        ) as resp:
            resp.raise_for_status()
            data = await resp.json()
        return [str(entry["id"]) for entry in data.get("data", []) if entry.get("id")]

    @staticmethod
    def _now() -> float:
        return asyncio.get_running_loop().time()
