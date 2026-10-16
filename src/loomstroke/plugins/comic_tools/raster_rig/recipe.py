"""Compositing recipe + nested rig data model — BZ4R spike."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from .guide import AttachmentGuide
from .tail_rig import TailRig

RASTER_RIG_VERSION = 1


@dataclass
class CompositingRecipe:
    """Data-driven joint parameters (no per-style branches).

    * ``z_order``: ``tail_over`` | ``tail_under``
    * ``opening_enabled``: cut a LOCAL alpha mask into the body outline
      around the root (the body image itself stays complete, so moving the
      tail restores the old place automatically)
    * ``opening_extra_px``: half-arc = tail base half-width + this
    * ``opening_depth_px``: band thickness across the outline (both sides)
    * ``opening_feather_px``: soft edge of the mask
    * ``overlap_px``: how far the tail base is pushed INTO the body along
      the inward normal so the two overlap by a few pixels
    * ``base_hide_px``: alpha fade of the tail's own base rows (rest space)
    * ``warp_band_px``: reserved — local warp limited to the joint band
    """

    z_order: str = "tail_over"
    opening_enabled: bool = True
    opening_extra_px: float = 1.0
    opening_depth_px: float = 4.0
    opening_feather_px: float = 1.0
    overlap_px: float = 2.0
    base_hide_px: float = 0.0
    warp_band_px: float = 0.0
    align_ends: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompositingRecipe:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def propose_recipe(guide: AttachmentGuide, rig: TailRig) -> CompositingRecipe:
    """Initial recipe from measured facts (fill ratio, line width, drawn
    gap) — a parameterisation, not a style classifier. The user confirms it
    at registration."""

    outline_body = guide.fill_ratio < 0.5
    # a filled body has no meaningful "line width" (its ridge is the whole
    # shape): size the joint from the tail's own line width instead
    lw = max(guide.line_width, 1.5) if outline_body else max(rig.line_width, 1.5)
    if outline_body:
        return CompositingRecipe(
            z_order="tail_over",
            opening_enabled=True,
            opening_extra_px=max(0.5, lw * 0.25),
            opening_depth_px=lw * 1.5 + 1.0,
            opening_feather_px=max(1.0, lw * 0.35),
            overlap_px=max(2.0, lw * 1.0),
            base_hide_px=0.0,
        )
    # filled body: nothing to cut; the tail simply overlaps into the fill
    return CompositingRecipe(
        z_order="tail_over",
        opening_enabled=False,
        overlap_px=min(12.0, max(2.0, lw * 1.5)),
        base_hide_px=0.0,
    )


@dataclass
class TailPoseData:
    root_u: float
    tip: tuple[float, float]  # page px
    bend: tuple[float, float] | None = None
    detail: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_u": float(self.root_u),
            "tip": [float(v) for v in self.tip],
            "bend": [float(v) for v in self.bend] if self.bend is not None else None,
            "detail": bool(self.detail),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TailPoseData:
        bend = data.get("bend")
        return cls(
            root_u=float(data["root_u"]),
            tip=(float(data["tip"][0]), float(data["tip"][1])),
            bend=(float(bend[0]), float(bend[1])) if bend else None,
            detail=bool(data.get("detail", False)),
        )


@dataclass
class SourceRef:
    """Where the pristine RGBA lives (hidden child raster layer) + hash."""

    layer_id: str
    width: int
    height: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceRef:
        return cls(**data)


@dataclass
class RasterRigData:
    """Everything the renderer needs, nested under one ``renderer`` key."""

    body_source: SourceRef
    tail_source: SourceRef
    attachment_guide: AttachmentGuide
    tail_rig: TailRig
    attachment: TailPoseData
    compositing_recipe: CompositingRecipe
    registered_pose: TailPoseData | None = None
    type: str = "raster_rig"
    version: int = RASTER_RIG_VERSION
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "version": int(self.version),
            "body_source": self.body_source.to_dict(),
            "tail_source": self.tail_source.to_dict(),
            "attachment_guide": self.attachment_guide.to_dict(),
            "tail_rig": self.tail_rig.to_dict(),
            "attachment": self.attachment.to_dict(),
            "registered_pose": (
                self.registered_pose.to_dict() if self.registered_pose is not None else None
            ),
            "compositing_recipe": self.compositing_recipe.to_dict(),
            "extras": dict(self.extras),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RasterRigData:
        if data.get("type") != "raster_rig":
            raise ValueError("not a raster_rig renderer block")
        version = int(data.get("version", 1))
        if version > RASTER_RIG_VERSION:
            raise ValueError(f"unsupported raster_rig version {version}")
        reg = data.get("registered_pose")
        return cls(
            body_source=SourceRef.from_dict(data["body_source"]),
            tail_source=SourceRef.from_dict(data["tail_source"]),
            attachment_guide=AttachmentGuide.from_dict(data["attachment_guide"]),
            tail_rig=TailRig.from_dict(data["tail_rig"]),
            attachment=TailPoseData.from_dict(data["attachment"]),
            compositing_recipe=CompositingRecipe.from_dict(data["compositing_recipe"]),
            registered_pose=TailPoseData.from_dict(reg) if reg else None,
            version=version,
            extras=dict(data.get("extras", {})),
        )


__all__ = [
    "RASTER_RIG_VERSION",
    "CompositingRecipe",
    "RasterRigData",
    "SourceRef",
    "TailPoseData",
    "propose_recipe",
]
