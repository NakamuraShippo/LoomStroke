"""Shim registry: status model and one-call application of all known shims."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class CompatibilityStatus:
    """Status values a shim can report (string enum by convention)."""

    NATIVE = "native"  # upstream already behaves correctly; no shim applied
    SHIM_APPLIED = "shim_applied"  # known-broken pattern found and patched
    UNSUPPORTED_VERSION = "unsupported_version"  # unknown upstream code; fail-closed
    FAILED = "failed"  # shim application raised; fail-closed
    NOT_APPLICABLE = "not_applicable"  # e.g. Windows-only shim on Linux

    TERMINAL_BAD = frozenset({UNSUPPORTED_VERSION, FAILED})


@dataclass(frozen=True, slots=True)
class ShimResult:
    name: str
    status: str
    detail: str = ""

    @property
    def feature_available(self) -> bool:
        return self.status in (
            CompatibilityStatus.NATIVE,
            CompatibilityStatus.SHIM_APPLIED,
            CompatibilityStatus.NOT_APPLICABLE,
        )


@dataclass(frozen=True, slots=True)
class CompatibilityReport:
    """Aggregated shim outcomes plus upstream identity for diagnostics."""

    sam3_version: str
    sam3_source_dir: str
    results: tuple[ShimResult, ...] = field(default_factory=tuple)

    def status_of(self, name: str) -> str:
        for result in self.results:
            if result.name == name:
                return result.status
        return CompatibilityStatus.UNSUPPORTED_VERSION

    def as_payload(self) -> dict[str, Any]:
        return {
            "sam3Version": self.sam3_version,
            "sam3SourceDir": self.sam3_source_dir,
            "shims": {
                result.name: {"status": result.status, "detail": result.detail}
                for result in self.results
            },
        }


def _upstream_identity() -> tuple[str, str]:
    version = "unknown"
    source_dir = "unknown"
    try:
        import sam3  # noqa: PLC0415

        source_dir = str(getattr(sam3, "__file__", "unknown"))
        try:
            from importlib.metadata import version as pkg_version  # noqa: PLC0415

            version = pkg_version("sam3")
        except Exception:  # noqa: BLE001
            version = str(getattr(sam3, "__version__", "unknown"))
    except Exception:  # noqa: BLE001
        pass
    return version, source_dir


def apply_all_shims(predictor: Any | None = None) -> CompatibilityReport:
    """Apply every registered shim and return the aggregated report.

    Module-level shims (SDP fallback) are applied immediately; predictor
    instance shims are applied when *predictor* is provided. The call is
    idempotent — reapplying to an already-shimmed target is a no-op with
    the same status.
    """

    from . import multiplex_session, point_new_object, windows_sdp  # noqa: PLC0415

    version, source_dir = _upstream_identity()
    results = [windows_sdp.apply()]
    if predictor is not None:
        results.append(multiplex_session.apply(predictor))
        results.append(point_new_object.apply(predictor))
    return CompatibilityReport(
        sam3_version=version,
        sam3_source_dir=source_dir,
        results=tuple(results),
    )


__all__ = [
    "CompatibilityReport",
    "CompatibilityStatus",
    "ShimResult",
    "apply_all_shims",
]
