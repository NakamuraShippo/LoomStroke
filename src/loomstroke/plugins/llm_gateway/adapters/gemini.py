"""Adapter for Google Gemini Developer API."""

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
_ENDPOINT_PREFIX = "https://generativelanguage.googleapis.com/v1beta/models"
_MODELS_ENDPOINT = f"{_ENDPOINT_PREFIX}"


class GeminiAdapter:
    """Provider adapter for Gemini generateContent API."""

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
        contents: list[dict[str, Any]] = []
        image_parts = [
            {
                "inline_data": {
                    "mime_type": attachment.mime,
                    "data": base64.b64encode(attachment.data).decode("ascii"),
                }
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
                parts.append({"text": content})
            if role == "user" and not image_attached and image_parts:
                parts.extend(image_parts)
                image_attached = True
            contents.append({"role": role, "parts": parts or [{"text": ""}]})
        # Gemini 2.5 has "thinking" enabled by default and consumes
        # output tokens for the internal reasoning trace. With small
        # ``max_tokens`` (the LLM panel defaults to 512) the entire
        # budget can be eaten by thinking and the response text comes
        # back empty / truncated. The LLM Gateway is used for chat-
        # style completions where thinking is unwanted, so disable
        # it by default. ``thinkingBudget=0`` is the documented opt-
        # out for Gemini 2.5 Flash.
        body: dict[str, Any] = {
            "contents": contents or [{"role": "user", "parts": [{"text": ""}]}],
            "generationConfig": {
                "temperature": temperature,
                "thinkingConfig": {"thinkingBudget": 0},
            },
        }
        if system_text:
            body["systemInstruction"] = {"parts": [{"text": system_text}]}
        if max_tokens:
            body["generationConfig"]["maxOutputTokens"] = max_tokens
        if response_format:
            body["generationConfig"]["responseMimeType"] = response_format.get(
                "mime_type", "application/json"
            )
            if "schema" in response_format:
                body["generationConfig"]["responseSchema"] = response_format["schema"]
        headers = {"Content-Type": "application/json"}
        return ProviderPayload(body=body, headers=headers)

    async def submit(self, ctx: SubmitContext) -> ProviderJob:
        headers = dict(ctx.payload.headers)
        headers["x-goog-api-key"] = ctx.config.api_key
        body = dict(ctx.payload.body)
        endpoint = f"{_ENDPOINT_PREFIX}/{ctx.config.model}:generateContent"
        try:
            async with ctx.session.post(endpoint, json=body, headers=headers) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except ClientResponseError as exc:
            if exc.status in {401, 403}:
                raise AuthenticationError("Gemini authentication failed") from exc
            raise LLMError(f"Gemini request failed: {exc}") from exc
        except Exception as exc:  # pragma: no cover
            raise LLMError(f"Gemini request error: {exc}") from exc
        remote_id = data.get("id") or ctx.job_id
        metadata = {"response": data}
        return ProviderJob(
            remote_id=str(remote_id),
            provider="gemini",
            model=ctx.config.model,
            submitted_at=self._now(),
            metadata=metadata,
        )

    async def stream(self, ctx: StreamContext) -> AsyncIterator[ProviderChunk]:
        response = ctx.job.metadata.get("response", {}) if ctx.job.metadata else {}
        text_parts: list[str] = []
        candidates = response.get("candidates", [])
        for candidate in candidates:
            for part in candidate.get("content", {}).get("parts", []):
                if "text" in part:
                    text_parts.append(str(part.get("text", "")))
        delta = "".join(text_parts)
        usage = response.get("usageMetadata")
        yield ProviderChunk(delta=delta, finish_reason="stop", usage=usage)

    async def cancel(self, ctx: CancelContext) -> None:  # pragma: no cover
        return

    async def health(self, ctx: HealthContext) -> bool:
        headers = {"x-goog-api-key": ctx.config.api_key}
        try:
            async with ctx.session.get(_MODELS_ENDPOINT, headers=headers) as resp:
                resp.raise_for_status()
                return True
        except Exception:
            _logger.debug("Gemini health check failed", exc_info=True)
            return False

    @staticmethod
    def _now() -> float:
        return asyncio.get_running_loop().time()
