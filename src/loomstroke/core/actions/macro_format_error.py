"""Raised when a macro JSON payload is malformed or forbidden (CT-20 §13)."""

from __future__ import annotations

__all__ = ["MacroFormatError"]


class MacroFormatError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code if not detail else f"{code}: {detail}")
        self.code = code
        self.detail = detail
