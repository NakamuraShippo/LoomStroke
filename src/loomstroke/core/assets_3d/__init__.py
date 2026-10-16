"""Provider-neutral 3D asset model and generated asset storage."""

from .file_io import (
    UsdzConverterUnavailable,
    export_glb,
    export_gltf,
    export_obj,
    import_glb,
    import_gltf,
    import_obj,
    load_scene,
    save_scene,
)
from .generated_store import Generated3DAssetStore, GeneratedAssetManifest
from .material import MaterialAsset
from .mesh import MeshAsset, MeshPrimitive, create_unit_cube_mesh
from .provider_contract import (
    Generated3DProviderOutput,
    ProviderAdapter,
    ProviderArtifact,
    mask_sensitive_metadata,
)
from .receiver import Generated3DAssetReceiver, Received3DScene
from .scene import Scene3DCamera, Scene3DDocument, Scene3DLight, Scene3DNode, Transform3D
from .texture import TextureAsset

__all__ = [
    "Generated3DAssetReceiver",
    "Generated3DAssetStore",
    "Generated3DProviderOutput",
    "GeneratedAssetManifest",
    "MaterialAsset",
    "MeshAsset",
    "MeshPrimitive",
    "ProviderAdapter",
    "ProviderArtifact",
    "Received3DScene",
    "Scene3DCamera",
    "Scene3DDocument",
    "Scene3DLight",
    "Scene3DNode",
    "TextureAsset",
    "Transform3D",
    "UsdzConverterUnavailable",
    "create_unit_cube_mesh",
    "export_glb",
    "export_gltf",
    "export_obj",
    "import_glb",
    "import_gltf",
    "import_obj",
    "load_scene",
    "mask_sensitive_metadata",
    "save_scene",
]
