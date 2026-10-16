"""Persist each AI Effect image through the host's generation history store."""

from __future__ import annotations

import copy
import logging
from typing import Any

from .core.generation_record import GenerationRecord
from .plugins.ai_effect.gallery_bridge import extract_ai_effect_payload, matches_param_hash
from .services.thumbnails import generate_gallery_thumbnail

logger = logging.getLogger(__name__)


class _AIEffectGalleryAdapter:
    """Bridge the generation controller's GalleryStore to the host store.

    The primary record retains its backend job ID and complete output metadata.
    Additional images get independent records/files, with their backend job ID
    and output index in the opaque AI Effect snapshot. Only the primary record
    participates in cache lookup; a sibling must never stand in for its image.
    Persistence failures are logged independently so other outputs can survive.
    """

    def __init__(self, store: Any) -> None:
        self._store = store

    def get_by_param_hash(self, param_hash: str) -> GenerationRecord | None:
        try:
            records = self._store.list_records()
        except Exception:
            logger.exception("GenerationHistoryStore.list_records failed")
            return None
        for record in records:
            payload = extract_ai_effect_payload(record)
            if payload is not None and payload.get("gallery_output_index", 0) != 0:
                continue
            if matches_param_hash(record, param_hash):
                return record
        return None

    def append(self, record: GenerationRecord, images: list[bytes]) -> None:
        if not images:
            self._append_image(record, None)
            return
        # add_record prepends: save in reverse so the primary remains first.
        # Build siblings before saving/mutating the primary's file paths.
        for index in range(len(images) - 1, -1, -1):
            output_record = record if index == 0 else self._sibling_record(record, index)
            self._append_image(output_record, images[index])

    @staticmethod
    def _sibling_record(record: GenerationRecord, index: int) -> GenerationRecord:
        sibling = copy.deepcopy(record)
        sibling.job_id = f"{record.job_id}__output_{index}"
        sibling.image_path = None
        sibling.thumbnail_path = None
        sibling.output_images = copy.deepcopy(record.output_images[index : index + 1])
        if sibling.output_images:
            output = sibling.output_images[0]
            sibling.width = output.get("width")
            sibling.height = output.get("height")
        else:
            sibling.width = sibling.height = None
        payload = sibling.service_snapshot.setdefault("ai_effect", {})
        payload["gateway_job_id"] = record.job_id
        payload["gallery_output_index"] = index
        return sibling

    def _append_image(self, record: GenerationRecord, image: bytes | None) -> None:
        try:
            if image is not None:
                record.image_path = self._store.save_image(record.job_id, image)
                # Thumbnails remain best-effort, matching the single-image path.
                try:
                    thumb_bytes = generate_gallery_thumbnail(image)
                except Exception:
                    logger.exception("generate_gallery_thumbnail failed for job=%s", record.job_id)
                    thumb_bytes = None
                if thumb_bytes:
                    try:
                        record.thumbnail_path = self._store.save_thumbnail(
                            record.job_id, thumb_bytes
                        )
                    except Exception:
                        logger.exception(
                            "GenerationHistoryStore.save_thumbnail failed for job=%s", record.job_id
                        )
            self._store.add_record(record)
        except Exception:
            logger.exception("GenerationHistoryStore append failed for job=%s", record.job_id)
