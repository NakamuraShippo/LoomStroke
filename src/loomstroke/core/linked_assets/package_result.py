"""Outcome of writing a package (CT-18 §14: partial failure leaves no manifest)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

__all__ = ["PackageResult"]


@dataclass(frozen=True, slots=True)
class PackageResult:
    ok: bool
    destination: Path
    manifest_path: Path | None = None
    copied_count: int = 0
    omitted_count: int = 0
    document_hash: str = ""
    error_code: str = ""
    detail: str = ""

    @classmethod
    def failure(cls, destination: Path, error_code: str, detail: str = "") -> PackageResult:
        return cls(False, destination, error_code=error_code, detail=detail)
