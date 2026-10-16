"""SAM31 root folder layout resolution (no copies of user checkpoints)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

CHECKPOINT_FILENAME = "sam3.1_multiplex.pt"
_CHECKPOINT_SUBDIRS = ("", "checkpoints", "model")


@dataclass(frozen=True, slots=True)
class Sam31Root:
    """Resolved layout under the user-chosen SAM31 root folder."""

    root: Path

    @property
    def runtime_dir(self) -> Path:
        return self.root / "runtime"

    @property
    def venv_dir(self) -> Path:
        return self.runtime_dir / "venv"

    @property
    def venv_python(self) -> Path:
        return self.venv_dir / "Scripts" / "python.exe"

    @property
    def sam3_src_dir(self) -> Path:
        """Optional offline copy of the facebookresearch/sam3 sources."""
        return self.runtime_dir / "sam3-src"

    @property
    def logs_dir(self) -> Path:
        return self.runtime_dir / "logs"

    @property
    def state_dir(self) -> Path:
        return self.runtime_dir / "state"

    @property
    def setup_log_path(self) -> Path:
        return self.logs_dir / "setup.log"

    @property
    def worker_log_path(self) -> Path:
        return self.logs_dir / "worker.log"

    def find_checkpoint(self) -> Path | None:
        """Locate the multiplex checkpoint without copying or moving it.

        The root itself may BE the Hugging Face clone (checkpoint at top
        level) or hold it under ``checkpoints``/``model``. The preferred
        filename wins; otherwise a single ``*.pt`` is accepted, and multiple
        candidates are ambiguous (return None — the caller reports it).
        """

        for subdir in _CHECKPOINT_SUBDIRS:
            directory = self.root / subdir if subdir else self.root
            if not directory.is_dir():
                continue
            preferred = directory / CHECKPOINT_FILENAME
            if preferred.is_file():
                return preferred
            candidates = sorted(directory.glob("*.pt"))
            if len(candidates) == 1:
                return candidates[0]
        return None


__all__ = ["CHECKPOINT_FILENAME", "Sam31Root"]
