"""Runtime configuration resolved from settings + the SAM31 root layout."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .paths import Sam31Root

SETTING_ROOT_DIR = "sam31/root_dir"
SETTING_DEVICE = "sam31/runtime/device"
SETTING_AUTO_START = "sam31/server/auto_start"
SETTING_REQUEST_TIMEOUT_S = "sam31/runtime/request_timeout_s"

DEFAULT_DEVICE = "auto"
DEFAULT_AUTO_START = False
DEFAULT_REQUEST_TIMEOUT_S = 120


@dataclass(frozen=True, slots=True)
class Sam31RuntimeConfig:
    root_dir: str
    device: str = DEFAULT_DEVICE
    auto_start: bool = DEFAULT_AUTO_START
    request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S

    @property
    def root(self) -> Sam31Root | None:
        return Sam31Root(Path(self.root_dir)) if self.root_dir else None


def build_sam31_runtime_config(settings: object) -> Sam31RuntimeConfig:
    def get(key: str, default: object) -> object:
        getter = getattr(settings, "get", None)
        return getter(key, default) if callable(getter) else default

    timeout = get(SETTING_REQUEST_TIMEOUT_S, DEFAULT_REQUEST_TIMEOUT_S)
    try:
        timeout_value = max(1.0, float(timeout))
    except (TypeError, ValueError):
        timeout_value = float(DEFAULT_REQUEST_TIMEOUT_S)
    return Sam31RuntimeConfig(
        root_dir=str(get(SETTING_ROOT_DIR, "") or ""),
        device=str(get(SETTING_DEVICE, DEFAULT_DEVICE) or DEFAULT_DEVICE),
        auto_start=bool(get(SETTING_AUTO_START, DEFAULT_AUTO_START)),
        request_timeout_s=timeout_value,
    )


def loomstroke_src_root() -> Path:
    """Repo/installation ``src`` root injected into the worker PYTHONPATH."""

    return Path(__file__).resolve().parents[2]


__all__ = [
    "DEFAULT_AUTO_START",
    "DEFAULT_DEVICE",
    "DEFAULT_REQUEST_TIMEOUT_S",
    "SETTING_AUTO_START",
    "SETTING_DEVICE",
    "SETTING_REQUEST_TIMEOUT_S",
    "SETTING_ROOT_DIR",
    "Sam31RuntimeConfig",
    "build_sam31_runtime_config",
    "loomstroke_src_root",
]
