"""Adapter for OpenAI Responses API."""

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
_ENDPOINT = "https://api.openai.com/v1/responses"
_MODELS_ENDPOINT = "https://api.openai.com/v1/models"


class OpenAIAdapter:
    """Implementation of ProviderAdapter for OpenAI."""

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
        content_entries: list[dict[str, Any]] = []
        image_parts = [
            {
                "type": "input_image",
                "image_base64": base64.b64encode(attachment.data).decode("ascii"),
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
            parts: list[dict[str, Any]] = []
            if content:
                parts.append({"type": "input_text", "text": content})
            if role == "user" and not image_attached and image_parts:
                parts.extend(image_parts)
                image_attached = True
            content_entries.append(
                {"role": role, "content": parts or [{"type": "input_text", "text": ""}]}
            )
        body: dict[str, Any] = {
            "model": "",
            "input": content_entries
            or [{"role": "user", "content": [{"type": "input_text", "text": ""}]}],
            "temperature": temperature,
        }
        if system_text:
            body["instructions"] = system_text
        if max_tokens:
            body["max_output_tokens"] = max_tokens
        if response_format:
            body["response_format"] = response_format
        headers = {"Content-Type": "application/json"}
        return ProviderPayload(body=body, headers=headers)

    async def submit(self, ctx: SubmitContext) -> ProviderJob:
        headers = dict(ctx.payload.headers)
        headers["Authorization"] = f"Bearer {ctx.config.api_key}"
        body = dict(ctx.payload.body)
        body["model"] = ctx.config.model
        try:
            async with ctx.session.post(_ENDPOINT, json=body, headers=headers) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except ClientResponseError as exc:
            if exc.status in {401, 403}:
                raise AuthenticationError("OpenAI authentication failed") from exc
            raise LLMError(f"OpenAI request failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover - network failure
            raise LLMError(f"OpenAI request error: {exc}") from exc
        remote_id = str(data.get("id", ctx.job_id))
        metadata = {"response": data}
        return ProviderJob(
            remote_id=remote_id,
            provider="openai",
            model=ctx.config.model,
            submitted_at=self._now(),
            metadata=metadata,
        )

    async def stream(self, ctx: StreamContext) -> AsyncIterator[ProviderChunk]:
        response = ctx.job.metadata.get("response", {}) if ctx.job.metadata else {}
        text_parts: list[str] = []
        usage = response.get("usage")
        outputs = response.get("output", [])
        for item in outputs:
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text_parts.append(str(content.get("text", "")))
        delta = "".join(text_parts)
        yield ProviderChunk(delta=delta, finish_reason="stop", usage=usage or None)

    async def cancel(self, ctx: CancelContext) -> None:  # pragma: no cover - no cancel endpoint
        return

    async def health(self, ctx: HealthContext) -> bool:
        headers = {"Authorization": f"Bearer {ctx.config.api_key}"}
        try:
            async with ctx.session.get(_MODELS_ENDPOINT, headers=headers) as resp:
                resp.raise_for_status()
                return True
        except Exception:
            _logger.debug("OpenAI health check failed", exc_info=True)
            return False

    @staticmethod
    def _now() -> float:
        return asyncio.get_running_loop().time()
