"""Value redaction shared by settings and environment diagnostic logs."""

from __future__ import annotations

from typing import Any

_SENSITIVE_KEY_PARTS = ("key", "secret", "password", "token", "credential")


def redact_setting_value(key: str, value: Any) -> Any:
    """Return a log-only value; never change the stored or returned setting."""
    normalized = key.lower().replace("-", "_")
    if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
        return "<redacted>" if value else ""
    return value
