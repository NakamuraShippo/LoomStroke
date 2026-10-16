"""Attachment handling utilities for the LLM gateway plugin."""

from __future__ import annotations

import io
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

try:
    from PIL import Image  # type: ignore[import]  # optional dependency
except Exception:  # pragma: no cover - optional dependency
    Image = None  # type: ignore[assignment]  # optional dependency fallback

from .errors import AttachmentError
from .types import AttachmentRecord, ImageAttachment, LayerSnapshotResult

_SUPPORTED_MIME = {"image/png", "image/jpeg", "image/webp"}


def _sniff_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".png"}:
        return "image/png"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix in {".webp"}:
        return "image/webp"
    return "application/octet-stream"


@dataclass(slots=True)
class ImagePayloadBuilder:
    """Construct attachments from files or layer snapshots."""

    max_mb: int = 4

    def encode_file_drop(self, path: Path) -> ImageAttachment:
        if not path.exists() or not path.is_file():
            raise AttachmentError(f"attachment missing: {path}")
        mime = _sniff_mime(path)
        if mime not in _SUPPORTED_MIME:
            raise AttachmentError(f"unsupported mime: {mime}")
        data = path.read_bytes()
        if self._exceeds(data):
            raise AttachmentError("attachment exceeds size limit")
        width, height = self._probe_dimensions(path, data)
        attachment_id = f"llm-attach-{uuid4().hex}"
        return ImageAttachment(
            id=attachment_id,
            source="file",
            mime=mime,
            data=data,
            width=width,
            height=height,
            original_path=path,
        )

    def encode_layer_result(self, result: LayerSnapshotResult) -> ImageAttachment:
        if self._exceeds(result.data):
            raise AttachmentError("layer snapshot exceeds size limit")
        attachment_id = f"llm-attach-{uuid4().hex}"
        return ImageAttachment(
            id=attachment_id,
            source=result.source,
            mime=result.mime,
            data=result.data,
            width=result.width,
            height=result.height,
            layer=result.layer,
        )

    def _exceeds(self, data: bytes) -> bool:
        max_bytes = self.max_mb * 1024 * 1024
        return len(data) > max_bytes

    @staticmethod
    def _probe_dimensions(path: Path, data: bytes) -> tuple[int, int]:
        if not Image:
            return 0, 0
        try:
            if path.suffix.lower() == ".webp":
                img = Image.open(path)
            else:
                img = Image.open(io.BytesIO(data))
        except Exception:
            return 0, 0
        width, height = img.size
        img.close()
        return int(width), int(height)


@dataclass
class AttachmentStore:
    """Simple in-memory store for attachment records."""

    records: dict[str, AttachmentRecord]

    def __init__(self) -> None:
        self.records = {}

    def add(self, record: AttachmentRecord) -> None:
        self.records[record.attachment.id] = record

    def update_status(self, attachment_id: str, status: str, *, error: str | None = None) -> None:
        record = self.records.get(attachment_id)
        if not record:
            return
        record.status = status  # type: ignore[assignment]  # dataclass field str-typed
        record.error = error

    def remove(self, attachment_id: str) -> None:
        self.records.pop(attachment_id, None)

    def list_ready(self) -> list[ImageAttachment]:
        return [rec.attachment for rec in self.records.values() if rec.status == "ready"]

    def __iter__(self) -> Iterable[AttachmentRecord]:
        return iter(self.records.values())
