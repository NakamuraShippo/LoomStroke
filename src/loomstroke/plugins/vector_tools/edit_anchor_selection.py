from dataclasses import dataclass


@dataclass(frozen=True)
class _AnchorSelection:
    path_idx: int
    anchor_idx: int
