"""Sam31SetupEngine: run the idempotent stage machine with progress + logging.

GUI-independent by contract: the Preferences page only renders the
``SetupProgress`` events and the final ``SetupReport``. Worker start and
health checks are injected callables, so unit tests never spawn processes.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..paths import Sam31Root
from ..runtime_config import Sam31RuntimeConfig
from .command_runner import StreamingCommandRunner
from .errors import ACTIONS, CANCELLED, Sam31SetupError
from .stages import SetupStage, StageContext, default_stages

STAGE_SKIPPED = "skipped"  # check() passed, nothing to do
STAGE_APPLIED = "applied"  # apply() ran and re-check passed
STAGE_FAILED = "failed"
STAGE_CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class SetupProgress:
    stage_index: int
    stage_count: int
    stage_name: str
    label: str
    message: str = ""


@dataclass(slots=True)
class SetupReport:
    ok: bool = False
    error_code: str | None = None
    error_message: str = ""
    error_action: str = ""
    failed_stage: str | None = None
    stages: list[tuple[str, str]] = field(default_factory=list)
    capabilities: dict[str, Any] | None = None
    elapsed_s: float = 0.0


ProgressCallback = Callable[[SetupProgress], None]


class Sam31SetupEngine:
    def __init__(
        self,
        config: Sam31RuntimeConfig,
        *,
        network_allowed: bool,
        worker_start: Callable[[], None] | None = None,
        worker_is_running: Callable[[], bool] | None = None,
        worker_health: Callable[[], dict[str, Any]] | None = None,
        worker_capabilities: Callable[[], dict[str, Any]] | None = None,
        runner: StreamingCommandRunner | None = None,
        stages: tuple[SetupStage, ...] | None = None,
    ) -> None:
        self._config = config
        self._network_allowed = network_allowed
        self._runner = runner or StreamingCommandRunner()
        self._stages = stages or default_stages()
        self._hooks = {
            "worker_start": worker_start,
            "worker_is_running": worker_is_running,
            "worker_health": worker_health,
            "worker_capabilities": worker_capabilities,
        }

    @classmethod
    def with_server_manager(
        cls,
        config: Sam31RuntimeConfig,
        *,
        network_allowed: bool,
        server_manager: Any,
        **kwargs: Any,
    ) -> Sam31SetupEngine:
        from ..server_manager import WORKER_RUNNING  # noqa: PLC0415

        return cls(
            config,
            network_allowed=network_allowed,
            worker_start=lambda: server_manager.start(config),
            worker_is_running=lambda: server_manager.worker_state() == WORKER_RUNNING,
            worker_health=server_manager.health,
            worker_capabilities=server_manager.capabilities,
            **kwargs,
        )

    def run(
        self,
        progress: ProgressCallback | None = None,
        cancel_event: threading.Event | None = None,
    ) -> SetupReport:
        report = SetupReport()
        started = time.time()
        root = self._config.root
        if root is None:
            report.error_code = "ROOT_INVALID"
            report.error_message = "SAM 3.1 root folder is not configured"
            report.error_action = ACTIONS.get("ROOT_INVALID", "")
            return report

        cancel_event = cancel_event or threading.Event()
        log_lines: list[str] = []
        log_file = self._open_log(root)

        def log(message: str) -> None:
            log_lines.append(message)
            if log_file is not None:
                try:
                    log_file.write(message + "\n")
                    log_file.flush()
                except OSError:
                    pass

        context = StageContext(
            root=root,
            config=self._config,
            runner=self._runner,
            log=log,
            cancel_event=cancel_event,
            network_allowed=self._network_allowed,
        )
        context.extra.update(
            {key: value for key, value in self._hooks.items() if value is not None}
        )

        try:
            count = len(self._stages)
            for index, stage in enumerate(self._stages):
                if cancel_event.is_set():
                    self._record_failure(
                        report,
                        stage,
                        Sam31SetupError(CANCELLED, "setup was cancelled"),
                        status=STAGE_CANCELLED,
                    )
                    return report
                self._emit(progress, index, count, stage, "checking")
                log(f"--- [{index + 1}/{count}] {stage.label} ({stage.name})")
                try:
                    if stage.check(context):
                        report.stages.append((stage.name, STAGE_SKIPPED))
                        log("    already satisfied")
                        continue
                    self._emit(progress, index, count, stage, "applying")
                    stage.apply(context)
                    if not stage.check(context):
                        raise Sam31SetupError(
                            (
                                "HEALTH_FAILED"
                                if stage.name == "health"
                                else (
                                    "WORKER_START_FAILED"
                                    if stage.name == "worker_start"
                                    else "PIP_FAILED"
                                )
                            ),
                            f"stage {stage.name} did not converge after apply",
                        )
                    report.stages.append((stage.name, STAGE_APPLIED))
                except Sam31SetupError as error:
                    self._record_failure(report, stage, error)
                    log(f"    FAILED [{error.code}]: {error}")
                    return report
            report.ok = True
            capabilities = context.extra.get("capabilities")
            if isinstance(capabilities, dict):
                report.capabilities = capabilities
            log("=== SAM 3.1 Ready")
            return report
        finally:
            report.elapsed_s = time.time() - started
            if log_file is not None:
                log_file.close()

    @staticmethod
    def _record_failure(
        report: SetupReport,
        stage: SetupStage,
        error: Sam31SetupError,
        *,
        status: str = STAGE_FAILED,
    ) -> None:
        report.error_code = error.code
        report.error_message = str(error)
        report.error_action = error.action
        report.failed_stage = stage.name
        report.stages.append((stage.name, status))

    @staticmethod
    def _open_log(root: Sam31Root):
        try:
            root.logs_dir.mkdir(parents=True, exist_ok=True)
            return open(root.setup_log_path, "a", encoding="utf-8")  # noqa: SIM115
        except OSError:
            return None

    @staticmethod
    def _emit(
        progress: ProgressCallback | None,
        index: int,
        count: int,
        stage: SetupStage,
        message: str,
    ) -> None:
        if progress is not None:
            progress(
                SetupProgress(
                    stage_index=index,
                    stage_count=count,
                    stage_name=stage.name,
                    label=stage.label,
                    message=message,
                )
            )


__all__ = [
    "STAGE_APPLIED",
    "STAGE_CANCELLED",
    "STAGE_FAILED",
    "STAGE_SKIPPED",
    "Sam31SetupEngine",
    "SetupProgress",
    "SetupReport",
]
