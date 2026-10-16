"""Common data model for AI generation history records.

This module defines the shared data structure used by all AI generation
plugins (ComfyUI, SD WebUI API, Flux, etc.) to store generation history.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class GenerationRecord:
    """All-AI-plugin common generation history record."""

    # === Identity ===
    job_id: str
    service_id: str  # e.g. "comfyui", "sd_api"
    service_name: str  # e.g. "ComfyUI", "Stable Diffusion"
    status: str  # "completed" | "failed" | "cancelled"
    created_at: str  # ISO 8601
    completed_at: str | None = None  # ISO 8601 or None
    duration_sec: float | None = None

    # === Generation info (best-effort, nullable) ===
    prompt: str | None = None
    negative_prompt: str | None = None
    model_name: str | None = None
    seed: int | None = None
    width: int | None = None
    height: int | None = None

    # === Service-specific opaque snapshot ===
    service_snapshot: dict[str, Any] = field(default_factory=dict)

    # === Output ===
    output_images: list[dict[str, Any]] = field(default_factory=list)
    image_path: str | None = None  # relative path to original image (PNG)
    thumbnail_path: str | None = None  # relative path to thumbnail (WebP)
    error: str | None = None

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict."""
        return {
            "job_id": self.job_id,
            "service_id": self.service_id,
            "service_name": self.service_name,
            "status": self.status,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "duration_sec": self.duration_sec,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "model_name": self.model_name,
            "seed": self.seed,
            "width": self.width,
            "height": self.height,
            "service_snapshot": self.service_snapshot,
            "output_images": self.output_images,
            "image_path": self.image_path,
            "thumbnail_path": self.thumbnail_path,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GenerationRecord:
        """Deserialize from a dict."""
        return cls(
            job_id=data["job_id"],
            service_id=data["service_id"],
            service_name=data.get("service_name", ""),
            status=data.get("status", "completed"),
            created_at=data.get("created_at", ""),
            completed_at=data.get("completed_at"),
            duration_sec=data.get("duration_sec"),
            prompt=data.get("prompt"),
            negative_prompt=data.get("negative_prompt"),
            model_name=data.get("model_name"),
            seed=data.get("seed"),
            width=data.get("width"),
            height=data.get("height"),
            service_snapshot=data.get("service_snapshot", {}),
            output_images=data.get("output_images", []),
            image_path=data.get("image_path"),
            thumbnail_path=data.get("thumbnail_path"),
            error=data.get("error"),
        )
