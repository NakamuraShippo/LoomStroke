"""Advanced print document modes (CT-24 D1): mode / spot / overprint models, capability matrix,
conversion planning (dry run), mono1 bit packing, CPU separation / ink coverage oracle.

Pure Python + numpy, one class per module.  No storage format, GPU path or
conversion apply is touched here (those are D2+ slices with format migration)."""

from __future__ import annotations

from .document_color_mode import MODE_IDS, DocumentColorMode, ModeId
from .document_mode_planner import DocumentModePlanner
from .ink_coverage_calculator import PROCESS_PLATES, InkCoverageCalculator
from .mode_capability import ModeCapability
from .mode_capability_matrix import ModeCapabilityMatrix
from .mode_conversion_plan import ModeConversionPlan
from .mono1_bit_packer import Mono1BitPacker
from .overprint_attributes import OverprintAttributes
from .separation_snapshot import SeparationSnapshot
from .spot_ink_definition import SpotInkDefinition

__all__ = [
    "MODE_IDS",
    "PROCESS_PLATES",
    "DocumentColorMode",
    "DocumentModePlanner",
    "InkCoverageCalculator",
    "ModeCapability",
    "ModeCapabilityMatrix",
    "ModeConversionPlan",
    "ModeId",
    "Mono1BitPacker",
    "OverprintAttributes",
    "SeparationSnapshot",
    "SpotInkDefinition",
]
