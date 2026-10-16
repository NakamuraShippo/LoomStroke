"""AI Effect telemetry sink (Phase 8c-4).

When ``preferences.developer.log_level == "debug"``, every finished
generation emits one JSON line to ``user_data/logs/ai_effect.log``
recording phase, elapsed_ms, layer_id, cache_hit, gateway_id, and
retry_count. The output is keyed off a dedicated logger
(``loomstroke.plugins.ai_effect.telemetry``) with ``propagate=False``
so the debug verbosity stays scoped to AI Effect — the root logger
and ``loomstroke.log`` are unaffected.

Production wiring lives in app.py: build with ``from_preferences`` and
inject into ``AIEffectGenerationController``. Tests bypass the file
handler by passing a custom ``logging.Handler`` (typically an
in-memory capturing handler) so they don't touch user_data.
"""

from __future__ import annotations

import contextlib
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from loomstroke.plugins.ai_effect.settings import AIEffectPreferences

#: Logger name used by every ``AIEffectTelemetry`` instance. Shared so
#: that opt-in user tooling (Preferences → "Show telemetry") can attach
#: an additional handler without instantiating the class.
LOGGER_NAME = "loomstroke.plugins.ai_effect.telemetry"


class AIEffectTelemetry:
    """JSON-line telemetry for AI Effect generations.

    One instance per running app. ``log_generation`` is called once
    per finished ``AIEffectGenerationController.execute`` call. When
    ``enabled`` is False every call is a no-op so production code can
    leave the integration in place without conditionals.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        log_path: Path | None = None,
        handler: logging.Handler | None = None,
    ) -> None:
        self._enabled = enabled
        self._logger = logging.getLogger(LOGGER_NAME)
        # Keep AI Effect debug verbosity isolated from root /
        # loomstroke.log — the user opted into telemetry, not into
        # full root debug.
        self._logger.propagate = False
        self._installed_handler: logging.Handler | None = None
        if not enabled:
            return
        if handler is None and log_path is not None:
            handler = RotatingFileHandler(
                log_path,
                maxBytes=5 * 1024 * 1024,  # 5 MB — matches loomstroke.log
                backupCount=2,
                encoding="utf-8",
            )
            handler.setFormatter(logging.Formatter("%(message)s"))
        if handler is not None:
            handler.setLevel(logging.DEBUG)
            self._logger.addHandler(handler)
            self._installed_handler = handler
        # Only lower the level if it is currently coarser; never raise
        # it (a host that already configured DEBUG keeps DEBUG).
        if self._logger.level == logging.NOTSET or self._logger.level > logging.DEBUG:
            self._logger.setLevel(logging.DEBUG)

    def log_generation(
        self,
        *,
        phase: str,
        layer_id: str,
        elapsed_ms: float,
        cache_hit: bool,
        gateway_id: str,
        **extra: Any,
    ) -> None:
        """Emit one JSON line summarizing a finished generation.

        ``phase`` is the ``GenerationOutcome.status`` value
        (``success`` / ``cache_hit`` / ``cancelled`` / ``error``) or
        ``cancelled_external`` when the outer Task was cancelled
        before the controller produced an outcome.
        """
        if not self._enabled:
            return
        payload: dict[str, Any] = {
            "phase": phase,
            "layer_id": layer_id,
            "elapsed_ms": round(elapsed_ms, 3),
            "cache_hit": cache_hit,
            "gateway_id": gateway_id,
        }
        if extra:
            payload.update(extra)
        self._logger.debug(json.dumps(payload, separators=(",", ":"), ensure_ascii=False))

    @property
    def enabled(self) -> bool:
        return self._enabled

    def close(self) -> None:
        """Detach the installed handler. Idempotent.

        Tests call this between cases to avoid handler leakage on the
        shared ``LOGGER_NAME`` logger; production code calls it on app
        shutdown. Safe to call when no handler is installed.
        """
        if self._installed_handler is None:
            return
        self._logger.removeHandler(self._installed_handler)
        with contextlib.suppress(Exception):  # pragma: no cover - best effort cleanup
            self._installed_handler.close()
        self._installed_handler = None

    @classmethod
    def from_preferences(
        cls,
        preferences: AIEffectPreferences,
        *,
        log_dir: Path | None = None,
    ) -> AIEffectTelemetry:
        """Build a sink whose enabled state follows
        ``preferences.developer.log_level == "debug"``.

        ``log_dir`` defaults to ``user_data/logs/``. Tests pass a
        ``tmp_path`` to keep the user data directory pristine.
        """
        enabled = (preferences.developer.log_level or "").strip().lower() == "debug"
        log_path: Path | None = None
        if enabled:
            if log_dir is None:
                from loomstroke.platform.user_dirs import (  # noqa: PLC0415
                    default_user_root,
                )

                log_dir = default_user_root() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "ai_effect.log"
        return cls(enabled=enabled, log_path=log_path)
