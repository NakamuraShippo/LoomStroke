"""Compatibility layer for the official facebookresearch/sam3 runtime.

Every known defect of the official package that LoomStroke works around
lives here — never as an edit to the official sources. Each shim:

- verifies the exact known-broken source pattern before applying
  (version/SHA alone is not trusted),
- applies itself as an instance binding / module attribute monkeypatch
  (no checkpoint, state_dict, or architecture changes),
- reports one of the ``CompatibilityStatus`` values so ``capabilities``
  can distinguish native behaviour from shimmed behaviour,
- fails closed: an unrecognized upstream source means the shim is NOT
  applied and the dependent feature reports itself unavailable.
"""

from .registry import (
    CompatibilityReport,
    CompatibilityStatus,
    ShimResult,
    apply_all_shims,
)

__all__ = [
    "CompatibilityReport",
    "CompatibilityStatus",
    "ShimResult",
    "apply_all_shims",
]
