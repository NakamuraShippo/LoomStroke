"""ComfyUI Workflow Model.

This module defines the workflow data structures and manipulation logic
 ported from the legacy implementation and adapted to the new plugin architecture.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class NodeClass(Enum):
    """ComfyUI node classes."""

    INPUT = "input"
    OUTPUT = "output"
    LATENT = "latent"
    CONDITIONING = "conditioning"
    MODEL = "model"
    VAE = "vae"
    CLIP = "clip"
    CONTROL_NET = "controlnet"
    IMAGE = "image"
    MASK = "mask"
    CUSTOM = "custom"


@dataclass
class NodeInput:
    """Node input configuration."""

    name: str
    value: Any
    type: str = "default"
    link: int | None = None  # Link ID if connected


@dataclass
class NodeOutput:
    """Node output configuration."""

    name: str
    type: str
    links: list[int] = field(default_factory=list)  # Connected link IDs


@dataclass
class NodeConfig:
    """ComfyUI node configuration."""

    id: str | int
    type: str  # Node class name (e.g., "KSampler", "LoadImage")
    title: str | None = None  # User-defined display name from ComfyUI
    pos: list[float] = field(default_factory=lambda: [0.0, 0.0])
    size: list[float] = field(default_factory=lambda: [200.0, 100.0])
    flags: dict[str, Any] = field(default_factory=dict)
    order: int = 0
    mode: int = 0
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: list[NodeOutput] = field(default_factory=list)
    properties: dict[str, Any] = field(default_factory=dict)
    widgets_values: list[Any] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NodeConfig:
        """Create NodeConfig from dictionary."""
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            type=data.get("type", ""),
            title=data.get("title"),
            pos=data.get("pos", [0, 0]),
            size=data.get("size", [200, 100]),
            flags=data.get("flags", {}),
            order=data.get("order", 0),
            mode=data.get("mode", 0),
            inputs=data.get("inputs", {}),
            outputs=[
                # UI exports include display metadata (localized_name,
                # slot_index, etc.) that is not part of the execution model.
                (
                    NodeOutput(name=o["name"], type=o["type"], links=o.get("links") or [])
                    if isinstance(o, dict)
                    else o
                )
                for o in data.get("outputs", [])
            ],
            properties=data.get("properties", {}),
            widgets_values=data.get("widgets_values", []),
        )

    @classmethod
    def from_comfyui_format(cls, node_id: str, data: dict[str, Any]) -> NodeConfig:
        """Create NodeConfig from ComfyUI format (node_id, {inputs, class_type, _meta})."""
        meta = data.get("_meta", {})
        return cls(
            id=node_id,
            type=data.get("class_type", ""),
            title=meta.get("title") if isinstance(meta, dict) else None,
            pos=[0, 0],  # ComfyUI doesn't store position in this format
            size=[200, 100],
            flags={},
            order=0,
            mode=0,
            inputs=data.get("inputs", {}),
            outputs=[],  # ComfyUI doesn't store outputs in this format
            properties={},
            widgets_values=[],
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result: dict[str, Any] = {
            "id": self.id,
            "type": self.type,
            "pos": self.pos,
            "size": self.size,
            "flags": self.flags,
            "order": self.order,
            "mode": self.mode,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "properties": self.properties,
            "widgets_values": self.widgets_values,
        }
        if self.title is not None:
            result["title"] = self.title
        return result


@dataclass
class LinkConfig:
    """Connection between nodes."""

    id: int
    source_node: str | int
    source_slot: int  # Output slot index
    target_node: str | int
    target_slot: int  # Input slot index
    type: str | None = None  # Data type (e.g., "IMAGE", "LATENT")

    @classmethod
    def from_list(cls, data: Sequence[Any]) -> LinkConfig:
        """Create LinkConfig from list format [id, src_node, src_slot, tgt_node, tgt_slot, type]."""
        return cls(
            id=data[0],
            source_node=data[1],
            source_slot=data[2],
            target_node=data[3],
            target_slot=data[4],
            type=data[5] if len(data) > 5 else None,
        )

    def to_list(self) -> list[Any]:
        """Convert to list format."""
        result = [self.id, self.source_node, self.source_slot, self.target_node, self.target_slot]
        if self.type:
            result.append(self.type)
        return result


@dataclass
class WorkflowModel:
    """ComfyUI workflow model."""

    nodes: dict[str, NodeConfig] = field(default_factory=dict)
    links: list[LinkConfig] = field(default_factory=list)
    groups: list[dict[str, Any]] = field(default_factory=list)
    version: float = 0.4
    metadata: dict[str, Any] = field(default_factory=dict)
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> WorkflowModel:
        """Create WorkflowModel from JSON data.

        Handles both formats:
        1. Standard format with 'nodes' array: {"nodes": [...], "links": [...]}
        2. ComfyUI format where root object contains nodes: {"3": {...}, "4": {...}}
        """
        nodes = {}

        # Handle standard format with 'nodes' array
        if "nodes" in data:
            for node_data in data["nodes"]:
                node = NodeConfig.from_dict(node_data)
                nodes[str(node.id)] = node
        else:
            # Handle ComfyUI format where root object contains nodes
            # Node IDs are keys, and each value has 'class_type' and 'inputs'
            for key, value in data.items():
                if isinstance(value, dict) and "class_type" in value:
                    # This is a node
                    node = NodeConfig.from_comfyui_format(key, value)
                    nodes[str(node.id)] = node

        # Handle links
        links = []
        if "links" in data:
            for link_data in data["links"]:
                if isinstance(link_data, list):
                    links.append(LinkConfig.from_list(link_data))
                else:
                    links.append(LinkConfig(**link_data))

        return cls(
            nodes=nodes,
            links=links,
            groups=data.get("groups", []),
            version=data.get("version", 0.4),
            metadata=data.get("metadata", {}),
            extra=data.get("extra", {}),
        )

    def to_json(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary."""
        return {
            "nodes": [node.to_dict() for node in self.nodes.values()],
            "links": [link.to_list() for link in self.links],
            "groups": self.groups,
            "version": self.version,
            "metadata": self.metadata,
            "extra": self.extra,
        }

    def to_api_format(  # noqa: PLR0912 - title, socket and widget conversion
        self, object_info: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Convert to ComfyUI API format for execution.

        ``object_info`` is an optional ``/object_info`` snapshot — either the
        wrapped ``{"nodes": {class_type: schema, ...}, ...}`` shape or the raw
        ``{class_type: schema, ...}`` dict. When it has a schema for a node
        type, ``widgets_values`` are mapped to input names via the schema's
        declared input order (required then optional, connection inputs
        excluded) so custom nodes are covered too. Without a schema the
        static ``_get_widget_mapping`` table remains the fallback.
        """
        # API format uses a simpler structure
        api_format: dict[str, dict[str, Any]] = {}
        links_by_id = {link.id: link for link in self.links}

        for node_id, node in self.nodes.items():
            api_inputs: dict[str, Any] = {}
            api_node: dict[str, Any] = {
                "class_type": node.type,
                "inputs": api_inputs,
            }
            if isinstance(node.title, str) and node.title.strip():
                api_node["_meta"] = {"title": node.title}

            # Convert inputs - these take priority as they may have been updated by UI
            if isinstance(node.inputs, dict):
                for input_name, input_value in node.inputs.items():
                    api_inputs[input_name] = input_value
            elif isinstance(node.inputs, list):
                # UI-format inputs are socket descriptions, with connections
                # stored in the workflow's link table rather than input values.
                for socket in node.inputs:
                    link_id = socket.get("link")
                    if link_id is None:
                        continue
                    link = links_by_id[link_id]
                    api_inputs[socket["name"]] = [str(link.source_node), link.source_slot]

            # If we still have widget values that weren't mapped to inputs, add them
            # This is a fallback for cases where the UI didn't properly map widgets
            if node.widgets_values and not any(k.startswith("widget_") for k in api_inputs):
                # Map widget values: schema-derived order first (covers
                # custom nodes), static table as fallback.
                widget_mapping = self._widget_order_from_schema(
                    node.type, object_info
                ) or self._get_widget_mapping(node.type)
                if widget_mapping and isinstance(node.widgets_values, list):
                    for idx, value in enumerate(node.widgets_values):
                        if idx < len(widget_mapping):
                            param_name = widget_mapping[idx]
                            if param_name is None:
                                # UI-only widget slot (e.g. the
                                # control_after_generate dropdown) — consume
                                # the index without emitting an input.
                                continue
                            # Only add if not already in inputs
                            if param_name not in api_inputs:
                                api_inputs[param_name] = value

            api_format[str(node_id)] = api_node

        return api_format

    @staticmethod
    def _widget_order_from_schema(
        node_type: str, object_info: dict[str, Any] | None
    ) -> list[str | None] | None:
        """Derive the ``widgets_values`` order from an ``/object_info`` schema.

        Mirrors the ComfyUI frontend's widget layout: required inputs first,
        then optional, keeping only inputs that materialise as widgets
        (combos, INT / FLOAT / STRING / BOOLEAN, or custom scalar types that
        declare a ``default``). Connection inputs (LATENT, MODEL, ...) do not
        occupy a ``widgets_values`` slot. Seed-style INT widgets are followed
        by a ``None`` placeholder for the UI-only ``control_after_generate``
        dropdown, which occupies a slot but is not an API input.

        Returns ``None`` when the schema is unavailable so callers can fall
        back to the static table.
        """
        from loomstroke.plugins.comfyui.object_info_spec import (  # noqa: PLC0415
            is_combo_head,
            split_input_spec,
        )

        connection_types = {
            "LATENT",
            "CONDITIONING",
            "MODEL",
            "VAE",
            "CLIP",
            "CONTROL_NET",
            "IMAGE",
            "MASK",
        }

        if not isinstance(object_info, dict):
            return None
        nodes = object_info.get("nodes") if isinstance(object_info.get("nodes"), dict) else None
        if nodes is None:
            nodes = object_info
        schema = nodes.get(node_type)
        input_section = schema.get("input") if isinstance(schema, dict) else None
        if not isinstance(input_section, dict):
            return None

        order: list[str | None] = []
        for section in ("required", "optional"):
            specs = input_section.get(section)
            if not isinstance(specs, dict):
                continue
            for name, spec in specs.items():
                head, options = split_input_spec(spec)
                if is_combo_head(head):
                    order.append(name)
                elif head in ("INT", "FLOAT", "STRING", "BOOLEAN"):
                    order.append(name)
                    if head == "INT" and (
                        options.get("control_after_generate") or name in ("seed", "noise_seed")
                    ):
                        order.append(None)
                elif (
                    isinstance(head, str) and head not in connection_types and "default" in options
                ):
                    # Custom scalar-ish type that declares a default value —
                    # rendered as a widget by the frontend.
                    order.append(name)
                # Anything else is a connection input: no widget slot.
        return order or None

    def _get_widget_mapping(self, node_type: str) -> list[str] | None:
        """Get widget parameter names for common node types.

        Args:
            node_type: The ComfyUI node type

        Returns:
            List of parameter names in order, or None if unknown
        """
        # Common node type widget mappings (same as in node_tabs.py)
        mappings = {
            "KSampler": [
                "seed",
                "control_after_generate",
                "steps",
                "cfg",
                "sampler_name",
                "scheduler",
                "denoise",
            ],
            "KSamplerAdvanced": [
                "add_noise",
                "noise_seed",
                "control_after_generate",
                "steps",
                "cfg",
                "sampler_name",
                "scheduler",
                "start_at_step",
                "end_at_step",
                "return_with_leftover_noise",
            ],
            "CLIPTextEncode": ["text"],
            "CheckpointLoaderSimple": ["ckpt_name"],
            "LoadImage": ["image", "upload"],
            "SaveImage": ["filename_prefix"],
            "EmptyLatentImage": ["width", "height", "batch_size"],
            "LatentUpscale": ["upscale_method", "width", "height", "crop"],
            "LoraLoader": ["lora_name", "strength_model", "strength_clip"],
            "CLIPSetLastLayer": ["stop_at_clip_layer"],
            "ControlNetLoader": ["control_net_name"],
            "ControlNetApply": ["strength"],
        }
        return mappings.get(node_type)

    def get_input_nodes(self) -> list[NodeConfig]:
        """Get all input nodes (LoadImage, etc.)."""
        input_types = ["LoadImage", "LoadImageMask", "EmptyImage", "EmptyLatentImage"]
        return [node for node in self.nodes.values() if node.type in input_types]

    def get_output_nodes(self) -> list[NodeConfig]:
        """Get all output nodes (SaveImage, PreviewImage, etc.)."""
        output_types = ["SaveImage", "PreviewImage", "SaveAnimatedWEBP", "SaveAnimatedPNG"]
        return [node for node in self.nodes.values() if node.type in output_types]

    def find_node_by_type(self, node_type: str) -> NodeConfig | None:
        """Find first node of given type."""
        for node in self.nodes.values():
            if node.type == node_type:
                return node
        return None

    def find_nodes_by_type(self, node_type: str) -> list[NodeConfig]:
        """Find all nodes of given type."""
        return [node for node in self.nodes.values() if node.type == node_type]

    def add_node(self, node_type: str, **kwargs: Any) -> NodeConfig:
        """Add a new node to the workflow."""
        node_id = str(uuid.uuid4())

        node = NodeConfig(id=node_id, type=node_type, **kwargs)

        self.nodes[node_id] = node
        return node

    def add_link(
        self,
        source_node: str | NodeConfig,
        source_slot: int,
        target_node: str | NodeConfig,
        target_slot: int,
        link_type: str | None = None,
    ) -> LinkConfig:
        """Add a link between nodes."""
        # Get node IDs
        src_id = source_node.id if isinstance(source_node, NodeConfig) else source_node
        tgt_id = target_node.id if isinstance(target_node, NodeConfig) else target_node

        # Generate link ID
        link_id = max([link.id for link in self.links], default=0) + 1

        link = LinkConfig(
            id=link_id,
            source_node=src_id,
            source_slot=source_slot,
            target_node=tgt_id,
            target_slot=target_slot,
            type=link_type,
        )

        self.links.append(link)
        return link

    def remove_node(self, node_id: str) -> None:
        """Remove a node and its connections."""
        if node_id in self.nodes:
            del self.nodes[node_id]

            # Remove associated links
            self.links = [
                link for link in self.links if node_id not in (link.source_node, link.target_node)
            ]

    def validate(self) -> bool:
        """Basic workflow validation."""
        # Check if workflow has nodes
        if not self.nodes:
            return False

        # Check if all links reference existing nodes
        node_ids = set(self.nodes.keys())
        for link in self.links:
            if str(link.source_node) not in node_ids or str(link.target_node) not in node_ids:
                return False

        return True

    def clone(self) -> WorkflowModel:
        """Create a deep copy of the workflow."""
        import copy  # noqa: PLC0415

        return copy.deepcopy(self)
