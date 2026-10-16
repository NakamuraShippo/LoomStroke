"""Base protocol and helper dataclasses for provider adapters."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import aiohttp

from ..types import ImageAttachment, ProviderPayload


@dataclass(slots=True)
class ProviderConfig:
    api_key: str
    model: str
    timeout: float
    verify_ssl: bool


@dataclass(slots=True)
class SubmitContext:
    session: aiohttp.ClientSession
    config: ProviderConfig
    payload: ProviderPayload
    attachments: list[ImageAttachment]
    job_id: str


@dataclass(slots=True)
class StreamContext:
    session: aiohttp.ClientSession
    config: ProviderConfig
    job: ProviderJob


@dataclass(slots=True)
class CancelContext:
    session: aiohttp.ClientSession
    config: ProviderConfig
    job: ProviderJob


@dataclass(slots=True)
class HealthContext:
    session: aiohttp.ClientSession
    config: ProviderConfig


@dataclass(slots=True)
class ProviderJob:
    remote_id: str
    provider: str
    model: str
    submitted_at: float
    metadata: Mapping[str, Any]


@dataclass(slots=True)
class ProviderChunk:
    delta: str
    finish_reason: str | None
    usage: Mapping[str, Any] | None = None


class ProviderAdapter(Protocol):
    """Adapter contract for submitting requests to providers."""

    async def submit(self, ctx: SubmitContext) -> ProviderJob: ...

    async def stream(self, ctx: StreamContext) -> AsyncIterator[ProviderChunk]: ...

    async def cancel(self, ctx: CancelContext) -> None: ...

    async def health(self, ctx: HealthContext) -> bool: ...

    def build_payload(
        self,
        *,
        messages: list[dict[str, Any]],
        response_format: Mapping[str, Any] | None,
        attachments: list[ImageAttachment],
        temperature: float,
        max_tokens: int | None,
    ) -> ProviderPayload: ...
