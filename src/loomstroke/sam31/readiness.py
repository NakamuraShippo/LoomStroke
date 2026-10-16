"""Lightweight readiness verification for the SAM31 runtime.

``setup_done`` flags are never trusted: readiness is judged from the real
filesystem and interpreter every time (deep smoke tests belong to the setup
engine's S8 stage; this probe stays fast enough for UI status refreshes).
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .runtime_config import Sam31RuntimeConfig

MIN_WORKER_PYTHON = (3, 12)
_PYTHON_VERSION_PATTERN = re.compile(r"Python\s+(\d+)\.(\d+)\.(\d+)")

# blocked codes
ROOT_NOT_CONFIGURED = "ROOT_NOT_CONFIGURED"
ROOT_INVALID = "ROOT_INVALID"
SETUP_REQUIRED = "SETUP_REQUIRED"
PYTHON_UNSUPPORTED = "PYTHON_UNSUPPORTED"
CHECKPOINT_MISSING = "CHECKPOINT_MISSING"

CommandRunner = Callable[[list[str], float], "CommandResult"]


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


def _default_command_runner(command: list[str], timeout_s: float) -> CommandResult:
    try:
        completed = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout_s, check=False
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)
    except Exception as exc:  # noqa: BLE001
        return CommandResult(-1, "", f"{type(exc).__name__}: {exc}")


@dataclass(frozen=True, slots=True)
class Sam31Readiness:
    blocked_reasons: tuple[str, ...]
    diagnostics: dict[str, Any] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return not self.blocked_reasons


def check_sam31_readiness(
    config: Sam31RuntimeConfig,
    *,
    command_runner: CommandRunner | None = None,
    timeout_s: float = 10.0,
) -> Sam31Readiness:
    runner = command_runner or _default_command_runner
    blocked: list[str] = []
    diagnostics: dict[str, Any] = {}

    root = config.root
    if root is None:
        return Sam31Readiness((ROOT_NOT_CONFIGURED,), {})
    diagnostics["root"] = str(root.root)
    if not root.root.is_dir():
        return Sam31Readiness((ROOT_INVALID,), diagnostics)

    checkpoint = root.find_checkpoint()
    diagnostics["checkpoint"] = str(checkpoint) if checkpoint else None
    if checkpoint is None:
        blocked.append(CHECKPOINT_MISSING)

    python = root.venv_python
    diagnostics["venvPython"] = str(python)
    if not python.is_file():
        blocked.append(SETUP_REQUIRED)
    else:
        result = runner([str(python), "--version"], timeout_s)
        match = _PYTHON_VERSION_PATTERN.search(result.stdout or result.stderr or "")
        if result.returncode != 0 or match is None:
            blocked.append(SETUP_REQUIRED)
            diagnostics["venvPythonError"] = (result.stderr or result.stdout)[:300]
        else:
            version = tuple(int(part) for part in match.groups())
            diagnostics["venvPythonVersion"] = ".".join(str(v) for v in version)
            if version[:2] < MIN_WORKER_PYTHON:
                blocked.append(PYTHON_UNSUPPORTED)

    return Sam31Readiness(tuple(blocked), diagnostics)


__all__ = [
    "CHECKPOINT_MISSING",
    "MIN_WORKER_PYTHON",
    "PYTHON_UNSUPPORTED",
    "ROOT_INVALID",
    "ROOT_NOT_CONFIGURED",
    "SETUP_REQUIRED",
    "CommandResult",
    "Sam31Readiness",
    "check_sam31_readiness",
]
