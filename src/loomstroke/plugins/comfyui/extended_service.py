"""Extended ComfyUI Service with legacy feature support.

This module extends the base ComfyUIService to include workflow management,
layer operations, and other features from the legacy implementation while
maintaining plugin SDK compliance.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import shutil
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import aiohttp

from loomstroke.core.events import Events, JobProgressEvent, JobResultEvent
from loomstroke.host_api import LayerRef
from loomstroke.platform.user_dirs import ensure_directory, plugin_data_dir
from loomstroke.plugins.comfyui.output_targets import CREATE_NEW_3D_LAYER_ID
from loomstroke.plugins.common.outbound_policy import ensure_network_services_consent
from loomstroke.plugins.sdk.base import JobResult

from .service import ComfyUIService
from .workflow_model import WorkflowModel

logger = logging.getLogger(__name__)

_MODEL_FILE_SUFFIXES = {".glb", ".gltf", ".obj"}
_MODEL_OUTPUT_KEYS = {
    "3d",
    "3d_model",
    "3d_models",
    "artifact",
    "artifacts",
    "file",
    "files",
    "glb",
    "gltf",
    "mesh",
    "meshes",
    "model",
    "model_file",
    "model_path",
    "models",
    "obj",
    "output",
    "outputs",
    "result",
}
_COMFY_OUTPUT_FOLDERS = {"input", "output", "temp"}


# Helper functions for async file I/O with fallback
async def _read_text_async(filepath: Path) -> str:
    """Read text file asynchronously with fallback to thread pool.

    Args:
        filepath: Path to file

    Returns:
        File content as string
    """
    try:
        import aiofiles  # type: ignore  # noqa: PLC0415

        async with aiofiles.open(filepath, encoding="utf-8") as f:
            return await f.read()
    except (ModuleNotFoundError, ImportError):
        # Fallback to thread pool executor for synchronous I/O
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, filepath.read_text, "utf-8")


async def _write_text_async(filepath: Path, content: str) -> None:
    """Write text file asynchronously with fallback to thread pool.

    Args:
        filepath: Path to file
        content: Content to write
    """
    try:
        import aiofiles  # type: ignore  # noqa: PLC0415

        async with aiofiles.open(filepath, "w", encoding="utf-8") as f:
            await f.write(content)
    except (ModuleNotFoundError, ImportError):
        # Fallback to thread pool executor for synchronous I/O
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, filepath.write_text, content, "utf-8")


async def _write_bytes_async(filepath: Path, content: bytes) -> None:
    """Write binary data asynchronously with a thread-pool fallback."""

    try:
        import aiofiles  # type: ignore  # noqa: PLC0415

        async with aiofiles.open(filepath, "wb") as f:
            await f.write(content)
    except (ModuleNotFoundError, ImportError):
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, filepath.write_bytes, content)


@dataclass
class ComfyUIObjectInfo:
    """ComfyUI node and model information cache."""

    nodes: dict[str, dict[str, Any]] = field(default_factory=dict)
    models: dict[str, list[str]] = field(default_factory=dict)
    last_updated: float | None = None


class ExtendedComfyUIService(ComfyUIService):
    """Extended ComfyUI service with workflow and layer management.

    This class extends the base ComfyUIService to provide:
    - Workflow file management (load/save)
    - Object info caching
    - Layer export/import operations
    - Job queue management
    """

    def __init__(self, event_bus=None, host_api=None):
        """Initialize extended service.

        Args:
            event_bus: Event bus for communication
            host_api: Host API for canvas/layer operations
        """
        super().__init__(event_bus)
        self.host_api = host_api
        self._object_info_cache = ComfyUIObjectInfo()
        self._workflow_cache: dict[str, WorkflowModel] = {}
        self._recent_workflows: list[Path] = []
        self._max_recent = 10
        self._output_targets: dict[str, dict[str, LayerRef]] = {}
        self._model_output_targets: dict[str, dict[str, LayerRef]] = {}
        self._output_tasks: dict[str, asyncio.Task[None]] = {}
        # Ensure plugin-local data roots exist under user_data/
        self._data_root = plugin_data_dir("comfyui")
        self._workflows_root = ensure_directory(self._data_root / "workflows")
        self._last_workflow_path: Path | None = None
        self._recent_workflows: list[Path] = self._load_recent_workflows()

    # --- Workflow Management ---

    def last_workflow_path(self) -> Path | None:
        """Return the last workflow path that was loaded/saved (under user_data)."""
        return self._last_workflow_path

    def list_available_workflows(self) -> list[Path]:
        """List all workflow files under the managed workflows directory."""
        workflows = sorted(self._workflows_root.glob("*.json"))
        return workflows

    async def load_workflow(self, filepath: Path) -> WorkflowModel:
        """Load workflow from JSON file.

        Args:
            filepath: Path to workflow JSON file

        Returns:
            Parsed workflow model

        Raises:
            FileNotFoundError: If workflow file doesn't exist
            ValueError: If workflow JSON is invalid
        """
        if not filepath.exists():
            raise FileNotFoundError(f"Workflow file not found: {filepath}")

        # Always copy external workflows into user_data/plugins/comfyui/workflows
        target_path = filepath
        try:
            if not filepath.resolve().is_relative_to(self._workflows_root.resolve()):
                dest = self._workflows_root / filepath.name
                if dest.exists() and dest.resolve() != filepath.resolve():
                    dest = (
                        self._workflows_root
                        / f"{filepath.stem}_{uuid.uuid4().hex[:4]}{filepath.suffix}"
                    )
                try:
                    shutil.copyfile(filepath, dest)
                    target_path = dest
                    logger.info("Copied workflow into user_data: %s", dest)
                except Exception as copy_exc:
                    logger.warning("Failed to copy workflow into user_data: %s", copy_exc)
            else:
                target_path = filepath
        except Exception:
            target_path = filepath

        try:
            # Read file using helper with fallback
            json_content = await _read_text_async(target_path)
            data = json.loads(json_content)

            workflow = WorkflowModel.from_json(data)

            # Add to recent workflows
            self._add_recent_workflow(target_path)

            # Cache the workflow
            self._workflow_cache[str(target_path)] = workflow
            self._last_workflow_path = target_path

            logger.info(f"Loaded workflow: {target_path}")
            return workflow

        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid workflow JSON: {e}")  # noqa: B904

    async def save_workflow(self, workflow: WorkflowModel, filepath: Path) -> None:
        """Save workflow to JSON file.

        Args:
            workflow: Workflow model to save
            filepath: Target file path
        """
        # Ensure parent directory exists
        filepath.parent.mkdir(parents=True, exist_ok=True)

        # Convert to JSON
        data = workflow.to_json()
        json_content = json.dumps(data, indent=2)

        # Write file using helper with fallback
        await _write_text_async(filepath, json_content)

        # Update cache
        self._workflow_cache[str(filepath)] = workflow

        # Add to recent
        self._add_recent_workflow(filepath)
        self._last_workflow_path = filepath

        logger.info(f"Saved workflow: {filepath}")

    def get_recent_workflows(self) -> list[Path]:
        """Get list of recently used workflows.

        Returns:
            List of workflow file paths
        """
        # Filter out non-existent files
        self._recent_workflows = [p for p in self._recent_workflows if p.exists()]
        self._persist_recent_workflows()
        return self._recent_workflows.copy()

    # --- Recent workflow helpers ---

    def _add_recent_workflow(self, path: Path) -> None:
        """Insert a workflow path into recents and persist."""
        try:
            resolved = path.resolve()
        except Exception:
            resolved = path
        # Remove duplicates
        cleaned: list[Path] = []
        for p in self._recent_workflows:
            try:
                if p.resolve() == resolved:
                    continue
            except Exception:
                continue
            cleaned.append(p)
        self._recent_workflows = cleaned
        self._recent_workflows.insert(0, path)
        self._recent_workflows = self._recent_workflows[: self._max_recent]
        self._persist_recent_workflows()

    def _persist_recent_workflows(self) -> None:
        """Persist recent workflow paths to settings."""
        try:
            payload = [str(p) for p in self._recent_workflows if p.exists()]
            self._settings.set("comfyui/recent_workflows", payload)
        except Exception:
            logger.debug("Failed to persist recent workflows", exc_info=True)

    def _load_recent_workflows(self) -> list[Path]:
        """Load recent workflow paths from settings."""
        try:
            raw = self._settings.get("comfyui/recent_workflows", []) or []
            paths: list[Path] = []
            for entry in raw if isinstance(raw, list) else []:
                try:
                    p = Path(str(entry))
                except Exception:
                    continue
                if p.exists():
                    paths.append(p)
            return paths[: self._max_recent]
        except Exception:
            logger.debug("Failed to load recent workflows; starting empty", exc_info=True)
            return []

    # --- Object Info Management ---

    async def get_object_info(self, force_refresh: bool = False) -> dict[str, Any]:
        """Get ComfyUI node and model information.

        Args:
            force_refresh: Force refresh from server

        Returns:
            Object info dictionary with nodes and models
        """
        if not ensure_network_services_consent(self._settings, service_name="ComfyUI"):
            return {"nodes": {}, "models": {}}
        from .server_registry import get_registry  # noqa: PLC0415
        from .session_target import bind_service  # noqa: PLC0415

        await bind_service(self, get_registry())
        if not force_refresh and self._object_info_cache.nodes:
            return {
                "nodes": self._object_info_cache.nodes,
                "models": self._object_info_cache.models,
            }

        await self.initialize()

        if self._connection:
            # Fetch object info from ComfyUI
            info = await self._connection.get_object_info()

            if info:
                # ComfyUI returns nodes directly at root level
                self._object_info_cache.nodes = info

                # Try to fetch models separately
                try:
                    models = await self._connection.get_models()
                    if models:
                        self._object_info_cache.models = models
                        logger.info(f"Fetched models: {list(models.keys())}")
                except Exception as e:
                    logger.debug(f"Could not fetch models: {e}")
                    self._object_info_cache.models = {}

                import time  # noqa: PLC0415

                self._object_info_cache.last_updated = time.time()

                # Log cache statistics for debugging
                node_count = len(info) if isinstance(info, dict) else 0
                logger.info(f"Updated object info cache with {node_count} node types")

        return {"nodes": self._object_info_cache.nodes, "models": self._object_info_cache.models}

    def get_node_schema(self, node_type: str) -> dict[str, Any] | None:
        """Get schema for a specific node type.

        Args:
            node_type: ComfyUI node type (e.g., "KSampler")

        Returns:
            Node schema or None if not found
        """
        return self._object_info_cache.nodes.get(node_type)

    # --- Layer Operations ---

    async def export_layer(self, layer_ref: LayerRef) -> bytes:
        """Export layer as PNG image data.

        Args:
            layer_ref: Reference to layer to export

        Returns:
            PNG image data as bytes
        """
        if not self.host_api:
            raise RuntimeError("Host API not available for layer operations")

        # Use host API to get layer data
        # This would need to be implemented through commands
        # For now, return placeholder
        logger.warning("Layer export not yet implemented through command pattern")
        return b""

    async def import_to_layer(
        self, image_data: bytes, layer_ref: LayerRef | None = None
    ) -> LayerRef:
        """Import image data to a layer.

        Args:
            image_data: PNG image data
            layer_ref: Optional target layer (creates new if None)

        Returns:
            Reference to the layer with imported image
        """
        if not self.host_api:
            raise RuntimeError("Host API not available for layer operations")

        # This would need to be implemented through commands
        # For now, return placeholder
        logger.warning("Layer import not yet implemented through command pattern")

        if layer_ref:
            return layer_ref
        else:
            # Would create new layer through host API
            return LayerRef(id="placeholder", name="ComfyUI Result")

    # --- Workflow Validation ---

    async def validate_workflow(self, workflow: WorkflowModel) -> dict[str, Any]:
        """Validate workflow against ComfyUI node schemas.

        Args:
            workflow: Workflow to validate

        Returns:
            Validation result with errors and warnings
        """
        errors = []
        warnings = []

        # Get object info if not cached
        await self.get_object_info()

        # Check each node
        for node_id, node in workflow.nodes.items():
            schema = self.get_node_schema(node.type)

            if not schema:
                errors.append(f"Unknown node type: {node.type} (id: {node_id})")
                continue

            # Validate required inputs
            required_inputs = schema.get("input", {}).get("required", {})
            for input_name, _input_spec in required_inputs.items():
                if input_name not in node.inputs:
                    errors.append(f"Missing required input '{input_name}' in node {node_id}")

        # Check links
        for link in workflow.links:
            if link.source_node not in workflow.nodes:
                errors.append(f"Link references unknown source node: {link.source_node}")
            if link.target_node not in workflow.nodes:
                errors.append(f"Link references unknown target node: {link.target_node}")

        return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}

    # --- Job Management Extensions ---

    async def submit_workflow(  # noqa: PLR0912
        self,
        workflow: WorkflowModel,
        client_id: str | None = None,
        image_parameters: dict[str, Any] | None = None,
        output_targets: dict[str, LayerRef] | None = None,
        model_output_targets: dict[str, LayerRef] | None = None,
        metadata_bindings: dict[str, Any] | None = None,
    ) -> str:
        """Submit a workflow for execution with IMAGE parameter processing.

        Args:
            workflow: Workflow to execute
            client_id: Optional client ID for WebSocket updates
            image_parameters: Optional dict of node_id.param_name to ImageParameterWidget
            output_targets: Optional SaveImage node_id to target layer mapping
            model_output_targets: Optional Save 3D Model node_id to target layer mapping

        Returns:
            Job ID for tracking
        """
        if not ensure_network_services_consent(self._settings, service_name="ComfyUI"):
            raise RuntimeError(
                "ComfyUI access is disabled. "
                "Set 'privacy/allow_external_services' to true to enable."
            )
        # Validate workflow first
        validation = await self.validate_workflow(workflow)
        if not validation["valid"]:
            raise ValueError(f"Invalid workflow: {validation['errors']}")

        # Process IMAGE parameters if provided
        if image_parameters:
            await self._process_image_parameters(workflow, image_parameters)

        # Resolve metadata bindings (e.g. layer bounds → INT values)
        if metadata_bindings:
            self._resolve_metadata_bindings(workflow, metadata_bindings)

        # Convert to API format
        api_workflow = workflow.to_api_format()

        # Check LoadImage nodes for layer references vs file paths
        for node_id, node_data in api_workflow.items():
            if node_data.get("class_type") == "LoadImage" and "image" in node_data.get(
                "inputs", {}
            ):
                image_path = node_data["inputs"]["image"]
                # Check if it's a layer reference (starts with "layer_")
                if isinstance(image_path, str) and image_path.startswith("layer_"):
                    logger.info(f"Layer reference detected in node {node_id}: {image_path}")
                    # In the future, this will trigger layer export
                    # For now, we'll let it pass through
                elif (
                    isinstance(image_path, str)
                    and not image_path.startswith("/")
                    and not image_path.startswith("C:\\")
                ):
                    # This is likely a ComfyUI managed file (relative path)
                    logger.debug(f"ComfyUI file reference in node {node_id}: {image_path}")
                    # ComfyUI will handle this

        # Submit directly to ComfyUI
        await self.initialize()

        if not self._connection:
            raise RuntimeError("Not connected to ComfyUI")

        # Check connection health before submitting
        try:
            if not self._connection.is_connected():
                logger.warning("Connection lost, attempting to reconnect...")
                await self._connection.connect()
                if not self._connection.is_connected():
                    raise RuntimeError("Failed to reconnect to ComfyUI")
        except Exception as e:
            logger.error(f"Connection check failed: {e}")
            raise RuntimeError(f"ComfyUI connection error: {e}")  # noqa: B904

        # Submit the workflow
        try:
            prompt_id = await self._connection.queue_prompt(api_workflow, client_id)
            if not prompt_id:
                raise RuntimeError("Failed to queue workflow")

            # Keep prompt/job id mapping clear for result callbacks.
            self._job_prompt_ids[prompt_id] = prompt_id
            if prompt_id and (output_targets or model_output_targets):
                self._schedule_output_import(
                    prompt_id,
                    output_targets or {},
                    model_output_targets or {},
                )

            # Start progress/result streaming so preview panel and UI can update in real time.
            streaming_task = asyncio.create_task(self._stream_job(prompt_id, prompt_id))
            self._active_jobs[prompt_id] = streaming_task

            return prompt_id
        except aiohttp.ClientConnectionError as e:
            logger.error(f"Connection error during submission: {e}")
            raise RuntimeError("Lost connection to ComfyUI during workflow submission") from e
        except Exception as e:
            logger.error(f"Failed to submit workflow: {e}")
            raise RuntimeError(f"Workflow submission failed: {e}") from e

    def _schedule_output_import(
        self,
        job_id: str,
        targets: dict[str, LayerRef],
        model_targets: dict[str, LayerRef] | None = None,
    ) -> None:
        """Schedule monitoring for SaveImage/Save 3D Model outputs mapped to layers."""
        filtered: dict[str, LayerRef] = {}
        for node_id, layer in targets.items():
            if isinstance(layer, LayerRef) and layer.id:
                filtered[node_id] = LayerRef(
                    id=layer.id,
                    name=layer.name,
                    layer_type=layer.layer_type,
                    vector_kind=layer.vector_kind,
                )
        filtered_models: dict[str, LayerRef] = {}
        for node_id, layer in (model_targets or {}).items():
            if isinstance(layer, LayerRef) and layer.id:
                filtered_models[node_id] = LayerRef(
                    id=layer.id,
                    name=layer.name,
                    layer_type=layer.layer_type,
                    vector_kind=layer.vector_kind,
                )
        if not filtered and not filtered_models:
            return
        if filtered:
            self._output_targets[job_id] = filtered
        if filtered_models:
            self._model_output_targets[job_id] = filtered_models
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("No event loop available for output monitoring")
            return
        task = loop.create_task(self._monitor_job_outputs(job_id, filtered, filtered_models))
        self._output_tasks[job_id] = task

    async def _process_image_parameters(  # noqa: PLR0912, PLR0915
        self, workflow: WorkflowModel, image_parameters: dict[str, Any]
    ) -> None:
        """Process IMAGE parameters by exporting and uploading layers once per layer."""
        if not ensure_network_services_consent(self._settings, service_name="ComfyUI"):
            return
        if not image_parameters:
            return

        from .layer_bridge import LayerBridge  # noqa: PLC0415

        if not hasattr(self, "_layer_bridge"):
            host_api = None
            for widget in image_parameters.values():
                if hasattr(widget, "host_api") and widget.host_api:
                    host_api = widget.host_api
                    break

            if not host_api and self.host_api:
                host_api = self.host_api

            if not host_api:
                logger.warning("No host API available for layer export")
                return

            self._layer_bridge = LayerBridge(host_api)

        layer_targets: dict[str, list[tuple[str, str, Any]]] = {}
        layer_refs: dict[str, LayerRef] = {}

        for key, image_widget in image_parameters.items():
            try:
                parts = key.rsplit(".", 1)
                if len(parts) != 2:
                    continue
                node_id, param_name = parts

                if not hasattr(image_widget, "get_selected_layer"):
                    continue

                layer = image_widget.get_selected_layer()
                layer_id = getattr(layer, "id", None) if layer else None
                if not layer or not layer_id:
                    continue

                node = workflow.nodes.get(node_id) if isinstance(workflow.nodes, dict) else None
                if not node:
                    logger.debug(
                        "Workflow node %s not found while processing layer export", node_id
                    )
                    continue

                existing_value = (
                    node.inputs.get(param_name) if isinstance(node.inputs, dict) else None
                )
                if isinstance(existing_value, (list, tuple, dict)):
                    logger.debug(
                        "Skipping %s.%s because parameter is wired to another node",
                        node_id,
                        param_name,
                    )
                    continue

                layer_refs.setdefault(layer_id, layer)
                targets = layer_targets.setdefault(layer_id, [])
                targets.append((node_id, param_name, image_widget))
            except Exception as exc:
                logger.error("Failed to inspect IMAGE parameter %s: %s", key, exc)

        if not layer_refs:
            return

        upload_cache: dict[str, str] = {}

        for layer_id, layer_ref in layer_refs.items():
            try:
                uploaded_filename = upload_cache.get(layer_id)
                if uploaded_filename:
                    logger.info(
                        "Reusing cached upload for layer '%s' (id: %s): %s",
                        layer_ref.name,
                        layer_id,
                        uploaded_filename,
                    )
                else:
                    export_path = await self._layer_bridge.export_layer_with_timestamp(layer_ref)
                    logger.info(
                        "Exported layer '%s' (id: %s) to: %s", layer_ref.name, layer_id, export_path
                    )

                    if not self._connection:
                        logger.error("No connection available for upload")
                        continue

                    logger.info("Uploading image to ComfyUI: %s", Path(export_path).name)
                    upload_result = await self._connection.upload_image(export_path)

                    if isinstance(upload_result, dict):
                        filename = (
                            upload_result.get("filename")
                            or upload_result.get("name")
                            or Path(export_path).name
                        )
                        subfolder = upload_result.get("subfolder") or ""
                        uploaded_filename = f"{subfolder}/{filename}" if subfolder else filename
                    else:
                        uploaded_filename = str(upload_result)

                    if not uploaded_filename:
                        logger.error("Upload did not return a filename for layer %s", layer_id)
                        continue

                    logger.info("Upload successful. ComfyUI filename: %s", uploaded_filename)
                    upload_cache[layer_id] = uploaded_filename

                for node_id, param_name, image_widget in layer_targets.get(layer_id, []):
                    node = workflow.nodes.get(node_id)
                    if not node:
                        logger.debug(
                            "Workflow node %s missing while applying image upload", node_id
                        )
                        continue

                    existing_value = (
                        node.inputs.get(param_name) if isinstance(node.inputs, dict) else None
                    )
                    if isinstance(existing_value, (list, tuple, dict)):
                        logger.debug(
                            "Skipping update for %s.%s because parameter remains wired",
                            node_id,
                            param_name,
                        )
                        continue

                    node.inputs[param_name] = uploaded_filename

                    if isinstance(node.inputs, dict) and (
                        "upload" in node.inputs or node.type == "LoadImage"
                    ):
                        node.inputs["upload"] = "input"

                    if hasattr(image_widget, "current_value"):
                        image_widget.current_value = uploaded_filename

                    logger.info(
                        "Updated workflow node %s.%s = '%s'", node_id, param_name, uploaded_filename
                    )

            except Exception as exc:
                logger.error("Failed to process IMAGE layer %s: %s", layer_id, exc)

    def _resolve_metadata_bindings(self, workflow: WorkflowModel, bindings: dict[str, Any]) -> None:
        """Resolve metadata bindings and inject values into workflow node inputs."""
        from .metadata_registry import (  # noqa: PLC0415
            MetadataBinding,
            ResolveContext,
            get_metadata_registry,
        )

        host_api = self.host_api
        if not host_api:
            logger.warning("No host API available for metadata binding resolution")
            return

        registry = get_metadata_registry()

        for key, binding in bindings.items():
            if not isinstance(binding, MetadataBinding):
                continue
            parts = key.rsplit(".", 1)
            if len(parts) != 2:
                continue
            node_id, param_name = parts

            node = workflow.nodes.get(node_id) if isinstance(workflow.nodes, dict) else None
            if not node:
                logger.debug("Node %s not found for metadata binding", node_id)
                continue

            ctx = ResolveContext(layer_id=binding.layer_id)
            value = registry.resolve(binding.entry_key, host_api, ctx)
            if value is not None:
                node.inputs[param_name] = value
                logger.info(
                    "Metadata binding: %s.%s = %s (from %s)",
                    node_id,
                    param_name,
                    value,
                    binding.entry_key,
                )

    async def _monitor_job_outputs(
        self,
        job_id: str,
        targets: dict[str, LayerRef],
        model_targets: dict[str, LayerRef] | None = None,
    ) -> None:
        """Poll ComfyUI history until job completion and import outputs."""
        success_flag: bool | None = None
        error_message: str | None = None
        cancelled = False
        history_entry: dict[str, Any] | None = None
        model_targets = model_targets or {}
        try:
            if not targets and not model_targets:
                return
            if not ensure_network_services_consent(self._settings, service_name="ComfyUI"):
                raise RuntimeError("ComfyUI access is disabled")
            await self.initialize()
            if not self._connection:
                logger.warning("Connection unavailable while monitoring job %s", job_id)
                return
            attempts = 0
            while True:
                history = await self._connection.get_history(job_id)
                entry = history.get(job_id) if history else None
                status = self._extract_history_status(entry)
                if status in {"completed", "success", "finished"} or (
                    entry and entry.get("outputs")
                ):
                    outputs = entry.get("outputs", {}) if isinstance(entry, dict) else {}
                    first_image = await self._apply_job_outputs(job_id, outputs, targets)
                    await self._apply_3d_job_outputs(job_id, outputs, model_targets)
                    success_flag = True
                    history_entry = entry if isinstance(entry, dict) else None
                    if history_entry is not None and first_image:
                        history_entry = {**history_entry, "_image_bytes": first_image}
                    break
                if status in {"failed", "error", "cancelled"}:
                    logger.error(
                        "ComfyUI job %s ended with status %s; skipping layer import", job_id, status
                    )
                    success_flag = False
                    error_message = f"status={status}"
                    cancelled = status == "cancelled"
                    break
                await asyncio.sleep(min(2.0, 0.5 + attempts * 0.5))
                attempts += 1
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Failed to monitor outputs for job %s: %s", job_id, exc)
            success_flag = False
            error_message = str(exc)
        finally:
            self._output_targets.pop(job_id, None)
            self._model_output_targets.pop(job_id, None)
            task = self._output_tasks.pop(job_id, None)
            current = asyncio.current_task()
            if task and task is not current and not task.done():
                task.cancel()

            # Emit AI_JOB_RESULT so Gallery / history recording can proceed
            # even when _stream_job fails to detect completion via WebSocket.
            if success_flag is not None and self._event_bus:
                if cancelled:
                    self._event_bus.emit(
                        Events.AI_JOB_CANCELLED,
                        JobResultEvent(
                            job_id=job_id,
                            success=False,
                            result=None,
                            error=error_message,
                        ),
                    )
                else:
                    self._event_bus.emit(
                        Events.AI_JOB_RESULT,
                        JobResultEvent(
                            job_id=job_id,
                            success=success_flag,
                            result=None,
                            result_data=history_entry,
                            error=error_message,
                        ),
                    )

    def _extract_history_status(self, entry: dict[str, Any] | None) -> str:
        if not isinstance(entry, dict):
            return ""
        status = entry.get("status")
        if isinstance(status, dict):
            value = status.get("status") or status.get("state")
            if isinstance(value, str):
                return value.lower()
        elif isinstance(status, str):
            return status.lower()
        if entry.get("completed") is True:
            return "completed"
        if entry.get("error"):
            return "error"
        return ""

    async def _stream_job(self, job_id: str, prompt_id: str) -> None:
        """Override: skip AI_JOB_RESULT when _monitor_job_outputs handles it."""
        has_output_monitor = (
            job_id in self._output_targets
            or job_id in self._model_output_targets
            or job_id in self._output_tasks
        )
        try:
            async for event in self.stream(job_id):
                if self._event_bus:
                    self._event_bus.emit(
                        Events.AI_JOB_PROGRESS,
                        JobProgressEvent(
                            job_id=job_id,
                            step=event.step,
                            total=event.total,
                            message=event.message,
                            preview_data=event.preview_png,
                        ),
                    )
                    is_complete = event.total > 0 and event.step >= event.total
                    if is_complete:
                        if not has_output_monitor:
                            result = await self.get_result(job_id)
                            raw_history = None
                            if self._connection:
                                raw_history = await self._connection.get_result(
                                    self._resolve_prompt_id(job_id)
                                )
                            self._event_bus.emit(
                                Events.AI_JOB_RESULT,
                                JobResultEvent(
                                    job_id=job_id,
                                    success=True,
                                    result=result,
                                    result_data=raw_history,
                                    error=None,
                                ),
                            )
                        return
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self._event_bus and not has_output_monitor:
                self._event_bus.emit(
                    Events.AI_JOB_RESULT,
                    JobResultEvent(
                        job_id=job_id,
                        success=False,
                        result=None,
                        error=str(e),
                    ),
                )
        finally:
            self._cleanup_job_tracking(job_id)

    async def _apply_job_outputs(
        self, job_id: str, outputs: dict[str, Any], targets: dict[str, LayerRef]
    ) -> bytes | None:
        """Fetch generated images and import them into the selected layers.

        Returns:
            The first downloaded image bytes (for Gallery), or None.
        """
        first_image_bytes: bytes | None = None
        if not targets:
            return None
        from .layer_bridge import LayerBridge  # noqa: PLC0415

        if not hasattr(self, "_layer_bridge") or self._layer_bridge is None:
            if self.host_api is None:
                logger.warning("Host API unavailable; cannot import ComfyUI outputs")
                return None
            self._layer_bridge = LayerBridge(self.host_api)
        if not self._connection:
            logger.warning("Connection lost before importing outputs for job %s", job_id)
            return None
        for node_id, layer in targets.items():
            try:
                node_output = outputs.get(node_id) if isinstance(outputs, dict) else None
                images = []
                if isinstance(node_output, dict):
                    images = node_output.get("images") or node_output.get("image", [])
                elif isinstance(node_output, list):
                    images = node_output
                if not images:
                    logger.warning("No images found for node %s in job %s", node_id, job_id)
                    continue
                image_info = images[0]
                filename = None
                subfolder = ""
                folder_type = "output"
                if isinstance(image_info, dict):
                    filename = image_info.get("filename") or image_info.get("name")
                    subfolder = image_info.get("subfolder") or ""
                    folder_type = image_info.get("type") or "output"
                elif isinstance(image_info, str):
                    filename = image_info
                if not filename:
                    logger.warning("Image metadata missing filename for node %s", node_id)
                    continue
                image_bytes = await self._connection.get_image(
                    filename, subfolder=subfolder, folder_type=folder_type
                )
                if first_image_bytes is None:
                    first_image_bytes = image_bytes
                metadata = {
                    "job_id": job_id,
                    "node_id": node_id,
                    "filename": filename,
                    "subfolder": subfolder,
                    "folder_type": folder_type,
                    "source": "ComfyUI",
                    "target_layer_id": layer.id,
                }
                await self._layer_bridge.import_image_to_layer(
                    image_bytes, layer, metadata=metadata
                )
                logger.info("Imported ComfyUI output %s into layer %s", node_id, layer.id)
            except Exception as exc:
                logger.error("Failed to import output from node %s: %s", node_id, exc)
        return first_image_bytes

    async def _apply_3d_job_outputs(
        self,
        job_id: str,
        outputs: dict[str, Any],
        targets: dict[str, LayerRef],
    ) -> int:
        """Fetch generated 3D assets and apply them to the selected layers."""

        if not targets:
            return 0
        if not self._connection:
            logger.warning("Connection lost before importing 3D outputs for job %s", job_id)
            return 0
        canvas = getattr(self.host_api, "canvas", None) if self.host_api is not None else None
        set_scene = getattr(canvas, "set_3d_layer_scene", None) if canvas is not None else None
        add_3d_layer = getattr(canvas, "add_3d_layer", None) if canvas is not None else None
        if not callable(set_scene) and not callable(add_3d_layer):
            logger.warning("Canvas does not support applying ComfyUI 3D outputs")
            return 0

        receiver = self._get_generated_3d_receiver()
        imported = 0
        for node_id, layer in targets.items():
            try:
                node_output = outputs.get(node_id) if isinstance(outputs, dict) else None
                entries = self._extract_3d_model_entries(node_output)
                if not entries:
                    logger.warning(
                        "No 3D model artifact found for node %s in job %s", node_id, job_id
                    )
                    continue

                for entry in entries:
                    file_info = self._model_file_info(entry)
                    if file_info is None:
                        continue
                    filename, subfolder, folder_type = file_info
                    model_bytes = await self._connection.get_image(
                        filename,
                        subfolder=subfolder,
                        folder_type=folder_type,
                    )
                    model_path = await self._store_downloaded_model(
                        job_id,
                        node_id,
                        filename,
                        model_bytes,
                    )
                    await self._prepare_downloaded_model(
                        model_path,
                        source_subfolder=subfolder,
                        folder_type=folder_type,
                    )
                    received = receiver.receive_scene_response(
                        "comfyui.3d",
                        {
                            "prompt_id": job_id,
                            "model_file": str(model_path),
                            "node_id": node_id,
                            "metadata": {
                                "filename": filename,
                                "subfolder": subfolder,
                                "folder_type": folder_type,
                            },
                        },
                    )
                    gen_3d = {
                        "provider_id": "comfyui.3d",
                        "job_id": job_id,
                        "node_id": node_id,
                        "asset_id": received.manifest.asset_id,
                        "artifact": received.artifact_relative_path,
                        "filename": filename,
                    }
                    layer_id_for_log = layer.id
                    if layer.id == CREATE_NEW_3D_LAYER_ID:
                        if not callable(add_3d_layer):
                            logger.warning("Canvas cannot create a 3D layer for node %s", node_id)
                            continue
                        created = add_3d_layer(
                            name=self._default_3d_layer_name(filename),
                            scene=received.scene,
                            display_mode="solid",
                            gen_3d=gen_3d,
                            origin={"type": "comfyui_3d_output"},
                        )
                        ok = created is not None
                        layer_id_for_log = getattr(created, "id", CREATE_NEW_3D_LAYER_ID)
                    else:
                        if not callable(set_scene):
                            logger.warning("Canvas cannot update 3D layer target %s", layer.id)
                            continue
                        ok = bool(
                            set_scene(
                                layer.id,
                                scene=received.scene,
                                display_mode="solid",
                                gen_3d=gen_3d,
                                origin={"type": "comfyui_3d_output"},
                            )
                        )
                    if ok:
                        imported += 1
                        logger.info(
                            "Imported ComfyUI 3D output %s into layer %s",
                            node_id,
                            layer_id_for_log,
                        )
                        break
                    logger.warning(
                        "Canvas rejected ComfyUI 3D output %s for layer %s", node_id, layer.id
                    )
            except Exception as exc:
                logger.error("Failed to import 3D output from node %s: %s", node_id, exc)
        return imported

    def _get_generated_3d_receiver(self) -> Any:
        receiver = getattr(self, "_generated_3d_receiver", None)
        if receiver is not None:
            return receiver
        from loomstroke.core.assets_3d import Generated3DAssetReceiver  # noqa: PLC0415
        from loomstroke.plugins.three_d_generation.providers.comfyui import (  # noqa: PLC0415
            ComfyUI3DProviderAdapter,
        )

        receiver = Generated3DAssetReceiver(
            adapters={"comfyui.3d": ComfyUI3DProviderAdapter()},
        )
        self._generated_3d_receiver = receiver
        return receiver

    def _extract_3d_model_entries(self, node_output: Any) -> list[Any]:
        entries: list[Any] = []
        self._collect_3d_model_entries(node_output, entries, key_hint=None)
        return entries

    def _collect_3d_model_entries(
        self,
        value: Any,
        entries: list[Any],
        *,
        key_hint: str | None,
    ) -> None:
        if value is None:
            return
        if isinstance(value, Mapping):
            filename = self._entry_filename(value)
            artifact_kind = str(
                value.get("kind") or value.get("role") or value.get("artifact_type") or ""
            ).lower()
            if filename and (
                self._is_model_output_hint(key_hint)
                or self._is_model_path(filename)
                or artifact_kind in {"mesh", "model", "3d", "glb", "gltf", "obj"}
            ):
                entries.append(value)
                return
            for key, child in value.items():
                self._collect_3d_model_entries(child, entries, key_hint=str(key).lower())
            return
        if isinstance(value, (list, tuple)):
            for child in value:
                self._collect_3d_model_entries(child, entries, key_hint=key_hint)
            return
        if isinstance(value, str) and (
            self._is_model_output_hint(key_hint) or self._is_model_path(value)
        ):
            entries.append(value)

    def _model_file_info(self, entry: Any) -> tuple[str, str, str] | None:
        filename: str | None = None
        subfolder = ""
        folder_type = "output"
        if isinstance(entry, Mapping):
            filename = self._entry_filename(entry)
            subfolder = str(entry.get("subfolder") or "")
            raw_folder = entry.get("folder_type") or entry.get("type") or "output"
            raw_folder_text = str(raw_folder or "output").lower()
            if raw_folder_text in _COMFY_OUTPUT_FOLDERS:
                folder_type = raw_folder_text
        elif isinstance(entry, str):
            filename = entry
        if not filename:
            return None
        filename, subfolder = self._split_comfy_output_path(filename, subfolder)
        if not self._is_model_path(filename):
            logger.warning("Skipping non-model ComfyUI artifact: %s", filename)
            return None
        return filename, subfolder, folder_type

    @staticmethod
    def _entry_filename(entry: Mapping[str, Any]) -> str | None:
        for key in ("filename", "name", "file", "path", "local_path", "model_file", "model_path"):
            value = entry.get(key)
            if value:
                return str(value)
        return None

    @staticmethod
    def _is_model_output_hint(key_hint: str | None) -> bool:
        if not key_hint:
            return False
        normalized = "".join(ch if ch.isalnum() else "_" for ch in key_hint.lower()).strip("_")
        return normalized in _MODEL_OUTPUT_KEYS or any(
            token in normalized for token in ("3d", "model", "mesh", "glb", "gltf", "obj")
        )

    @staticmethod
    def _is_model_path(value: str) -> bool:
        return Path(str(value).split("?", 1)[0]).suffix.lower() in _MODEL_FILE_SUFFIXES

    @staticmethod
    def _split_comfy_output_path(filename: str, subfolder: str) -> tuple[str, str]:
        raw = str(filename).replace("\\", "/")
        parts = [part for part in raw.split("/") if part]
        if not parts:
            return filename, subfolder
        basename = parts[-1]
        if subfolder or raw.startswith("/") or (parts and parts[0].endswith(":")):
            return basename, subfolder
        if len(parts) > 1:
            return basename, "/".join(parts[:-1])
        return basename, subfolder

    async def _store_downloaded_model(
        self,
        job_id: str,
        node_id: str,
        filename: str,
        content: bytes,
    ) -> Path:
        output_dir = ensure_directory(
            self._data_root
            / "generated_3d_outputs"
            / (
                f"{self._safe_path_token(job_id)}_"
                f"{self._safe_path_token(node_id)}_"
                f"{uuid.uuid4().hex[:8]}"
            )
        )
        safe_name = self._safe_model_filename(filename)
        target = output_dir / safe_name
        await _write_bytes_async(target, content)
        return target

    async def _prepare_downloaded_model(
        self,
        model_path: Path,
        *,
        source_subfolder: str,
        folder_type: str,
    ) -> None:
        if model_path.suffix.lower() != ".gltf":
            return
        raw = json.loads(model_path.read_text(encoding="utf-8"))
        changed = False
        for buffer in raw.get("buffers", []):
            if not isinstance(buffer, dict):
                continue
            uri = buffer.get("uri")
            if not isinstance(uri, str) or uri.startswith("data:"):
                continue
            dependency_path = self._local_gltf_dependency_path(model_path.parent, uri)
            if not dependency_path.is_file():
                filename, subfolder = self._dependency_file_info(uri, source_subfolder)
                if self._connection is None:
                    raise RuntimeError("Connection unavailable while fetching glTF buffer")
                dependency_bytes = await self._connection.get_image(
                    filename,
                    subfolder=subfolder,
                    folder_type=folder_type,
                )
                ensure_directory(dependency_path.parent)
                await _write_bytes_async(dependency_path, dependency_bytes)
            data = dependency_path.read_bytes()
            buffer["uri"] = "data:application/octet-stream;base64," + base64.b64encode(data).decode(
                "ascii"
            )
            buffer["byteLength"] = len(data)
            changed = True
        if changed:
            await _write_text_async(model_path, json.dumps(raw, separators=(",", ":")))

    @staticmethod
    def _dependency_file_info(uri: str, source_subfolder: str) -> tuple[str, str]:
        relative = ExtendedComfyUIService._safe_relative_uri_path(uri)
        parts = list(relative.parts)
        filename = parts[-1]
        folder_parts = [
            part for part in str(source_subfolder or "").replace("\\", "/").split("/") if part
        ]
        folder_parts.extend(parts[:-1])
        return filename, "/".join(folder_parts)

    @staticmethod
    def _local_gltf_dependency_path(base_dir: Path, uri: str) -> Path:
        relative = ExtendedComfyUIService._safe_relative_uri_path(uri)
        base = base_dir.resolve()
        target = (base / relative).resolve()
        try:
            target.relative_to(base)
        except ValueError as exc:
            raise ValueError(f"glTF dependency escapes model directory: {uri}") from exc
        return target

    @staticmethod
    def _safe_relative_uri_path(uri: str) -> Path:
        split = urlsplit(uri)
        if split.scheme or split.netloc:
            raise ValueError(f"remote glTF dependencies are not supported: {uri}")
        raw_path = unquote(split.path).replace("\\", "/")
        parts = [part for part in raw_path.split("/") if part and part != "."]
        if not parts or any(part == ".." or ":" in part for part in parts):
            raise ValueError(f"unsafe glTF dependency path: {uri}")
        return Path(*parts)

    @staticmethod
    def _safe_model_filename(filename: str) -> str:
        name = Path(str(filename).replace("\\", "/")).name or "model.glb"
        safe = "".join(ch if ch.isalnum() or ch in {".", "-", "_"} else "_" for ch in name)
        return safe or "model.glb"

    @staticmethod
    def _safe_path_token(value: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))
        return safe[:48] or "unknown"

    @staticmethod
    def _default_3d_layer_name(filename: str) -> str:
        stem = Path(str(filename).replace("\\", "/")).stem.strip()
        return stem or "ComfyUI 3D Model"

    async def _notify_job_completion(
        self,
        job_id: str,
        *,
        success: bool,
        error: str | None = None,
        cancelled: bool = False,
    ) -> None:
        """Emit job lifecycle events for UI / preview consumers."""
        if not self._event_bus:
            return

        result: JobResult | None = None
        if success:
            try:
                result = await self.get_result(job_id)
            except Exception as exc:  # pragma: no cover - diagnostic fallback
                logger.debug("Failed to fetch ComfyUI job result %s: %s", job_id, exc)

        payload = JobResultEvent(
            job_id=job_id,
            success=success,
            result=result,
            error=error,
        )

        try:
            self._event_bus.emit(Events.AI_JOB_RESULT, payload)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.debug("Failed to emit AI_JOB_RESULT for %s: %s", job_id, exc)

        if cancelled:
            try:
                self._event_bus.emit(Events.AI_JOB_CANCELLED, payload)
            except Exception as exc:  # pragma: no cover - defensive logging
                logger.debug("Failed to emit AI_JOB_CANCELLED for %s: %s", job_id, exc)

    async def get_job_history(self, limit: int = 50) -> list[dict[str, Any]]:
        """Get job execution history.

        Args:
            limit: Maximum number of jobs to return

        Returns:
            List of job records
        """
        if not ensure_network_services_consent(self._settings, service_name="ComfyUI"):
            return []
        await self.initialize()

        if self._connection:
            history = await self._connection.get_history(limit)
            return history or []

        return []

    async def clear_queue(self) -> bool:
        """Clear all pending jobs in queue.

        Returns:
            True if successful
        """
        if not ensure_network_services_consent(self._settings, service_name="ComfyUI"):
            return False
        await self.initialize()

        if self._connection:
            return await self._connection.clear_queue()

        return False
