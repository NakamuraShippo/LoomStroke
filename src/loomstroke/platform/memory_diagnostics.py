"""Opt-in process memory diagnostics for beta dogfood and support runs."""

from __future__ import annotations

import ctypes
import gc
import json
import os
import sys
import time
import tracemalloc
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from loomstroke.platform.user_dirs import default_user_root

_TRUTHY = {"1", "true", "yes", "on"}
_SCHEMA_VERSION = 1


@dataclass(slots=True)
class ProcessMemorySnapshot:
    """Single process-memory sample.

    ``private_bytes`` is available on Windows through ``PROCESS_MEMORY_COUNTERS_EX``.
    Other platforms keep it ``None`` until a focused platform probe is added.
    """

    timestamp_monotonic_s: float
    timestamp_unix_s: float
    pid: int
    platform: str
    working_set_bytes: int | None
    peak_working_set_bytes: int | None
    private_bytes: int | None
    pagefile_bytes: int | None
    tracemalloc_current_bytes: int | None
    tracemalloc_peak_bytes: int | None
    gc_generation_counts: tuple[int, int, int]


class _ProcessMemoryCountersEx(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_ulong),
        ("PageFaultCount", ctypes.c_ulong),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def memory_diagnostics_enabled(env: Mapping[str, str] | None = None) -> bool:
    values = env if env is not None else os.environ
    return str(values.get("LOOMSTROKE_MEMORY_DIAGNOSTICS", "")).lower() in _TRUTHY


def sample_process_memory() -> ProcessMemorySnapshot:
    """Collect a best-effort process memory sample without third-party deps."""

    working_set: int | None = None
    peak_working_set: int | None = None
    private_bytes: int | None = None
    pagefile_bytes: int | None = None
    if sys.platform == "win32":
        counters = _windows_memory_counters()
        if counters is not None:
            working_set = int(counters.WorkingSetSize)
            peak_working_set = int(counters.PeakWorkingSetSize)
            private_bytes = int(counters.PrivateUsage)
            pagefile_bytes = int(counters.PagefileUsage)

    current_trace: int | None = None
    peak_trace: int | None = None
    if tracemalloc.is_tracing():
        current_trace, peak_trace = tracemalloc.get_traced_memory()

    gc_counts = gc.get_count()
    return ProcessMemorySnapshot(
        timestamp_monotonic_s=time.monotonic(),
        timestamp_unix_s=time.time(),
        pid=os.getpid(),
        platform=sys.platform,
        working_set_bytes=working_set,
        peak_working_set_bytes=peak_working_set,
        private_bytes=private_bytes,
        pagefile_bytes=pagefile_bytes,
        tracemalloc_current_bytes=current_trace,
        tracemalloc_peak_bytes=peak_trace,
        gc_generation_counts=(int(gc_counts[0]), int(gc_counts[1]), int(gc_counts[2])),
    )


def _windows_memory_counters() -> _ProcessMemoryCountersEx | None:
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        psapi.GetProcessMemoryInfo.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(_ProcessMemoryCountersEx),
            ctypes.c_ulong,
        ]
        psapi.GetProcessMemoryInfo.restype = ctypes.c_int
        counters = _ProcessMemoryCountersEx()
        counters.cb = ctypes.sizeof(_ProcessMemoryCountersEx)
        process_handle = kernel32.GetCurrentProcess()
        ok = psapi.GetProcessMemoryInfo(
            process_handle,
            ctypes.byref(counters),
            counters.cb,
        )
    except (AttributeError, OSError):
        return None
    return counters if ok else None


class MemoryDiagnosticsLogger:
    """Append throttled JSONL samples when explicitly enabled."""

    def __init__(
        self,
        *,
        enabled: bool,
        path: Path,
        interval_s: float = 5.0,
        enable_tracemalloc: bool = False,
    ) -> None:
        self.enabled = enabled
        self.path = path
        self.interval_s = max(0.0, float(interval_s))
        self._last_sample_monotonic: float | None = None
        if self.enabled and enable_tracemalloc and not tracemalloc.is_tracing():
            tracemalloc.start()

    @classmethod
    def from_env(
        cls,
        *,
        default_path: Path | None = None,
        env: Mapping[str, str] | None = None,
    ) -> MemoryDiagnosticsLogger:
        values = env if env is not None else os.environ
        path_text = values.get("LOOMSTROKE_MEMORY_DIAGNOSTICS_PATH")
        path = Path(path_text) if path_text else default_path or _default_log_path()
        interval_text = values.get("LOOMSTROKE_MEMORY_DIAGNOSTICS_INTERVAL_SEC", "5")
        try:
            interval_s = float(interval_text)
        except ValueError:
            interval_s = 5.0
        enable_tracemalloc = (
            str(values.get("LOOMSTROKE_MEMORY_DIAGNOSTICS_TRACEMALLOC", "")).lower() in _TRUTHY
        )
        return cls(
            enabled=memory_diagnostics_enabled(values),
            path=path,
            interval_s=interval_s,
            enable_tracemalloc=enable_tracemalloc,
        )

    def sample(
        self,
        *,
        phase: str,
        context: Mapping[str, Any] | None = None,
        force: bool = False,
    ) -> bool:
        if not self.enabled:
            return False
        snapshot = sample_process_memory()
        if (
            not force
            and self._last_sample_monotonic is not None
            and snapshot.timestamp_monotonic_s - self._last_sample_monotonic < self.interval_s
        ):
            return False

        payload = {
            "schema_version": _SCHEMA_VERSION,
            "phase": phase,
            "process": asdict(snapshot),
            "context": _json_safe(context or {}),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))
            handle.write("\n")
        self._last_sample_monotonic = snapshot.timestamp_monotonic_s
        return True


def _default_log_path() -> Path:
    return default_user_root(create=True) / "diagnostics" / "memory_diagnostics.jsonl"


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return str(value)
