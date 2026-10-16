"""Provider-neutral 3D scene graph model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .material import MaterialAsset
from .mesh import MeshAsset
from .texture import TextureAsset


@dataclass(frozen=True, slots=True)
class Transform3D:
    """Simple TRS transform stored in scene nodes."""

    translation: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_euler: tuple[float, float, float] = (0.0, 0.0, 0.0)
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0)


@dataclass(frozen=True, slots=True)
class Scene3DNode:
    """Scene graph node referencing a mesh."""

    id: str
    name: str
    mesh_id: str | None = None
    transform: Transform3D = field(default_factory=Transform3D)
    children: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Scene3DCamera:
    """Camera description used by preview and projection."""

    id: str = "camera"
    name: str = "Camera"
    position: tuple[float, float, float] = (0.0, 0.0, 4.0)
    target: tuple[float, float, float] = (0.0, 0.0, 0.0)
    fov_degrees: float = 45.0


@dataclass(frozen=True, slots=True)
class Scene3DLight:
    """Light description used by preview shading."""

    id: str = "key"
    name: str = "Key"
    kind: str = "directional"
    direction: tuple[float, float, float] = (-0.5, -0.75, -1.0)
    color: tuple[float, float, float] = (1.0, 1.0, 1.0)
    intensity: float = 1.0


@dataclass(frozen=True, slots=True)
class Scene3DDocument:
    """Complete 3D scene model retained outside ``.lstroke`` manifests."""

    id: str
    nodes: tuple[Scene3DNode, ...]
    meshes: tuple[MeshAsset, ...]
    materials: tuple[MaterialAsset, ...] = ()
    textures: tuple[TextureAsset, ...] = ()
    cameras: tuple[Scene3DCamera, ...] = (Scene3DCamera(),)
    lights: tuple[Scene3DLight, ...] = (Scene3DLight(),)
    root_node_ids: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "nodes", tuple(self.nodes))
        object.__setattr__(self, "meshes", tuple(self.meshes))
        object.__setattr__(self, "materials", tuple(self.materials))
        object.__setattr__(self, "textures", tuple(self.textures))
        object.__setattr__(self, "cameras", tuple(self.cameras))
        object.__setattr__(self, "lights", tuple(self.lights))

    def mesh_by_id(self, mesh_id: str) -> MeshAsset | None:
        return next((mesh for mesh in self.meshes if mesh.id == mesh_id), None)

    def material_by_id(self, material_id: str | None) -> MaterialAsset | None:
        if material_id is None:
            return None
        return next((material for material in self.materials if material.id == material_id), None)

    def bounds(self) -> tuple[np.ndarray, np.ndarray]:
        mesh_bounds = [mesh.bounds() for mesh in self.meshes if mesh.primitives]
        if not mesh_bounds:
            zero = np.zeros(3, dtype=np.float32)
            return zero, zero
        lows = np.stack([bounds[0] for bounds in mesh_bounds], axis=0)
        highs = np.stack([bounds[1] for bounds in mesh_bounds], axis=0)
        return lows.min(axis=0), highs.max(axis=0)
