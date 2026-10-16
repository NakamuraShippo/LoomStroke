"""Shared policy helpers for outbound network operations."""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Mapping
from typing import Any

CONSENT_SETTING_KEY = "privacy/allow_external_services"

logger = logging.getLogger(__name__)


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        value_lower = value.strip().lower()
        if value_lower in {"true", "1", "yes", "on"}:
            return True
        if value_lower in {"false", "0", "no", "off"}:
            return False
        return None
    if isinstance(value, Mapping):
        return None
    return bool(value)


def _resolve_settings_source(settings: Any) -> Any:
    target = settings
    if target is None:
        return None

    nested = getattr(target, "settings", None)
    if nested is not None:
        if callable(getattr(nested, "get", None)) or callable(getattr(nested, "value", None)):
            return nested
        return target

    return target


def is_external_services_allowed(settings: Any) -> bool:
    """Return whether external service access is enabled."""

    source = _resolve_settings_source(settings)
    if source is None:
        return False

    getter = getattr(source, "get", None)
    if callable(getter):
        with contextlib.suppress(Exception):
            value = getter(CONSENT_SETTING_KEY)
            coerced = _coerce_bool(value)
            if coerced is not None:
                return coerced
    value_getter = getattr(source, "value", None)
    if callable(value_getter):
        with contextlib.suppress(Exception):
            value = value_getter(CONSENT_SETTING_KEY)
            coerced = _coerce_bool(value)
            if coerced is not None:
                return coerced

    if isinstance(source, Mapping):
        value = source.get(CONSENT_SETTING_KEY)
        coerced = _coerce_bool(value)
        if coerced is not None:
            return coerced

    return False


def ensure_network_services_consent(settings: Any, *, service_name: str) -> bool:
    """Check explicit consent before starting outbound communication.

    Returns True when consent is granted. Returns False otherwise.
    """

    if not is_external_services_allowed(settings):
        logger.warning(
            "External service access blocked for %s: set '%s'=true to enable",
            service_name,
            CONSENT_SETTING_KEY,
        )
        return False
    return True


def build_and_validate_comfyui_base_url(
    host: str | None,
    port: str | int | None,
    use_ssl: bool | None,
    *,
    service_name: str = "ComfyUI",
) -> str:
    """Build and validate a ComfyUI base URL from host/port/settings."""

    if host is None:
        raise ValueError(f"{service_name}: host is required")
    if isinstance(port, bool):
        raise ValueError(f"{service_name}: port must be numeric")

    normalized_host = str(host).strip()
    if not normalized_host:
        raise ValueError(f"{service_name}: host cannot be empty")
    if "://" in normalized_host:
        raise ValueError(f"{service_name}: host must not include URL scheme")
    if "\n" in normalized_host or "\r" in normalized_host:
        raise ValueError(f"{service_name}: invalid host characters")

    try:
        normalized_port = int(port)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{service_name}: invalid port {port!r}") from exc

    if not (1 <= normalized_port <= 65535):
        raise ValueError(f"{service_name}: port must be between 1 and 65535")

    scheme = "https" if bool(use_ssl) else "http"
    return f"{scheme}://{normalized_host}:{normalized_port}"
