"""Custom selection envelope (CT-19 spec 19 §8.1).

JSON carried under ``application/x-loomstroke-selection+json``; the image
and mask travel as separate PNG parts (never base64 inside the JSON).
The envelope lists each part's MIME, byte length and SHA-256 so the
reader can refuse tampered or truncated data.  Allowlisted fields only —
no document path, layer name or other metadata.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, ClassVar

__all__ = ["ClipboardEnvelope"]


@dataclass(frozen=True, slots=True)
class ClipboardEnvelope:
    kind: str
    bounds: tuple[int, int, int, int]
    source_dpi: float | None = None
    canvas_size: tuple[int, int] | None = None
    parts: tuple[tuple[str, str, int, str], ...] = field(default_factory=tuple)
    # (part name, mime, length, sha256)
    schema_version: int = 1

    SCHEMA_MAJOR: ClassVar[int] = 1
    KINDS: ClassVar[tuple[str, ...]] = ("raster_selection",)
    MAX_JSON_BYTES: ClassVar[int] = 64 * 1024

    def part(self, name: str) -> tuple[str, int, str] | None:
        for part_name, mime, length, digest in self.parts:
            if part_name == name:
                return mime, length, digest
        return None

    def to_json(self) -> bytes:
        payload: dict[str, Any] = {
            "schemaVersion": self.schema_version,
            "kind": self.kind,
            "bounds": list(self.bounds),
            "parts": [
                {"name": name, "mime": mime, "length": length, "sha256": digest}
                for name, mime, length, digest in self.parts
            ],
        }
        if self.source_dpi is not None:
            payload["sourceDpi"] = float(self.source_dpi)
        if self.canvas_size is not None:
            payload["canvas"] = {"width": self.canvas_size[0], "height": self.canvas_size[1]}
        return json.dumps(payload, separators=(",", ":")).encode("utf-8")

    @staticmethod
    def _parse_bounds(bounds_raw: Any) -> tuple[int, int, int, int]:
        if not (isinstance(bounds_raw, list) and len(bounds_raw) == 4):
            raise ValueError("envelope_bounds_invalid")
        try:
            x, y, w, h = (int(v) for v in bounds_raw)
        except (TypeError, ValueError) as error:
            raise ValueError("envelope_bounds_invalid") from error
        if w <= 0 or h <= 0:
            raise ValueError("envelope_bounds_invalid")
        return (x, y, w, h)

    @staticmethod
    def _parse_parts(parts_raw: Any) -> tuple[tuple[str, str, int, str], ...]:
        if not isinstance(parts_raw, list):
            raise ValueError("envelope_parts_invalid")
        parts: list[tuple[str, str, int, str]] = []
        for entry in parts_raw:
            if not isinstance(entry, dict):
                raise ValueError("envelope_parts_invalid")
            name = str(entry.get("name", ""))
            mime = str(entry.get("mime", ""))
            length = entry.get("length")
            digest = str(entry.get("sha256", ""))
            if not name or not mime or not isinstance(length, int) or length < 0:
                raise ValueError("envelope_parts_invalid")
            if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("envelope_parts_invalid")
            parts.append((name, mime, length, digest))
        return tuple(parts)

    @classmethod
    def from_json(cls, raw: bytes) -> ClipboardEnvelope:
        """Strict parse; ``ValueError`` for anything outside the contract."""
        if len(raw) > cls.MAX_JSON_BYTES:
            raise ValueError("envelope_too_large")
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("envelope_invalid_json") from error
        if not isinstance(payload, dict):
            raise ValueError("envelope_not_object")
        version = payload.get("schemaVersion")
        if not isinstance(version, int) or version != cls.SCHEMA_MAJOR:
            raise ValueError("envelope_schema_unsupported")
        kind = str(payload.get("kind", ""))
        if kind not in cls.KINDS:
            raise ValueError("envelope_kind_unsupported")
        bounds = cls._parse_bounds(payload.get("bounds"))
        parts = cls._parse_parts(payload.get("parts", []))
        dpi_raw = payload.get("sourceDpi")
        dpi = float(dpi_raw) if isinstance(dpi_raw, (int, float)) and dpi_raw > 0 else None
        canvas_raw = payload.get("canvas")
        canvas_size = None
        if isinstance(canvas_raw, dict):
            try:
                canvas_size = (int(canvas_raw.get("width", 0)), int(canvas_raw.get("height", 0)))
            except (TypeError, ValueError):
                canvas_size = None
        return cls(
            kind=kind,
            bounds=bounds,
            source_dpi=dpi,
            canvas_size=canvas_size,
            parts=tuple(parts),
            schema_version=version,
        )
