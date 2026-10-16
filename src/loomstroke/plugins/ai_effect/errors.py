"""AI Effect error taxonomy.

Spec reference:
- ``AIeffect_design/04_python_design/gateway_protocol.md`` §3.3 (AIError canonical)
- ``AIeffect_design/04_python_design/error_handling.md`` §1 (8 ErrorClass UI mapping)

The 8-class ``ErrorClass`` enum drives:
- Layers Panel badge color (connection=orange, auth=red, ...)
- Effect Panel remediation button set
- Auto-retry eligibility (only BACKEND / TIMEOUT retry automatically)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

_MASK_VALUE = "***masked***"
_SENSITIVE_KEY_PATTERNS: tuple[str, ...] = (
    "api_key",
    "apikey",
    "token",
    "password",
    "secret",
    "authorization",
    "bearer",
)


def _mask_sensitive(raw: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``raw`` with values of sensitive keys masked.

    A key is considered sensitive if it is a string and any pattern in
    ``_SENSITIVE_KEY_PATTERNS`` appears within it (case-insensitive,
    substring match — e.g. ``"x_api_key"`` and ``"Authorization"`` both match).

    Only ``str`` and ``bytes`` values are replaced with ``"***masked***"``.
    Non-string values (int, bool, None, lists of non-strings, ...) are kept
    as-is so benign metadata like status codes survives intact. Nested dicts
    are traversed recursively; dicts inside lists/tuples are also masked.

    The input dict is NOT mutated.
    """

    def _mask_value(value: Any) -> Any:
        if isinstance(value, dict):
            return _mask_sensitive(value)
        if isinstance(value, list):
            return [_mask_value(v) for v in value]
        if isinstance(value, tuple):
            return tuple(_mask_value(v) for v in value)
        return value

    result: dict[str, Any] = {}
    for key, value in raw.items():
        key_lower = key.lower() if isinstance(key, str) else ""
        is_sensitive = isinstance(key, str) and any(
            pattern in key_lower for pattern in _SENSITIVE_KEY_PATTERNS
        )
        if is_sensitive and isinstance(value, (str, bytes)):
            result[key] = _MASK_VALUE
        else:
            result[key] = _mask_value(value)
    return result


class ErrorClass(str, Enum):
    """Failure taxonomy for AI Effect Layer execution.

    The string value is persisted (logs, JSON) so existing values MUST NOT
    change once in use. New classes may be appended.
    """

    CONNECTION = "connection"
    AUTH = "auth"
    DEPENDENCY = "dependency"
    VALIDATION = "validation"
    BACKEND = "backend"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


@dataclass
class AIError:
    """Structured error carried from Gateway implementations to UI.

    UI layer branches on ``class_`` only — never parses ``title`` or ``detail``
    strings. Gateway implementations are responsible for mapping backend
    exceptions to the correct ErrorClass.

    Fields:
        class_: Taxonomy class (drives UI color / retry rules).
        title: One-line, user-facing, i18n-resolved message.
        detail: Optional developer-facing detail (stack traces, raw payloads).
        remediation: Optional i18n-resolved recovery hint
            (e.g. "Check that ComfyUI is running").
        raw: Optional dict of machine-readable error metadata for logs.
            Gateway implementations SHOULD mask secrets before construction,
            but as a defence-in-depth measure ``__post_init__`` auto-masks
            values of common credential keys (api_key / token / password /
            secret / authorization / bearer, case-insensitive substring
            match). See ``_mask_sensitive`` for details.
    """

    class_: ErrorClass
    title: str
    detail: str | None = None
    remediation: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Defence-in-depth: re-scan raw and mask credential-like keys that
        # Gateway implementations might have forgotten to mask.
        if self.raw:
            self.raw = _mask_sensitive(self.raw)


class AIGatewayError(Exception):
    """Exception wrapper carrying a structured ``AIError`` payload.

    Gateway implementations raise this to the Controller instead of leaking
    raw aiohttp / asyncio exceptions. The Controller reads ``.error`` to
    route to the right UI path.
    """

    def __init__(self, error: AIError):
        super().__init__(error.title)
        self.error = error

    def __repr__(self) -> str:
        return f"AIGatewayError(class_={self.error.class_.value}, title={self.error.title!r})"
