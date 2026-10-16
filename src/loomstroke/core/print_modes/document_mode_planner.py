"""Plans (never applies) a document mode conversion against the capability matrix (CT-24 D1)."""

from __future__ import annotations

from typing import ClassVar

from .document_color_mode import DocumentColorMode
from .mode_capability_matrix import ModeCapabilityMatrix
from .mode_conversion_plan import ModeConversionPlan

__all__ = ["DocumentModePlanner"]


class DocumentModePlanner:
    DEFAULT_MEMORY_BUDGET: ClassVar[int] = 2 * 1024 * 1024 * 1024
    _LOSSY: ClassVar[frozenset[tuple[str, str]]] = frozenset(
        {
            ("rgba8", "gray8"),
            ("rgba8", "mono1"),
            ("rgba16", "rgba8"),
            ("rgba16", "gray8"),
            ("rgba16", "mono1"),
            ("cmyk8", "rgba8"),
            ("cmyk8", "gray8"),
            ("cmyk8", "mono1"),
            ("gray8", "mono1"),
            ("rgba8", "cmyk8"),
            ("rgba8", "cmyk16"),
            ("cmyk16", "cmyk8"),
            ("cmyk16", "rgba8"),
        }
    )

    def __init__(
        self, matrix: ModeCapabilityMatrix | None = None, *, memory_budget: int | None = None
    ) -> None:
        self._matrix = matrix or ModeCapabilityMatrix()
        self._budget = int(memory_budget or self.DEFAULT_MEMORY_BUDGET)

    def plan(
        self,
        source: DocumentColorMode,
        target: DocumentColorMode,
        *,
        width: int,
        height: int,
        layer_count: int,
    ) -> ModeConversionPlan:
        blocked: list[str] = []
        warnings: list[str] = []
        capability = self._matrix.get(target.mode_id)
        if capability is None:
            blocked.append("mode_unknown")
        if target.requires_profile and not target.profile_ref:
            blocked.append("profile_required")
        if capability is not None and not capability.storage_supported:
            blocked.append("mode_storage_unsupported")
        if target.alpha_enabled and capability is not None and not capability.alpha_supported:
            blocked.append("alpha_unsupported_in_mode")
        estimate = self._matrix.estimate_bytes(target.mode_id, width, height, layer_count)
        if estimate > self._budget:
            blocked.append("memory_budget_exceeded")
        if source.mode_id == target.mode_id and source.profile_ref != target.profile_ref:
            warnings.append("profile_reassignment_only")
        lossy = (source.mode_id, target.mode_id) in self._LOSSY
        if lossy:
            warnings.append("conversion_lossy")
        if source == target:
            warnings.append("conversion_noop")
        return ModeConversionPlan(
            source,
            target,
            int(width),
            int(height),
            int(layer_count),
            estimate,
            tuple(blocked),
            tuple(warnings),
            lossy,
        )
