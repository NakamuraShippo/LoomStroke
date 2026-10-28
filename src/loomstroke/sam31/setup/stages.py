"""Idempotent setup stages S1-S10 (check/apply pairs).

Every stage verifies real state in ``check()`` — nothing trusts a stored
"done" flag — and ``apply()`` only performs the missing work, so a failed
setup resumes at the failing stage instead of rebuilding everything.
"""

from __future__ import annotations

import json
import shlex
import sys
import threading
from dataclasses import dataclass, field
from typing import Any

from ..paths import Sam31Root
from ..runtime_config import Sam31RuntimeConfig
from . import errors
from .command_runner import RunOutcome, StreamingCommandRunner

_PIP_TIMEOUT_S = 3600.0
_SAM3_GITHUB_URL = "https://github.com/facebookresearch/sam3/archive/refs/heads/main.zip"
# Verified on real hardware 2026-08-09 (see SAM3_SAM31_Verification_Spike_Report):
# opencv/scipy are intentionally absent (they conflict with sam3's numpy<2 pin).
_BASE_DEPENDENCIES = ["einops", "pycocotools", "psutil", "pillow", "numpy<2"]
_WINDOWS_DEPENDENCIES = ["triton-windows"]
_TORCH_CUDA_INDEX = "https://download.pytorch.org/whl/cu130"
_TORCH_CPU_INDEX = "https://download.pytorch.org/whl/cpu"


@dataclass(slots=True)
class StageContext:
    root: Sam31Root
    config: Sam31RuntimeConfig
    runner: StreamingCommandRunner
    log: Any  # Callable[[str], None]
    cancel_event: threading.Event
    network_allowed: bool
    is_windows: bool = sys.platform == "win32"
    base_python: list[str] | None = None
    cuda_detected: bool | None = None
    smoke_failure: tuple[str, str] | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def venv(self, *arguments: str) -> list[str]:
        return [str(self.root.venv_python), *arguments]

    def run(self, command: list[str], *, timeout_s: float = 120.0) -> RunOutcome:
        self.log("$ " + shlex.join(command))
        return self.runner.run(
            command,
            on_line=self.log,
            timeout_s=timeout_s,
            cancel_event=self.cancel_event,
        )


class SetupStage:
    name = "stage"
    label = "Stage"

    def check(self, context: StageContext) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def apply(self, context: StageContext) -> None:  # pragma: no cover - interface
        raise NotImplementedError


def _python_version_ok(outcome: RunOutcome) -> bool:
    import re  # noqa: PLC0415

    match = re.search(r"Python\s+(\d+)\.(\d+)", outcome.tail)
    return (
        outcome.returncode == 0
        and match is not None
        and (int(match.group(1)), int(match.group(2))) >= (3, 12)
    )


class RootValidationStage(SetupStage):
    name = "root_validation"
    label = "Checking root folder"

    def check(self, context: StageContext) -> bool:
        root = context.root
        return all(
            directory.is_dir()
            for directory in (root.root, root.runtime_dir, root.logs_dir, root.state_dir)
        )

    def apply(self, context: StageContext) -> None:
        root = context.root
        if not root.root.is_dir():
            raise errors.Sam31SetupError(
                errors.ROOT_INVALID, f"root folder does not exist: {root.root}"
            )
        try:
            for directory in (root.runtime_dir, root.logs_dir, root.state_dir):
                directory.mkdir(parents=True, exist_ok=True)
            probe = root.state_dir / ".write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink()
        except OSError as exc:
            raise errors.Sam31SetupError(
                errors.ROOT_INVALID, f"root folder is not writable: {exc}"
            ) from exc


class PythonDiscoveryStage(SetupStage):
    name = "python_discovery"
    label = "Checking Python"

    _CANDIDATES = (
        ["py", "-3.13"],
        ["py", "-3.12"],
        ["py", "-3"],
        ["python"],
        ["python3"],
    )

    def check(self, context: StageContext) -> bool:
        if context.root.venv_python.is_file():
            outcome = context.run(context.venv("--version"))
            if _python_version_ok(outcome):
                return True
        return context.base_python is not None

    def apply(self, context: StageContext) -> None:
        for candidate in self._CANDIDATES:
            outcome = context.run([*candidate, "--version"])
            if _python_version_ok(outcome):
                context.base_python = list(candidate)
                context.log(f"base python: {shlex.join(candidate)}")
                return
        raise errors.Sam31SetupError(
            errors.PYTHON_UNSUPPORTED,
            "no Python 3.12+ interpreter was found on this machine",
        )


class VenvStage(SetupStage):
    name = "venv"
    label = "Creating environment"

    def check(self, context: StageContext) -> bool:
        if not context.root.venv_python.is_file():
            return False
        return _python_version_ok(context.run(context.venv("--version")))

    def apply(self, context: StageContext) -> None:
        if context.base_python is None:
            raise errors.Sam31SetupError(
                errors.PYTHON_UNSUPPORTED, "no base Python available for venv creation"
            )
        outcome = context.run(
            [*context.base_python, "-m", "venv", str(context.root.venv_dir)],
            timeout_s=600.0,
        )
        if outcome.returncode != 0:
            raise errors.Sam31SetupError(
                errors.VENV_CREATE_FAILED, f"venv creation failed:\n{outcome.tail}"
            )


class PipStage(SetupStage):
    name = "pip_bootstrap"
    label = "Preparing pip"

    def check(self, context: StageContext) -> bool:
        return context.run(context.venv("-m", "pip", "--version")).returncode == 0

    def apply(self, context: StageContext) -> None:
        outcome = context.run(context.venv("-m", "ensurepip", "--upgrade"), timeout_s=600.0)
        if outcome.returncode != 0:
            raise errors.Sam31SetupError(
                errors.PIP_FAILED, f"pip bootstrap failed:\n{outcome.tail}"
            )


class TorchStage(SetupStage):
    name = "torch"
    label = "Installing PyTorch"

    def check(self, context: StageContext) -> bool:
        outcome = context.run(
            context.venv(
                "-c",
                "import torch, torchvision; print('TORCH_OK', torch.__version__)",
            ),
            timeout_s=300.0,
        )
        return outcome.returncode == 0 and "TORCH_OK" in outcome.tail

    def apply(self, context: StageContext) -> None:
        if not context.network_allowed:
            raise errors.Sam31SetupError(
                errors.NETWORK_CONSENT_REQUIRED,
                "installing PyTorch requires network access",
            )
        if context.cuda_detected is None:
            probe = context.run(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
            context.cuda_detected = probe.returncode == 0
        index = _TORCH_CUDA_INDEX if context.cuda_detected else _TORCH_CPU_INDEX
        outcome = context.run(
            context.venv("-m", "pip", "install", "torch", "torchvision", "--index-url", index),
            timeout_s=_PIP_TIMEOUT_S,
        )
        if outcome.returncode != 0:
            raise errors.Sam31SetupError(
                errors.TORCH_INSTALL_FAILED,
                f"PyTorch installation failed:\n{outcome.tail}",
            )


class Sam3DependenciesStage(SetupStage):
    name = "sam3_dependencies"
    label = "Installing SAM 3.1"

    def _probe_code(self, context: StageContext) -> str:
        modules = [
            "sam3.model_builder",
            "einops",
            "pycocotools",
            "psutil",
            "PIL",
            "numpy",
        ]
        lines = [f"import {module}" for module in modules]
        if context.is_windows:
            lines.append("import triton")
        lines += [
            "assert numpy.__version__.startswith('1.'), numpy.__version__",
            "print('SAM3_DEPS_OK')",
        ]
        return "\n".join(lines)

    def check(self, context: StageContext) -> bool:
        outcome = context.run(context.venv("-c", self._probe_code(context)), timeout_s=300.0)
        return outcome.returncode == 0 and "SAM3_DEPS_OK" in outcome.tail

    def apply(self, context: StageContext) -> None:
        source_dir = context.root.sam3_src_dir
        offline = (source_dir / "pyproject.toml").is_file()
        if not context.network_allowed:
            # The engine skips apply() when check() confirms all packages.
            # Source files alone do not provide runtime or build dependencies.
            raise errors.Sam31SetupError(
                errors.NETWORK_CONSENT_REQUIRED,
                "installing missing SAM 3.1 packages requires external services;"
                " a local source copy does not supply runtime or build dependencies",
            )
        dependencies = list(_BASE_DEPENDENCIES)
        if context.is_windows:
            dependencies += _WINDOWS_DEPENDENCIES
        outcome = context.run(
            context.venv("-m", "pip", "install", *dependencies),
            timeout_s=_PIP_TIMEOUT_S,
        )
        if outcome.returncode != 0:
            raise errors.Sam31SetupError(
                errors.SAM31_INSTALL_FAILED,
                f"dependency installation failed:\n{outcome.tail}",
            )
        target = str(source_dir) if offline else _SAM3_GITHUB_URL
        outcome = context.run(
            context.venv("-m", "pip", "install", target), timeout_s=_PIP_TIMEOUT_S
        )
        if outcome.returncode != 0:
            raise errors.Sam31SetupError(
                errors.SAM31_INSTALL_FAILED,
                f"sam3 package installation failed:\n{outcome.tail}",
            )


class CheckpointStage(SetupStage):
    name = "checkpoint"
    label = "Validating checkpoint"

    def check(self, context: StageContext) -> bool:
        return context.root.find_checkpoint() is not None

    def apply(self, context: StageContext) -> None:
        raise errors.Sam31SetupError(
            errors.CHECKPOINT_MISSING,
            f"no SAM 3.1 checkpoint found under {context.root.root}",
        )


class SmokeStage(SetupStage):
    name = "smoke"
    label = "Testing runtime"

    def check(self, context: StageContext) -> bool:
        checkpoint = context.root.find_checkpoint()
        if checkpoint is None:
            context.smoke_failure = (errors.CHECKPOINT_MISSING, "checkpoint vanished")
            return False
        code = (
            "import json, torch, sam3.model_builder\n"
            f"ckpt = torch.load(r'{checkpoint}', map_location='cpu',"
            " weights_only=True, mmap=True)\n"
            "if 'model' in ckpt and isinstance(ckpt['model'], dict):\n"
            "    ckpt = ckpt['model']\n"
            "keys = list(ckpt)\n"
            "print(json.dumps({'cuda': torch.cuda.is_available(),"
            " 'cudaBuild': torch.version.cuda,"
            " 'detector': any(k.startswith('detector.') for k in keys),"
            " 'tracker': any(k.startswith('tracker.') for k in keys)}))"
        )
        outcome = context.run(context.venv("-c", code), timeout_s=600.0)
        if outcome.returncode != 0:
            context.smoke_failure = (
                errors.SAM31_INSTALL_FAILED,
                f"runtime smoke test failed:\n{outcome.tail}",
            )
            return False
        try:
            payload = json.loads(outcome.tail.splitlines()[-1])
        except (json.JSONDecodeError, IndexError):
            context.smoke_failure = (
                errors.SAM31_INSTALL_FAILED,
                f"smoke test produced no result:\n{outcome.tail}",
            )
            return False
        if not (payload.get("detector") and payload.get("tracker")):
            context.smoke_failure = (
                errors.CHECKPOINT_MISSING,
                "checkpoint is not a SAM 3.1 multiplex checkpoint"
                " (detector/tracker weights missing)",
            )
            return False
        if not payload.get("cuda"):
            code_name = (
                errors.CUDA_MISMATCH
                if context.cuda_detected and not payload.get("cudaBuild")
                else errors.CUDA_UNAVAILABLE
            )
            context.smoke_failure = (code_name, "CUDA is not available to PyTorch")
            return False
        context.smoke_failure = None
        return True

    def apply(self, context: StageContext) -> None:
        code, message = context.smoke_failure or (
            errors.SAM31_INSTALL_FAILED,
            "smoke test failed",
        )
        raise errors.Sam31SetupError(code, message)


class WorkerStartStage(SetupStage):
    name = "worker_start"
    label = "Starting worker"

    def check(self, context: StageContext) -> bool:
        is_running = context.extra.get("worker_is_running")
        return bool(is_running()) if callable(is_running) else False

    def apply(self, context: StageContext) -> None:
        start = context.extra.get("worker_start")
        if not callable(start):
            raise errors.Sam31SetupError(
                errors.WORKER_START_FAILED, "no worker starter was provided"
            )
        try:
            start()
        except Exception as exc:  # noqa: BLE001
            raise errors.Sam31SetupError(
                errors.WORKER_START_FAILED, f"worker start failed: {exc}"
            ) from exc


class HealthStage(SetupStage):
    name = "health"
    label = "Running health check"

    def check(self, context: StageContext) -> bool:
        health = context.extra.get("worker_health")
        capabilities = context.extra.get("worker_capabilities")
        if not callable(health):
            return False
        try:
            result = health()
        except Exception as exc:  # noqa: BLE001
            context.extra["health_error"] = str(exc)
            return False
        if result.get("status") != "ok":
            context.extra["health_error"] = str(result)
            return False
        if callable(capabilities):
            try:
                context.extra["capabilities"] = capabilities()
            except Exception as exc:  # noqa: BLE001
                context.extra["health_error"] = str(exc)
                return False
        return True

    def apply(self, context: StageContext) -> None:
        raise errors.Sam31SetupError(
            errors.HEALTH_FAILED,
            f"health check failed: {context.extra.get('health_error', 'unknown')}",
        )


def default_stages() -> tuple[SetupStage, ...]:
    return (
        RootValidationStage(),
        PythonDiscoveryStage(),
        VenvStage(),
        PipStage(),
        TorchStage(),
        Sam3DependenciesStage(),
        CheckpointStage(),
        SmokeStage(),
        WorkerStartStage(),
        HealthStage(),
    )


__all__ = ["SetupStage", "StageContext", "default_stages"]
