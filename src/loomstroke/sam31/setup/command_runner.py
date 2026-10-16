"""Streaming subprocess runner for setup stages (injectable for tests)."""

from __future__ import annotations

import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass

LineCallback = Callable[[str], None]


@dataclass(frozen=True, slots=True)
class RunOutcome:
    returncode: int
    tail: str


class StreamingCommandRunner:
    """Runs a command, streaming merged output line-by-line to a callback."""

    def run(
        self,
        command: list[str],
        *,
        on_line: LineCallback,
        timeout_s: float = 1800.0,
        cancel_event: threading.Event | None = None,
    ) -> RunOutcome:
        try:
            process = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            return RunOutcome(-1, f"{type(exc).__name__}: {exc}")

        tail: list[str] = []

        def _pump() -> None:
            assert process.stdout is not None
            for raw_line in process.stdout:
                line = raw_line.rstrip()
                tail.append(line)
                del tail[:-40]
                on_line(line)

        pump = threading.Thread(target=_pump, daemon=True)
        pump.start()
        waited = 0.0
        interval = 0.2
        while True:
            if process.poll() is not None:
                break
            if cancel_event is not None and cancel_event.is_set():
                process.kill()
                process.wait()
                return RunOutcome(-2, "cancelled")
            if waited >= timeout_s:
                process.kill()
                process.wait()
                return RunOutcome(-3, f"timed out after {timeout_s:.0f}s")
            threading.Event().wait(interval)
            waited += interval
        pump.join(timeout=5.0)
        return RunOutcome(process.returncode, "\n".join(tail[-15:]))


__all__ = ["LineCallback", "RunOutcome", "StreamingCommandRunner"]
