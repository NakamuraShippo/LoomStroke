"""Naming templates for batch export (CT-10 Phase X1).

An allowlist parser — templates are never handed to ``str.format`` (spec
10 §7), so a malicious or broken template cannot reach arbitrary
attributes.  Two validity layers with different owners:

- **Template problems are rejected** (unknown token, stray brace, path
  separator, control character): the template author has to fix them.
- **Value problems are sanitized** (a page *named* ``a/b``, a folio with
  a colon): user data must not be able to break an export, so invalid
  filename characters in token values become ``_``, trailing dots/spaces
  are stripped, and a reserved device stem gets an ``_`` suffix.

Every destination name is rendered up front so internal collisions are
caught before anything is written; the comparison is case-insensitive
because the common target filesystems are.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "NamingContext",
    "NamingError",
    "detect_collisions",
    "render_export_names",
    "validate_template",
]

_TOKEN_PATTERN = re.compile(r"\{([a-z0-9_]*)\}")
_ALLOWED_TOKENS = (
    "index",
    "index0",
    "page_id",
    "page_name",
    "folio",
    "role",
    "document_name",
)
# Characters invalid in Windows file names (the strictest common target).
_INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED_STEMS = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{digit}" for digit in range(1, 10)}
    | {f"lpt{digit}" for digit in range(1, 10)}
)


class NamingError(ValueError):
    """A template the parser refuses (author error, never user data)."""


@dataclass(frozen=True, slots=True)
class NamingContext:
    """Token values for one export item.  ``index`` is 1-based logical."""

    index: int
    page_id: str
    page_name: str = ""
    folio: str | None = None
    role: str = "body"
    document_name: str = ""


def validate_template(template: str) -> None:
    """Reject unknown tokens, stray braces, separators, control chars."""
    if not template.strip():
        raise NamingError("template is empty")
    if _INVALID_NAME_CHARS.search(template):
        raise NamingError("template contains characters invalid in file names")
    stripped = _TOKEN_PATTERN.sub("", template)
    if "{" in stripped or "}" in stripped:
        raise NamingError("template has an unbalanced brace")
    for match in _TOKEN_PATTERN.finditer(template):
        token = match.group(1)
        if token not in _ALLOWED_TOKENS:
            raise NamingError(f"unknown naming token: {{{token}}}")


def render_export_names(
    template: str,
    items: Sequence[NamingContext],
    *,
    zero_pad: int = 0,
    extension: str = "png",
) -> tuple[str, ...]:
    """Render every destination file name (template validated first)."""
    validate_template(template)
    suffix = f".{extension.lstrip('.')}" if extension else ""
    return tuple(_render_one(template, item, zero_pad) + suffix for item in items)


def detect_collisions(names: Sequence[str]) -> tuple[str, ...]:
    """Names that appear more than once (case-insensitive), in order."""
    seen: dict[str, str] = {}
    collisions: list[str] = []
    for name in names:
        key = name.casefold()
        if key in seen:
            if seen[key] not in collisions:
                collisions.append(seen[key])
        else:
            seen[key] = name
    return tuple(collisions)


def _render_one(template: str, item: NamingContext, zero_pad: int) -> str:
    values = {
        "index": str(item.index).zfill(zero_pad),
        "index0": str(item.index - 1).zfill(zero_pad),
        "page_id": item.page_id,
        "page_name": item.page_name,
        "folio": item.folio or "",
        "role": item.role,
        "document_name": item.document_name,
    }

    def substitute(match: re.Match[str]) -> str:
        return values[match.group(1)]

    return _sanitize_name(_TOKEN_PATTERN.sub(substitute, template))


def _sanitize_name(name: str) -> str:
    """Make a rendered stem safe without failing the export (user data)."""
    cleaned = _INVALID_NAME_CHARS.sub("_", name).rstrip(" .")
    if not cleaned:
        cleaned = "_"
    if cleaned.casefold() in _RESERVED_STEMS:
        cleaned += "_"
    return cleaned
