"""Lifecycle facade over the SAM31 worker: start / stop / restart / health.

The user-facing name is "SAM 3.1 Local Server"; the implementation is the
stdio worker (no listen socket). ``Worker running`` and ``model loaded on
GPU`` are independent states — starting the worker never loads the model.
Fail-closed: the worker only starts when readiness reports no blockers.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from . import worker_protocol as protocol
from .readiness import Sam31Readiness, check_sam31_readiness
from .runtime_config import Sam31RuntimeConfig, loomstroke_src_root
from .worker_client import (
    Sam31WorkerClient,
    Sam31WorkerClientError,
    Sam31WorkerLaunchSpec,
)

logger = logging.getLogger("loomstroke.sam31.server")

WORKER_STOPPED = "stopped"
WORKER_RUNNING = "running"


class Sam31ServerError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class Sam31ServerManager:
    """Owns at most one worker client; safe to call from GUI threads."""

    def __init__(
        self,
        *,
        client_factory: Any | None = None,
        readiness_checker: Any | None = None,
    ) -> None:
        self._lock = threading.RLock()
        self._client: Sam31WorkerClient | None = None
        self._client_factory = client_factory or self._default_client_factory
        self._readiness_checker = readiness_checker or check_sam31_readiness
        self._last_model_state = protocol.MODEL_UNLOADED

    @staticmethod
    def _default_client_factory(config: Sam31RuntimeConfig) -> Sam31WorkerClient:
        root = config.root
        assert root is not None
        root.logs_dir.mkdir(parents=True, exist_ok=True)
        root.state_dir.mkdir(parents=True, exist_ok=True)
        spec = Sam31WorkerLaunchSpec(
            worker_python=root.venv_python,
            checkpoint_path=root.find_checkpoint(),
            state_dir=root.state_dir,
            log_file=root.worker_log_path,
            loomstroke_src_root=loomstroke_src_root(),
        )
        return Sam31WorkerClient(spec, request_timeout_s=config.request_timeout_s)

    # ── state ──────────────────────────────────────────────────────────

    def worker_state(self) -> str:
        with self._lock:
            client = self._client
            return WORKER_RUNNING if client is not None and client.is_running() else WORKER_STOPPED

    def last_model_state(self) -> str:
        return self._last_model_state

    def readiness(self, config: Sam31RuntimeConfig) -> Sam31Readiness:
        return self._readiness_checker(config)

    # ── lifecycle ──────────────────────────────────────────────────────

    def start(self, config: Sam31RuntimeConfig) -> dict[str, Any]:
        with self._lock:
            if self.worker_state() == WORKER_RUNNING:
                return self.health()
            readiness = self._readiness_checker(config)
            if not readiness.ready:
                raise Sam31ServerError(
                    readiness.blocked_reasons[0],
                    "SAM 3.1 runtime is not ready: " + ", ".join(readiness.blocked_reasons),
                )
            client = self._client_factory(config)
            client.start()
            self._client = client
            try:
                return self.health()
            except Sam31ServerError:
                self.stop()
                raise

    def stop(self) -> None:
        with self._lock:
            client = self._client
            self._client = None
            self._last_model_state = protocol.MODEL_UNLOADED
            if client is not None:
                client.stop()

    def restart(self, config: Sam31RuntimeConfig) -> dict[str, Any]:
        self.stop()
        return self.start(config)

    # ── worker requests ────────────────────────────────────────────────

    def _request(self, command: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        with self._lock:
            client = self._client
            if client is None or not client.is_running():
                raise Sam31ServerError("WORKER_NOT_RUNNING", "worker is not running")
            try:
                result = client.request(command, params)
            except Sam31WorkerClientError as exc:
                raise Sam31ServerError(exc.code, str(exc)) from exc
            state = result.get("modelState")
            if isinstance(state, str):
                self._last_model_state = state
            return result

    def health(self) -> dict[str, Any]:
        result = self._request(protocol.CMD_HEALTH)
        if result.get("status") != "ok":
            raise Sam31ServerError("HEALTH_FAILED", f"health check failed: {result}")
        return result

    def capabilities(self) -> dict[str, Any]:
        return self._request(protocol.CMD_CAPABILITIES)

    def runtime_info(self) -> dict[str, Any]:
        return self._request(protocol.CMD_RUNTIME_INFO)

    def load_model(self) -> dict[str, Any]:
        return self._request(protocol.CMD_LOAD_MODEL)

    def unload_model(self) -> dict[str, Any]:
        return self._request(protocol.CMD_UNLOAD_MODEL)

    def model_status(self) -> dict[str, Any]:
        return self._request(protocol.CMD_MODEL_STATUS)

    def segment(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        if command not in protocol.IMAGE_COMMANDS:
            raise Sam31ServerError("invalid_request", f"not an image command: {command}")
        return self._request(command, params)

    def video_request(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        if command not in protocol.VIDEO_COMMANDS:
            raise Sam31ServerError("invalid_request", f"not a video command: {command}")
        return self._request(command, params)

    @contextmanager
    def image_transaction(self) -> Iterator[None]:
        """Keep related image requests under the same lifecycle/request lock."""
        with self._lock:
            yield

    # ── app launch integration ─────────────────────────────────────────

    def auto_start_if_configured(self, config: Sam31RuntimeConfig) -> bool:
        """Best-effort worker start at app launch (never loads the model)."""

        if not config.auto_start:
            return False
        if self.worker_state() == WORKER_RUNNING:
            return True
        try:
            self.start(config)
            return True
        except Sam31ServerError as exc:
            logger.warning("sam31 auto-start skipped: %s (%s)", exc, exc.code)
            return False


_manager_lock = threading.Lock()
_manager: Sam31ServerManager | None = None


def get_sam31_server_manager() -> Sam31ServerManager:
    global _manager  # noqa: PLW0603 - process-wide singleton accessor
    with _manager_lock:
        if _manager is None:
            _manager = Sam31ServerManager()
        return _manager


__all__ = [
    "WORKER_RUNNING",
    "WORKER_STOPPED",
    "Sam31ServerError",
    "Sam31ServerManager",
    "get_sam31_server_manager",
]
