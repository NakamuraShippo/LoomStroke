"""Common GPU failure policy for 2D and 3D consumers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GpuFailureDecision:
    """Policy decision returned to GPU consumers."""

    use_fallback: bool
    invalidate_device: bool = False
    warning: str | None = None


class GpuFailurePolicy:
    """Conservative policy that preserves wgpu fallback paths."""

    def __init__(
        self,
        *,
        readback_warning_ms: float = 100.0,
        repeated_failure_limit: int = 2,
    ) -> None:
        self.readback_warning_ms = float(readback_warning_ms)
        self.repeated_failure_limit = max(1, int(repeated_failure_limit))
        self._failure_count = 0

    def adapter_unavailable(self, message: str) -> GpuFailureDecision:
        self._failure_count += 1
        return GpuFailureDecision(
            use_fallback=True,
            invalidate_device=True,
            warning=f"GPU adapter unavailable: {message}",
        )

    def device_lost(self, message: str) -> GpuFailureDecision:
        self._failure_count += 1
        return GpuFailureDecision(
            use_fallback=True,
            invalidate_device=True,
            warning=f"GPU device lost: {message}",
        )

    def render_failed(self, message: str) -> GpuFailureDecision:
        self._failure_count += 1
        return GpuFailureDecision(
            use_fallback=self._failure_count >= self.repeated_failure_limit,
            invalidate_device=self._failure_count >= self.repeated_failure_limit,
            warning=f"GPU render failed: {message}",
        )

    def readback_completed(self, elapsed_ms: float) -> GpuFailureDecision:
        if elapsed_ms <= self.readback_warning_ms:
            return GpuFailureDecision(use_fallback=False)
        return GpuFailureDecision(
            use_fallback=False,
            warning=f"GPU readback is slow: {elapsed_ms:.1f} ms",
        )

    def reset_success(self) -> None:
        self._failure_count = 0
