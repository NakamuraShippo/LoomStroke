"""3D mesh asset model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass(frozen=True, slots=True)
class MeshPrimitive:
    """Indexed triangle primitive."""

    vertices: np.ndarray
    indices: np.ndarray
    normals: np.ndarray | None = None
    uvs: np.ndarray | None = None
    material_id: str | None = None
    segment_id: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "vertices", np.asarray(self.vertices, dtype=np.float32))
        object.__setattr__(self, "indices", np.asarray(self.indices, dtype=np.uint32))
        if self.normals is not None:
            object.__setattr__(self, "normals", np.asarray(self.normals, dtype=np.float32))
        if self.uvs is not None:
            object.__setattr__(self, "uvs", np.asarray(self.uvs, dtype=np.float32))


@dataclass(frozen=True, slots=True)
class MeshAsset:
    """A mesh with one or more primitives."""

    id: str
    name: str
    primitives: tuple[MeshPrimitive, ...]
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "primitives", tuple(self.primitives))

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        if not self.primitives:
            zero = np.zeros(3, dtype=np.float32)
            return zero, zero
        vertices = np.concatenate([primitive.vertices for primitive in self.primitives], axis=0)
        return vertices.min(axis=0), vertices.max(axis=0)


def create_unit_cube_mesh(*, mesh_id: str = "cube", material_id: str | None = None) -> MeshAsset:
    """Create a small cube used by tests and fallback preview displays."""

    vertices = np.array(
        [
            [-0.5, -0.5, -0.5],
            [0.5, -0.5, -0.5],
            [0.5, 0.5, -0.5],
            [-0.5, 0.5, -0.5],
            [-0.5, -0.5, 0.5],
            [0.5, -0.5, 0.5],
            [0.5, 0.5, 0.5],
            [-0.5, 0.5, 0.5],
        ],
        dtype=np.float32,
    )
    indices = np.array(
        [
            [0, 1, 2],
            [0, 2, 3],
            [4, 6, 5],
            [4, 7, 6],
            [0, 4, 5],
            [0, 5, 1],
            [1, 5, 6],
            [1, 6, 2],
            [2, 6, 7],
            [2, 7, 3],
            [3, 7, 4],
            [3, 4, 0],
        ],
        dtype=np.uint32,
    )
    normals = vertices.copy()
    norm = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, np.maximum(norm, 1e-6), dtype=np.float32)
    primitive = MeshPrimitive(
        vertices=vertices,
        indices=indices,
        normals=normals,
        material_id=material_id,
    )
    return MeshAsset(id=mesh_id, name="Unit Cube", primitives=(primitive,))
