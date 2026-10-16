"""Optional external EPUBCheck (CT-26 spec §Fixed-Layout EPUB 3 / §Package validation).

EPUBCheck is neither bundled nor assumed: when the executable is not on
``PATH`` the result is ``not_checked`` and the UI must never display
"EPUB conformance checked".  When it is present the run is local,
network-free and bounded by a timeout; only a sanitized verdict comes
back (no paths from the tool's output are surfaced).
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar, Literal

__all__ = ["EpubCheckAdapter"]

Verdict = Literal["passed", "failed", "not_checked"]


class EpubCheckAdapter:
    EXECUTABLES: ClassVar[tuple[str, ...]] = ("epubcheck", "epubcheck.bat", "epubcheck.cmd")

    def __init__(
        self,
        *,
        which: Callable[[str], str | None] | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self._which = which or shutil.which
        self._runner = runner or subprocess.run

    def executable(self) -> str | None:
        for name in self.EXECUTABLES:
            found = self._which(name)
            if found:
                return found
        return None

    def available(self) -> bool:
        return self.executable() is not None

    def run(self, archive: Path, *, timeout: float = 120.0) -> tuple[Verdict, str]:
        """``(verdict, summary)``; ``summary`` is a short sanitized line."""
        executable = self.executable()
        if executable is None:
            return "not_checked", "epubcheck_not_found"
        try:
            completed = self._runner(
                [executable, str(archive), "--quiet"],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return "not_checked", "epubcheck_failed_to_run"
        if completed.returncode == 0:
            return "passed", "epubcheck_passed"
        errors = sum(
            1 for line in (completed.stdout + completed.stderr).splitlines() if "ERROR" in line
        )
        return "failed", f"epubcheck_errors:{errors}"
