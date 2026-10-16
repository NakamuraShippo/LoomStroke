"""Generated 3D asset receiver."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .generated_store import Generated3DAssetStore, GeneratedAssetManifest
from .provider_contract import Generated3DProviderOutput, ProviderAdapter
from .scene import Scene3DDocument


@dataclass(frozen=True, slots=True)
class Received3DScene:
    """Stored generated asset plus the first loadable mesh scene."""

    manifest: GeneratedAssetManifest
    scene: Scene3DDocument
    artifact_relative_path: str


class Generated3DAssetReceiver:
    """Normalize provider responses and persist credential-free artifacts."""

    def __init__(
        self,
        *,
        store: Generated3DAssetStore | None = None,
        adapters: Mapping[str, ProviderAdapter] | None = None,
    ) -> None:
        self.store = store or Generated3DAssetStore()
        self._adapters: dict[str, ProviderAdapter] = dict(adapters or {})

    def register_adapter(self, adapter: ProviderAdapter) -> None:
        self._adapters[adapter.provider_id] = adapter

    def receive_response(
        self,
        provider_id: str,
        response: Mapping[str, Any],
    ) -> GeneratedAssetManifest:
        adapter = self._adapters.get(provider_id)
        if adapter is None:
            raise KeyError(f"3D provider adapter is not registered: {provider_id}")
        output = adapter.normalize_response(response)
        return self.receive_output(output)

    def receive_output(self, output: Generated3DProviderOutput) -> GeneratedAssetManifest:
        return self.store.register_output(output)

    def receive_scene_response(
        self,
        provider_id: str,
        response: Mapping[str, Any],
    ) -> Received3DScene:
        manifest = self.receive_response(provider_id, response)
        return self.scene_from_manifest(manifest)

    def receive_scene_output(self, output: Generated3DProviderOutput) -> Received3DScene:
        manifest = self.receive_output(output)
        return self.scene_from_manifest(manifest)

    def scene_from_manifest(self, manifest: GeneratedAssetManifest) -> Received3DScene:
        from .file_io import load_scene  # noqa: PLC0415

        for artifact in manifest.artifacts:
            if artifact.kind != "mesh" or not artifact.relative_path:
                continue
            scene_path = self.store.artifact_path(manifest.asset_id, artifact.relative_path)
            scene = load_scene(scene_path)
            return Received3DScene(
                manifest=manifest,
                scene=scene,
                artifact_relative_path=artifact.relative_path,
            )
        raise ValueError("generated 3D asset manifest does not contain a local mesh artifact")
