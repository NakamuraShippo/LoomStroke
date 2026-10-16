"""Synchronous JSONL client for the SAM31 worker process.

The UI process never imports torch or sam3; it talks to the worker over
stdio only. Requests are serialized (one outstanding request at a time),
responses are matched by id, and stderr is retained for diagnostics.
The subprocess factory is injectable for tests.
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("loomstroke.sam31.client")

DEFAULT_REQUEST_TIMEOUT_S = 120.0
_MODEL_COMMAND_TIMEOUT_S = 600.0


class Sam31WorkerClientError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(slots=True)
class Sam31WorkerLaunchSpec:
    """Everything needed to spawn the worker inside the SAM31 venv."""

    worker_python: Path
    checkpoint_path: Path | None
    state_dir: Path | None
    log_file: Path | None
    loomstroke_src_root: Path
    extra_env: dict[str, str] = field(default_factory=dict)

    def command(self) -> list[str]:
        command = [
            str(self.worker_python),
            "-u",
            "-m",
            "loomstroke.sam31.worker_process",
        ]
        if self.checkpoint_path is not None:
            command += ["--checkpoint", str(self.checkpoint_path)]
        if self.state_dir is not None:
            command += ["--state-dir", str(self.state_dir)]
        if self.log_file is not None:
            command += ["--log-file", str(self.log_file)]
        return command

    def environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        source_root = str(self.loomstroke_src_root)
        existing = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = source_root + os.pathsep + existing if existing else source_root
        environment["PYTHONUNBUFFERED"] = "1"
        environment.update(self.extra_env)
        return environment


def _default_process_factory(spec: Sam31WorkerLaunchSpec) -> subprocess.Popen[str]:
    creation_flags = 0
    if sys.platform == "win32":  # no console window flash for the worker
        creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        spec.command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=spec.environment(),
        creationflags=creation_flags,
    )


class Sam31WorkerClient:
    """Owns the worker subprocess and the request/response channel."""

    def __init__(
        self,
        spec: Sam31WorkerLaunchSpec,
        *,
        request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S,
        process_factory: Callable[[Sam31WorkerLaunchSpec], Any] | None = None,
    ) -> None:
        self._spec = spec
        self._request_timeout_s = request_timeout_s
        self._process_factory = process_factory or _default_process_factory
        self._process: Any | None = None
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self._stderr_tail: deque[str] = deque(maxlen=60)
        self._request_lock = threading.Lock()
        self._next_id = 0

    # ── lifecycle ──────────────────────────────────────────────────────

    def start(self) -> None:
        if self.is_running():
            return
        self._responses = queue.Queue()
        self._stderr_tail.clear()
        self._process = self._process_factory(self._spec)
        threading.Thread(target=self._pump_stdout, name="sam31-stdout", daemon=True).start()
        threading.Thread(target=self._pump_stderr, name="sam31-stderr", daemon=True).start()

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def stop(self, *, graceful_timeout_s: float = 5.0) -> None:
        process = self._process
        self._process = None
        if process is None or process.poll() is not None:
            return
        try:
            self._write_line(process, {"id": -1, "cmd": "shutdown"})
            process.wait(timeout=graceful_timeout_s)
        except Exception:  # noqa: BLE001
            pass
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=graceful_timeout_s)
            except Exception:  # noqa: BLE001
                process.kill()

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr_tail)

    # ── requests ───────────────────────────────────────────────────────

    def request(
        self,
        command: str,
        params: dict[str, Any] | None = None,
        *,
        timeout_s: float | None = None,
    ) -> dict[str, Any]:
        if not self.is_running():
            raise Sam31WorkerClientError("worker_not_running", "worker is not running")
        if timeout_s is None:
            timeout_s = (
                _MODEL_COMMAND_TIMEOUT_S
                if command in ("load_model", "unload_model")
                else self._request_timeout_s
            )
        with self._request_lock:
            self._next_id += 1
            request_id = self._next_id
            payload = {"id": request_id, "cmd": command}
            if params:
                payload.update(params)
            process = self._process
            self._write_line(process, payload)
            while True:
                try:
                    response = self._responses.get(timeout=timeout_s)
                except queue.Empty:
                    raise Sam31WorkerClientError(
                        "worker_timeout",
                        f"{command} timed out after {timeout_s:.0f}s\n" f"{self.stderr_tail()}",
                    ) from None
                if response.get("_eof"):
                    raise Sam31WorkerClientError(
                        "worker_exited",
                        f"worker exited during {command}\n{self.stderr_tail()}",
                    )
                if response.get("id") != request_id:
                    continue  # stale response from an aborted request
                if response.get("ok"):
                    return response.get("result", {})
                error = response.get("error") or {}
                raise Sam31WorkerClientError(
                    str(error.get("code", "internal_error")),
                    str(error.get("message", "worker error")),
                )

    # ── internals ──────────────────────────────────────────────────────

    @staticmethod
    def _write_line(process: Any, payload: dict[str, Any]) -> None:
        try:
            process.stdin.write(json.dumps(payload) + "\n")
            process.stdin.flush()
        except Exception as exc:  # noqa: BLE001
            raise Sam31WorkerClientError(
                "worker_pipe_broken", f"failed to write to worker: {exc}"
            ) from exc

    def _pump_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for raw_line in process.stdout:
            line = raw_line.strip()
            if not line:
                continue
            try:
                self._responses.put(json.loads(line))
            except json.JSONDecodeError:
                logger.warning("sam31 worker emitted non-JSON stdout: %.200s", line)
        self._responses.put({"_eof": True})

    def _pump_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self._stderr_tail.append(line.rstrip())


__all__ = [
    "DEFAULT_REQUEST_TIMEOUT_S",
    "Sam31WorkerClient",
    "Sam31WorkerClientError",
    "Sam31WorkerLaunchSpec",
]
