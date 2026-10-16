"""A frame vector raster in its retained source coordinate system."""

from dataclasses import dataclass

from loomstroke.core.command_types import LayerTransform
from loomstroke.core.vector_text_render_service import VectorRasterResult


@dataclass(frozen=True)
class FrameVectorRasterResult(VectorRasterResult):
    render_transform: LayerTransform | None = None
