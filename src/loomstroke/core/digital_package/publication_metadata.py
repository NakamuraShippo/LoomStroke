"""Bibliographic metadata the user confirmed for publication (CT-26).

Nothing here is ever filled in from the OS account or an external
service (spec §Security); the identifier is either user supplied or a
locally generated ``urn:uuid`` the user asked for.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace

__all__ = ["PublicationMetadata"]

_MAX_FIELD = 2000


@dataclass(frozen=True, slots=True)
class PublicationMetadata:
    title: str
    language: str
    identifier: str
    creators: tuple[str, ...] = ()
    series: str | None = None
    volume: str | None = None
    description: str | None = None

    def __post_init__(self) -> None:
        for name in ("title", "language", "identifier"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        for name in ("title", "language", "identifier", "series", "volume", "description"):
            value = getattr(self, name)
            if value is not None and len(value) > _MAX_FIELD:
                raise ValueError(f"{name} is too long")
        if any(not creator.strip() for creator in self.creators):
            raise ValueError("creators must not be blank")
        if "\x00" in self.title:
            raise ValueError("title contains control characters")

    @staticmethod
    def generate_identifier(token: str | None = None) -> str:
        """Local ``urn:uuid:`` identifier (never fetched from a service)."""
        return f"urn:uuid:{token or uuid.uuid4()}"

    def with_identifier(self, identifier: str) -> PublicationMetadata:
        return replace(self, identifier=identifier)

    @property
    def creator_line(self) -> str:
        return ", ".join(creator.strip() for creator in self.creators if creator.strip())
