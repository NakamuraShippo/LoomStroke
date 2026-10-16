"""Credential and binary payload scrubbing for SAM3 metadata."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

_SENSITIVE_KEY_MARKERS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "authorization",
    "binary",
    "cookie",
    "credential",
    "image_bytes",
    "mask_bytes",
    "password",
    "private_endpoint",
    "request_header",
    "secret",
    "token",
)
_REDACTED = "<redacted>"


def scrub_sam3_metadata(value: Any) -> Any:
    """Return a JSON-compatible copy without secret or binary payload values."""

    if isinstance(value, Mapping):
        scrubbed: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                scrubbed[key_text] = _REDACTED
                continue
            scrubbed[key_text] = scrub_sam3_metadata(item)
        return scrubbed
    if isinstance(value, tuple):
        return [scrub_sam3_metadata(item) for item in value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [scrub_sam3_metadata(item) for item in value]
    if isinstance(value, (bytes, bytearray, memoryview)):
        return _REDACTED
    return value


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_").replace(" ", "_")
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)


__all__ = ["scrub_sam3_metadata"]
