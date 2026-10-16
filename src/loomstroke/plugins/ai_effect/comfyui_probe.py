"""HTTP probe for ComfyUI detection (Phase 6-i Onboarding extension).

When the user opens AI Effect for the first time, the panel needs a
URL to point at. Hard-coding ``127.0.0.1:8188`` works in the common
case but offers no signal whether a server is actually listening.
This module provides a small async ``probe_comfyui_url`` that issues
a tight-timeout HTTP GET against ``<url>/object_info`` — the same
endpoint ``AsyncComfyUIConnection.connect()`` uses as its health
check, so a successful probe directly implies the connect path will
also succeed.

Pure helper, no UI; the result drives the
``show_comfyui_detection_dialog`` flow in
``ui.onboarding_dialogs``. Kept in its own module so headless tests
do not need to drag Qt in.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import aiohttp

from loomstroke.plugins.common.outbound_policy import ensure_network_services_consent

logger = logging.getLogger(__name__)


#: Conventional ComfyUI defaults. Mirrored against
#: ``plugins.comfyui.ui.connection_bar.DEFAULT_COMFYUI_ADDRESS`` so
#: probing without an existing settings file lands on the same URL
#: the connection bar would have populated.
DEFAULT_COMFYUI_URL = "http://127.0.0.1:8188"


@dataclass(frozen=True)
class ProbeResult:
    """Outcome of a single ComfyUI probe attempt.

    ``ok`` is True only when the server responded 200 and the body
    parses as JSON (object_info shape). Any other outcome — refused
    connection, timeout, non-2xx, non-JSON — is False with the
    error message captured for the dialog to surface.
    """

    url: str
    ok: bool
    error: str | None = None


async def probe_comfyui_url(
    url: str = DEFAULT_COMFYUI_URL,
    *,
    timeout_s: float = 1.5,
    settings: Any = None,
) -> ProbeResult:
    """Issue a single HTTP GET against ``<url>/object_info``.

    Tight timeout so the probe stays fast on the typical
    "is ComfyUI running locally?" path. The default 1.5s is enough
    for a localhost connection but short enough that an unreachable
    URL fails the probe quickly. Failures are caught and reported in
    the ``ProbeResult.error`` field; the function never raises.
    """
    if settings is not None and not ensure_network_services_consent(
        settings, service_name="ComfyUI"
    ):
        return ProbeResult(url=url, ok=False, error="privacy_denied")

    target = url.rstrip("/") + "/object_info"
    timeout = aiohttp.ClientTimeout(total=timeout_s)
    try:
        async with (
            aiohttp.ClientSession(timeout=timeout) as session,
            session.get(target) as response,
        ):
            if response.status != 200:
                msg = f"HTTP {response.status}"
                logger.debug("ComfyUI probe %s -> %s", target, msg)
                return ProbeResult(url=url, ok=False, error=msg)
            data = await response.json(content_type=None)
            if not isinstance(data, dict) or not data:
                return ProbeResult(
                    url=url,
                    ok=False,
                    error="empty or non-dict /object_info response",
                )
            return ProbeResult(url=url, ok=True)
    except (TimeoutError, aiohttp.ClientError, OSError) as exc:
        logger.debug("ComfyUI probe %s failed: %s", target, exc)
        return ProbeResult(url=url, ok=False, error=str(exc))


__all__ = ["DEFAULT_COMFYUI_URL", "ProbeResult", "probe_comfyui_url"]
