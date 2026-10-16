"""Streaming SHA-256 of resource bytes (CT-18 §11: never load whole files to hash)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import ClassVar

__all__ = ["ResourceContentHasher"]


class ResourceContentHasher:
    PREFIX: ClassVar[str] = "sha256:"
    CHUNK_BYTES: ClassVar[int] = 1 << 20

    def hash_file(self, path: Path | str) -> str:
        """Raises ``OSError`` when the file cannot be read."""
        digest = hashlib.sha256()
        with Path(path).open("rb") as handle:
            while True:
                chunk = handle.read(self.CHUNK_BYTES)
                if not chunk:
                    break
                digest.update(chunk)
        return self.PREFIX + digest.hexdigest()

    def hash_bytes(self, data: bytes) -> str:
        return self.PREFIX + hashlib.sha256(data).hexdigest()

    @staticmethod
    def matches(expected: str, actual: str) -> bool:
        return bool(expected) and expected == actual
