"""Vectorized linear skinning into the existing native-wgpu 3D scene path."""

from __future__ import annotations

from functools import lru_cache

import numpy as np

from ..assets_3d.material import MaterialAsset
from ..assets_3d.mesh import MeshAsset, MeshPrimitive
from ..assets_3d.scene import Scene3DDocument, Scene3DNode
from .kinematics import world_matrices
from .model import FigurePose, load_asset


@lru_cache(maxsize=2)
def _inverse_bind(model: str) -> np.ndarray:
    return np.linalg.inv(load_asset(model).rest)


def build_scene(pose: FigurePose) -> Scene3DDocument:
    asset = load_asset(pose.model)
    transforms = (world_matrices(pose, asset) @ _inverse_bind(pose.model)).astype(np.float32)
    vertices = np.zeros_like(asset.vertices)
    normals = np.zeros_like(asset.normals)
    for influence in range(asset.joints.shape[1]):
        matrices = transforms[asset.joints[:, influence]]
        weight = asset.weights[:, influence, None]
        vertices += (
            np.einsum("nij,nj->ni", matrices[:, :3, :3], asset.vertices) + matrices[:, :3, 3]
        ) * weight
        normals += np.einsum("nij,nj->ni", matrices[:, :3, :3], asset.normals) * weight
    normals /= np.maximum(np.linalg.norm(normals, axis=1, keepdims=True), 1e-8)
    mesh = MeshAsset(
        "drawing-figure",
        pose.model,
        (MeshPrimitive(vertices, asset.indices, normals=normals, material_id="figure-clay"),),
    )
    node = Scene3DNode("drawing-figure", pose.model, mesh_id=mesh.id)
    return Scene3DDocument(
        f"drawing-figure-{pose.model}",
        (node,),
        (mesh,),
        materials=(MaterialAsset("figure-clay", "Clay", roughness=0.85),),
        root_node_ids=(node.id,),
    )
