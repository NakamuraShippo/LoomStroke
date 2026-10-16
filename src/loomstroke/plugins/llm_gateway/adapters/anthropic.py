"""Adapter for Anthropic Claude Messages API."""

from __future__ import annotations

import asyncio
import base64
import logging
from collections.abc import AsyncIterator, Mapping
from typing import Any

from aiohttp import ClientResponseError

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
_ENDPOINT = "https://api.anthropic.com/v1/messages"
_MODELS_ENDPOINT = "https://api.anthropic.com/v1/models"
_ANTHROPIC_VERSION = "2023-06-01"


class ClaudeAdapter:
    """Provider adapter implementation for Anthropic Claude."""

    def build_payload(
        self,
        *,
        messages: list[dict[str, Any]],
        response_format: Mapping[str, Any] | None,
        attachments: list[ImageAttachment],
        temperature: float,
        max_tokens: int | None,
    ) -> ProviderPayload:
        system_text: str | None = None
        mapped_messages: list[dict[str, Any]] = []
        image_blocks = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": attachment.mime,
                    "data": base64.b64encode(attachment.data).decode("ascii"),
                },
            }
            for attachment in attachments
        ]
        image_attached = False
        for message in messages:
            role = message.get("role", "user")
            content = str(message.get("content", ""))
            if role == "system":
                system_text = content
                continue
            blocks: list[dict[str, Any]] = []
            if content:
                blocks.append({"type": "text", "text": content})
            if role == "user" and not image_attached and image_blocks:
                blocks.extend(image_blocks)
                image_attached = True
            mapped_messages.append({"role": role, "content": blocks})
        body: dict[str, Any] = {
            "model": "",
            "messages": mapped_messages
            or [{"role": "user", "content": [{"type": "text", "text": ""}]}],
            "temperature": temperature,
            # ``max_tokens`` is REQUIRED by the Anthropic Messages
            # API — omitting it returns HTTP 400. The LLM panel
            # supplies a value, but defensively pin a fallback so
            # any caller that forgets it still gets a useful default
            # rather than a confusing 400.
            "max_tokens": max_tokens or 4096,
        }
        if system_text:
            body["system"] = system_text
        if response_format:
            body["response_format"] = response_format
        headers = {
            "Content-Type": "application/json",
            "anthropic-version": _ANTHROPIC_VERSION,
        }
        return ProviderPayload(body=body, headers=headers)

    async def submit(self, ctx: SubmitContext) -> ProviderJob:
        headers = dict(ctx.payload.headers)
        headers["x-api-key"] = ctx.config.api_key
        body = dict(ctx.payload.body)
        body["model"] = ctx.config.model
        try:
            async with ctx.session.post(_ENDPOINT, json=body, headers=headers) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except ClientResponseError as exc:
            if exc.status in {401, 403}:
                raise AuthenticationError("Anthropic authentication failed") from exc
            raise LLMError(f"Anthropic request failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover
            raise LLMError(f"Anthropic request error: {exc}") from exc
        remote_id = str(data.get("id", ctx.job_id))
        metadata = {"response": data}
        return ProviderJob(
            remote_id=remote_id,
            provider="anthropic",
            model=ctx.config.model,
            submitted_at=self._now(),
            metadata=metadata,
        )

    async def stream(self, ctx: StreamContext) -> AsyncIterator[ProviderChunk]:
        response = ctx.job.metadata.get("response", {}) if ctx.job.metadata else {}
        text_parts: list[str] = []
        for block in response.get("content", []):
            if block.get("type") == "text":
                text_parts.append(str(block.get("text", "")))
        delta = "".join(text_parts)
        usage = response.get("usage")
        yield ProviderChunk(delta=delta, finish_reason="stop", usage=usage)

    async def cancel(self, ctx: CancelContext) -> None:  # pragma: no cover
        return

    async def health(self, ctx: HealthContext) -> bool:
        headers = {
            "x-api-key": ctx.config.api_key,
            "anthropic-version": _ANTHROPIC_VERSION,
        }
        try:
            async with ctx.session.get(_MODELS_ENDPOINT, headers=headers) as resp:
                resp.raise_for_status()
                return True
        except Exception:
            _logger.debug("Anthropic health check failed", exc_info=True)
            return False

    @staticmethod
    def _now() -> float:
        return asyncio.get_running_loop().time()
