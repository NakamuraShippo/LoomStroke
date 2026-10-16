"""Manga drawing assists (CT-14): models, validation, stroke constraint / symmetry, overlay.

Pure Python, one class per module.  Qt integration lives in
``loomstroke.ui.canvas.drawing_assist_controller``, tool adapters in
``loomstroke.plugins.sdk.drawing_assist``, the dock in ``loomstroke.plugins.drawing_assist``.
"""

from __future__ import annotations

from .assist_issue import AssistIssue
from .assist_overlay_geometry import AssistOverlayGeometry
from .assist_type import KNOWN_ASSIST_TYPES, AssistType, is_known_type
from .assisted_stroke import AssistedStroke
from .concentric_constraint import ConcentricConstraint
from .drawing_assist import DrawingAssist
from .drawing_assist_codec import DrawingAssistViewStateCodec
from .drawing_assist_state import DrawingAssistState
from .drawing_assist_validator import DrawingAssistValidator
from .mirror_geometry import MirrorGeometry
from .overlay_geometry_builder import OverlayGeometryBuilder
from .parallel_constraint import ParallelConstraint
from .perspective_constraint import PerspectiveConstraint
from .radial_constraint import RadialConstraint
from .rotational_geometry import RotationalGeometry
from .stroke_constraint_session import StrokeConstraintSession
from .stroke_expander import StrokeExpander

__all__ = [
    "KNOWN_ASSIST_TYPES",
    "AssistIssue",
    "AssistOverlayGeometry",
    "AssistType",
    "AssistedStroke",
    "ConcentricConstraint",
    "DrawingAssist",
    "DrawingAssistState",
    "DrawingAssistValidator",
    "DrawingAssistViewStateCodec",
    "MirrorGeometry",
    "OverlayGeometryBuilder",
    "ParallelConstraint",
    "PerspectiveConstraint",
    "RadialConstraint",
    "RotationalGeometry",
    "StrokeConstraintSession",
    "StrokeExpander",
    "is_known_type",
]
