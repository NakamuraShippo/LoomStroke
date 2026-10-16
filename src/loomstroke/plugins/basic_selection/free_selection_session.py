"""Transient geometry and operation captured for one free selection gesture."""

from __future__ import annotations

from dataclasses import dataclass, field

from loomstroke.core.commands import SelectionOp


@dataclass
class FreeSelectionSession:
    mode: str
    operation: SelectionOp
    points: list[tuple[float, float]] = field(default_factory=list)
    hover: tuple[float, float] | None = None
    square: bool = False
    center: bool = False
