"""Stable assist type identifiers (CT-14). Unknown types are preserved, never converted."""

from __future__ import annotations

from typing import Literal

__all__ = ["KNOWN_ASSIST_TYPES", "AssistType", "is_known_type"]

AssistType = Literal[
    "mirror", "rotational", "parallel", "radial", "concentric", "perspective", "ellipse"
]
KNOWN_ASSIST_TYPES: tuple[str, ...] = (
    "mirror",
    "rotational",
    "parallel",
    "radial",
    "concentric",
    "perspective",
    "ellipse",
)


def is_known_type(value: object) -> bool:
    return isinstance(value, str) and value in KNOWN_ASSIST_TYPES
