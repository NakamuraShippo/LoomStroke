"""Safety guards for MCP transports and side-effecting tools."""

from __future__ import annotations

import inspect
import os
import secrets
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from loomstroke.platform.settings_schema import (
    MCP_CONFIRMATION_POLICY_ALWAYS_ALLOW,
    MCP_CONFIRMATION_POLICY_ALWAYS_ASK,
    MCP_CONFIRMATION_POLICY_EXTERNAL_ONLY,
    MCP_CONFIRMATION_POLICY_KEY,
    MCP_CONFIRMATION_POLICY_VALUES,
    MCP_SKIP_HARD_CONFIRMATIONS_KEY,
)

from .types import McpStructuredError, ReasonCode

CONSENT_SETTING_KEY = "privacy/allow_external_services"

_EXTERNAL_QUEUE_CONFIRMATION_OPERATIONS = frozenset(
    {
        "loomstroke_run_production_recipe",
        "loomstroke_submit_generation",
    }
)

_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "credential",
    "password",
    "secret",
    "token",
)
_SAFE_SECRET_METADATA_KEYS = frozenset(
    {
        "credentialpolicy",
        "credentialreadperformed",
        "credentialrequired",
        "credentialstatus",
        "credentialvaluesincluded",
        "sessiontokenrequired",
        "sessiontokenresponseheader",
        "tokenacceptedin",
        "urlquerytokenaccepted",
    }
)

ConfirmationCallback = Callable[[str, Mapping[str, Any]], bool | Awaitable[bool]]


def make_session_token() -> str:
    return secrets.token_urlsafe(32)


def scrub_secrets(value: Any) -> Any:
    """Return a JSON-like value with credential-shaped fields redacted."""

    if isinstance(value, Mapping):
        scrubbed: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            lowered = key_text.lower()
            if lowered not in _SAFE_SECRET_METADATA_KEYS and any(
                part in lowered for part in _SECRET_KEY_PARTS
            ):
                scrubbed[key_text] = "<redacted>"
            else:
                scrubbed[key_text] = scrub_secrets(item)
        return scrubbed
    if isinstance(value, list | tuple):
        return [scrub_secrets(item) for item in value]
    return value


def _coerce_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off", ""}:
            return False
    return default


def external_services_allowed(settings: Any) -> bool:  # noqa: PLR0911
    """Fail-closed read of the privacy gate from mapping/settings-like sources."""

    if settings is None:
        return False
    try:
        if isinstance(settings, Mapping):
            return _coerce_bool(settings.get(CONSENT_SETTING_KEY, False))
        get = getattr(settings, "get", None)
        if callable(get):
            return _coerce_bool(get(CONSENT_SETTING_KEY, False))
        value = getattr(settings, "value", None)
        if callable(value):
            return _coerce_bool(value(CONSENT_SETTING_KEY, False))
        nested = getattr(settings, "settings", None)
        if nested is not None and nested is not settings:
            return external_services_allowed(nested)
    except Exception:
        return False
    return False


def setting_value(settings: Any, key: str, default: Any = None) -> Any:
    """Fail-closed read from mapping/settings-like sources."""

    result = default
    if settings is None:
        return result
    try:
        if isinstance(settings, Mapping):
            result = settings.get(key, default)
        else:
            get = getattr(settings, "get", None)
            value = getattr(settings, "value", None)
            nested = getattr(settings, "settings", None)
            if callable(get):
                result = get(key, default)
            elif callable(value):
                result = value(key, default)
            elif nested is not None and nested is not settings:
                result = setting_value(nested, key, default)
    except Exception:
        result = default
    return result


def confirmation_policy(settings: Any) -> str:
    raw = setting_value(
        settings,
        MCP_CONFIRMATION_POLICY_KEY,
        MCP_CONFIRMATION_POLICY_ALWAYS_ASK,
    )
    if isinstance(raw, str) and raw in MCP_CONFIRMATION_POLICY_VALUES:
        return raw
    return MCP_CONFIRMATION_POLICY_ALWAYS_ASK


def requires_confirmation_for_operation(settings: Any, operation: str) -> bool:
    policy = confirmation_policy(settings)
    if policy == MCP_CONFIRMATION_POLICY_ALWAYS_ALLOW:
        return False
    if policy == MCP_CONFIRMATION_POLICY_EXTERNAL_ONLY:
        return operation in _EXTERNAL_QUEUE_CONFIRMATION_OPERATIONS
    return True


def hard_confirmations_skipped(settings: Any) -> bool:
    """Read the optional flag only under the explicit allow-all policy."""
    if confirmation_policy(settings) != MCP_CONFIRMATION_POLICY_ALWAYS_ALLOW:
        return False
    value = setting_value(settings, MCP_SKIP_HARD_CONFIRMATIONS_KEY, False)
    return isinstance(value, bool | str) and _coerce_bool(value)


def ensure_external_services_allowed(settings: Any) -> None:
    if not external_services_allowed(settings):
        raise McpStructuredError(
            ReasonCode.PRIVACY_DENIED,
            "External services are disabled.",
            details={"setting": CONSENT_SETTING_KEY},
        )


def normalize_roots(roots: Sequence[str | Path] | None) -> tuple[Path, ...]:
    normalized: list[Path] = []
    for root in roots or ():
        try:
            path = Path(root).expanduser().resolve(strict=False)
        except OSError as exc:
            raise McpStructuredError(
                ReasonCode.PERMISSION_DENIED,
                "Invalid MCP root.",
                details={"root": str(root), "error": exc.__class__.__name__},
            ) from exc
        normalized.append(path)
    return tuple(dict.fromkeys(normalized))


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath([str(path), str(root)])
    except ValueError:
        return False
    return common == str(root)


def validate_output_path(
    target: str | Path,
    *,
    roots: Sequence[str | Path],
    overwrite: bool = False,
    allowed_suffixes: Sequence[str] | None = None,
) -> Path:
    """Resolve a file-write target against roots using fail-closed policy."""

    normalized_roots = normalize_roots(roots)
    if not normalized_roots:
        raise McpStructuredError(
            ReasonCode.PERMISSION_DENIED,
            "File writes require at least one MCP root.",
            details={"policy": "roots_required"},
        )

    raw = Path(target).expanduser()
    if any(part in {"", ".", ".."} for part in raw.parts):
        raise McpStructuredError(
            ReasonCode.PERMISSION_DENIED,
            "Path traversal is not allowed.",
            details={"path": str(target)},
        )

    candidates = [raw] if raw.is_absolute() else [root / raw for root in normalized_roots]
    resolved: Path | None = None
    matched_root: Path | None = None
    for candidate in candidates:
        candidate_resolved = candidate.resolve(strict=False)
        for root in normalized_roots:
            if _is_relative_to(candidate_resolved, root):
                resolved = candidate_resolved
                matched_root = root
                break
        if resolved is not None:
            break

    if resolved is None or matched_root is None:
        raise McpStructuredError(
            ReasonCode.PERMISSION_DENIED,
            "Output path is outside the allowed MCP roots.",
            details={"path": str(target), "roots": [str(root) for root in normalized_roots]},
        )

    if allowed_suffixes is not None:
        allowed = {suffix.lower() for suffix in allowed_suffixes}
        if resolved.suffix.lower() not in allowed:
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "Unsupported export file extension.",
                details={"path": str(resolved), "allowed": sorted(allowed)},
            )

    if resolved.exists() and not overwrite:
        raise McpStructuredError(
            ReasonCode.CONFLICT,
            "Output path already exists.",
            details={"path": str(resolved), "overwrite": overwrite},
        )

    if not _is_relative_to(resolved.parent.resolve(strict=False), matched_root):
        raise McpStructuredError(
            ReasonCode.PERMISSION_DENIED,
            "Output parent is outside the allowed MCP root.",
            details={"path": str(resolved)},
        )
    return resolved


def validate_input_path(
    source: str | Path,
    *,
    roots: Sequence[str | Path],
    allowed_suffixes: Sequence[str] | None = None,
) -> Path:
    """Resolve a file-read source against roots using fail-closed policy."""

    normalized_roots = normalize_roots(roots)
    if not normalized_roots:
        raise McpStructuredError(
            ReasonCode.PERMISSION_DENIED,
            "File reads require at least one MCP root.",
            details={"policy": "roots_required"},
        )

    raw = Path(source).expanduser()
    if any(part in {"", ".", ".."} for part in raw.parts):
        raise McpStructuredError(
            ReasonCode.PERMISSION_DENIED,
            "Path traversal is not allowed.",
            details={"path": str(source)},
        )

    candidates = [raw] if raw.is_absolute() else [root / raw for root in normalized_roots]
    resolved: Path | None = None
    for candidate in candidates:
        candidate_resolved = candidate.resolve(strict=False)
        if any(_is_relative_to(candidate_resolved, root) for root in normalized_roots):
            resolved = candidate_resolved
            break

    if resolved is None:
        raise McpStructuredError(
            ReasonCode.PERMISSION_DENIED,
            "Input path is outside the allowed MCP roots.",
            details={"path": str(source), "roots": [str(root) for root in normalized_roots]},
        )

    if allowed_suffixes is not None:
        allowed = {suffix.lower() for suffix in allowed_suffixes}
        if resolved.suffix.lower() not in allowed:
            raise McpStructuredError(
                ReasonCode.INVALID_ARGUMENT,
                "Unsupported import file extension.",
                details={"path": str(resolved), "allowed": sorted(allowed)},
            )

    if not resolved.exists():
        raise McpStructuredError(
            ReasonCode.NOT_FOUND,
            "Input path does not exist.",
            details={"path": str(resolved)},
        )
    if not resolved.is_file():
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            "Input path must be a file.",
            details={"path": str(resolved)},
        )
    return resolved


def validate_local_bind_host(host: str) -> str:
    if host != "127.0.0.1":
        raise McpStructuredError(
            ReasonCode.PERMISSION_DENIED,
            "MCP HTTP transport may only bind to 127.0.0.1.",
            details={"host": host},
        )
    return host


def validate_origin(origin: str | None, allowed_origins: Sequence[str]) -> None:
    if not origin:
        raise McpStructuredError(
            ReasonCode.PERMISSION_DENIED,
            "Origin header is required.",
            details={"policy": "origin_required"},
        )
    if origin not in set(allowed_origins):
        raise McpStructuredError(
            ReasonCode.PERMISSION_DENIED,
            "Origin is not allowed.",
            details={"origin": origin},
        )


def validate_session_token(provided: str | None, expected: str) -> None:
    if not provided or not secrets.compare_digest(provided, expected):
        raise McpStructuredError(
            ReasonCode.PERMISSION_DENIED,
            "Invalid MCP session token.",
            details={"policy": "session_token_required"},
        )


class McpSafetyPolicy:
    """Runtime policy for confirmation, roots, privacy, and local HTTP security."""

    def __init__(
        self,
        *,
        settings: Any = None,
        roots: Sequence[str | Path] | None = None,
        confirmation_callback: ConfirmationCallback | None = None,
        allowed_origins: Sequence[str] | None = None,
        session_token: str | None = None,
    ) -> None:
        self.settings = settings
        self.roots = normalize_roots(roots)
        self.confirmation_callback = confirmation_callback
        self.allowed_origins = tuple(
            allowed_origins
            or (
                "http://127.0.0.1",
                "http://127.0.0.1:0",
                "http://localhost",
            )
        )
        self.session_token = session_token or make_session_token()

    @property
    def external_services_allowed(self) -> bool:
        return external_services_allowed(self.settings)

    @property
    def confirmation_policy(self) -> str:
        return confirmation_policy(self.settings)

    @property
    def hard_confirmations_skipped(self) -> bool:
        return hard_confirmations_skipped(self.settings)

    def requires_confirmation(self, operation: str) -> bool:
        return requires_confirmation_for_operation(self.settings, operation)

    async def require_confirmation(self, operation: str, details: Mapping[str, Any]) -> None:
        if not self.requires_confirmation(operation):
            return
        if self.confirmation_callback is None:
            raise McpStructuredError(
                ReasonCode.PERMISSION_DENIED,
                "User confirmation is required.",
                details={"operation": operation},
            )
        result = self.confirmation_callback(operation, scrub_secrets(dict(details)))
        if inspect.isawaitable(result):
            result = await result
        if not bool(result):
            raise McpStructuredError(
                ReasonCode.PERMISSION_DENIED,
                "User denied the requested operation.",
                details={"operation": operation},
            )

    def require_external_services(self) -> None:
        ensure_external_services_allowed(self.settings)

    def validate_output_path(
        self,
        target: str | Path,
        *,
        overwrite: bool = False,
        allowed_suffixes: Sequence[str] | None = None,
    ) -> Path:
        return validate_output_path(
            target,
            roots=self.roots,
            overwrite=overwrite,
            allowed_suffixes=allowed_suffixes,
        )

    def validate_input_path(
        self,
        source: str | Path,
        *,
        allowed_suffixes: Sequence[str] | None = None,
    ) -> Path:
        return validate_input_path(
            source,
            roots=self.roots,
            allowed_suffixes=allowed_suffixes,
        )

    def validate_http_request(self, *, origin: str | None, token: str | None) -> None:
        validate_origin(origin, self.allowed_origins)
        validate_session_token(token, self.session_token)
