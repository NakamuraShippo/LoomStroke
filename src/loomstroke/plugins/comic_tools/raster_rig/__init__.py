"""Phase BZ4R — Arbitrary Raster Balloon Rig (feasibility spike).

Isolated from the frozen Raster Preserve implementation. Registration takes
a complete BODY RGBA (no tail) and a separately drawn TAIL RGBA and derives:

* :class:`AttachmentGuide` — hidden control outline of the body (main
  perimeter, holes, decorations, ``u`` param, tangents/normals, allowed
  intervals) used ONLY for placement / hit-testing / attachment math.
* :class:`TailRig` — hidden internal rig of the tail (base left/right/centre,
  tip, spine, deform kind, rigid region) + a non-destructive deformer.
* :class:`CompositingRecipe` — data-driven joint parameters (z-order,
  local opening mask, base hide, overlap, feather).
* :class:`RigComposer` — non-destructive re-composition with dirty-rect
  updates; the source RGBA arrays are never modified.

Nothing here is wired to the UI, the service layer or ``BalloonRecord``.
"""

from .compose import RigComposer, TailPose
from .guide import AttachmentGuide, build_attachment_guide
from .opening import InkFillOpeningComposer, InkOpeningComposer
from .recipe import CompositingRecipe, RasterRigData
from .registration import RegistrationReport, register_raster_rig, rescale_rig
from .tail_rig import TailRig, build_tail_rig

__all__ = [
    "AttachmentGuide",
    "CompositingRecipe",
    "InkFillOpeningComposer",
    "InkOpeningComposer",
    "RasterRigData",
    "RegistrationReport",
    "RigComposer",
    "TailPose",
    "TailRig",
    "build_attachment_guide",
    "build_tail_rig",
    "register_raster_rig",
    "rescale_rig",
]
