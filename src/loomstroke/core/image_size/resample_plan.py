"""Frozen plan for resampling one page / canvas (CT-13 spec §9)."""

from __future__ import annotations

from dataclasses import dataclass

from .image_size_target import ImageSizeTarget


@dataclass(frozen=True, slots=True)
class PageResamplePlan:
    """Source geometry plus the validated target and derived scale factors.

    Built by :class:`ImageSizePlanner.plan`; consumers never recompute the
    scale so that preview, apply and undo all agree on the same numbers.
    """

    source_size: tuple[int, int]
    source_dpi: int
    target: ImageSizeTarget
    scale: tuple[float, float]

    @property
    def size_changed(self) -> bool:
        return (self.target.width_px, self.target.height_px) != self.source_size

    @property
    def dpi_changed(self) -> bool:
        return int(self.target.dpi) != int(self.source_dpi)

    @property
    def is_noop(self) -> bool:
        return not self.size_changed and not self.dpi_changed
