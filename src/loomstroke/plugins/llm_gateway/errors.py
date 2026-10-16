"""Error hierarchy for the LLM gateway plugin."""

from __future__ import annotations

from dataclasses import dataclass


class LLMError(RuntimeError):
    """Base error for LLM gateway issues."""


class AuthenticationError(LLMError):
    """Raised when remote service rejects credentials."""


class InvalidRequestError(LLMError):
    """Raised when payload validation fails locally or remotely."""


class RateLimitError(LLMError):
    """Raised when the provider indicates throttling is required."""


class ServerError(LLMError):
    """Raised when upstream service responds with 5xx."""


class TimeoutError(LLMError):
    """Raised when a network request exceeds the configured timeout."""


class AttachmentError(LLMError):
    """Raised when image attachments cannot be prepared."""


@dataclass(frozen=True)
class ErrorDetail:
    """Carries additional information about provider failures."""

    status: int | None = None
    code: str | None = None
    message: str | None = None


ERROR_MAP: dict[int, type[LLMError]] = {
    400: InvalidRequestError,
    401: AuthenticationError,
    403: AuthenticationError,
    404: InvalidRequestError,
    408: TimeoutError,
    409: InvalidRequestError,
    422: InvalidRequestError,
    429: RateLimitError,
    500: ServerError,
    502: ServerError,
    503: ServerError,
    504: TimeoutError,
}
