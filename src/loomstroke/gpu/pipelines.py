"""Shared pipeline cache metadata."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GpuPipelineKey:
    """Stable cache key for render/compute pipelines."""

    name: str
    shader_hash: str
    target_format: str
    vertex_layout: str = ""
    flags: tuple[str, ...] = ()


class GpuPipelineLibrary:
    """Lazy pipeline cache shared by 2D and 3D consumers."""

    def __init__(self) -> None:
        self._pipelines: dict[GpuPipelineKey, Any] = {}

    def get_or_create(self, key: GpuPipelineKey, factory: Callable[[], Any]) -> Any:
        pipeline = self._pipelines.get(key)
        if pipeline is None:
            pipeline = factory()
            self._pipelines[key] = pipeline
        return pipeline

    def invalidate(self, key: GpuPipelineKey | None = None) -> None:
        if key is None:
            self._pipelines.clear()
        else:
            self._pipelines.pop(key, None)

    def __len__(self) -> int:
        return len(self._pipelines)
