"""Lineart vector correction geometry (CT-15): width profiles, outlines, simplify / smooth /
straighten / offset / split / trim.  Pure Python on neutral point tuples, one class per module."""

from __future__ import annotations

from .arc_length_table import ArcLengthTable
from .path_correction_result import PathCorrectionResult
from .path_offsetter import PathOffsetter
from .path_simplifier import PathSimplifier
from .path_smoother import PathSmoother
from .path_splitter import PathSplitter
from .path_straightener import PathStraightener
from .path_trimmer import PathTrimmer
from .segment_intersector import SegmentIntersection, SegmentIntersector
from .stroke_profile import StrokeProfile
from .variable_width_outline_builder import VariableWidthOutlineBuilder

__all__ = [
    "ArcLengthTable",
    "PathCorrectionResult",
    "PathOffsetter",
    "PathSimplifier",
    "PathSmoother",
    "PathSplitter",
    "PathStraightener",
    "PathTrimmer",
    "SegmentIntersection",
    "SegmentIntersector",
    "StrokeProfile",
    "VariableWidthOutlineBuilder",
]
