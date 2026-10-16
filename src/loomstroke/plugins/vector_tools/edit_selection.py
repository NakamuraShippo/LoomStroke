from dataclasses import dataclass


@dataclass
class _Selection:
    vec_id: str
    path_idx: int
    anchor_idx: int
    layer_id: str | None
    handle: str | None  # "in" | "out" | None
