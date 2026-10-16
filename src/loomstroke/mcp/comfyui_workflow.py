"""ComfyUI object_info indexing and workflow draft helpers for MCP."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from loomstroke.plugins.comfyui.object_info_spec import (
    combo_choices,
    is_combo_head,
    is_multiselect,
)

AGENT_CATALOG_FORMAT = "comfyui.agent_catalog.v1"
AGENT_CATALOG_SCHEMA_VERSION = "1.0"

CONNECTION_TYPES = {
    "AUDIO",
    "CLIP",
    "CONDITIONING",
    "CONTROL_NET",
    "IMAGE",
    "LATENT",
    "MASK",
    "MODEL",
    "SIGMAS",
    "VAE",
}

SCALAR_TYPES = {"BOOLEAN", "FLOAT", "INT", "STRING"}

MODEL_FAMILY_PATTERNS: dict[str, tuple[str, ...]] = {
    "sdxl": (
        "sdxl",
        "sd_xl",
        "stable diffusion xl",
        "cliptextencodesdxl",
        "illustrious",
        "pony",
        "xl",
    ),
    "sd15": ("sd15", "sd1.5", "sd_1.5", "v1-5", "stable diffusion 1.5"),
    "flux": ("flux", "fluxguidance", "flux guidance"),
    "sd3": ("sd3", "stable diffusion 3", "triplecliploader", "emptysd3latentimage"),
    "wan": ("wan", "wanvideo", "wan2.1", "wan2_1"),
    "video": ("video", "animate", "svd", "wanvideo"),
}

AGENT_ROLE_ORDER = (
    "checkpoint_loader",
    "model_loader",
    "clip_loader",
    "vae_loader",
    "lora_loader",
    "text_encoder",
    "latent_source",
    "sampler",
    "decoder",
    "image_input",
    "image_output",
    "controlnet",
    "mask",
    "upscaler",
    "video",
)

AGENT_ROLE_DEFINITIONS = {
    "checkpoint_loader": "Loads a checkpoint and usually emits MODEL, CLIP, and VAE.",
    "model_loader": "Loads or prepares a MODEL output without being a full checkpoint loader.",
    "clip_loader": "Loads CLIP encoders.",
    "vae_loader": "Loads VAE models.",
    "lora_loader": "Applies or loads LoRA data for MODEL or CLIP paths.",
    "text_encoder": "Converts prompt text into CONDITIONING.",
    "latent_source": "Creates an initial LATENT input for sampling.",
    "sampler": "Runs diffusion sampling from MODEL, CONDITIONING, and LATENT inputs.",
    "decoder": "Decodes LATENT data into IMAGE output.",
    "image_input": "Loads or receives IMAGE data.",
    "image_output": "Saves, previews, or emits final IMAGE data.",
    "controlnet": "Loads or applies ControlNet style conditioning.",
    "mask": "Creates, transforms, or consumes MASK data.",
    "upscaler": "Upscales IMAGE or latent data.",
    "video": "Handles animated, video, or frame-sequence workflows.",
}

AGENT_ROLE_PREFERRED_CLASSES = {
    "checkpoint_loader": ("CheckpointLoaderSimple", "CheckpointLoader"),
    "text_encoder": ("CLIPTextEncodeSDXL", "CLIPTextEncode"),
    "latent_source": ("EmptyLatentImage", "EmptySD3LatentImage"),
    "sampler": ("KSampler",),
    "decoder": ("VAEDecode",),
    "image_output": ("SaveImage", "PreviewImage"),
}


@dataclass(frozen=True, slots=True)
class ObjectInfoCatalog:
    nodes: dict[str, Any]
    models: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WorkflowDraftRequest:
    kind: str = "sdxl_t2i"
    model_family: str = "sdxl"
    positive_prompt: str = "a clean, high quality digital illustration"
    negative_prompt: str = "low quality, blurry, text, watermark"
    checkpoint_name: str | None = None
    width: int = 1024
    height: int = 1024
    steps: int = 20
    cfg: float = 7.0
    seed: int = 0
    batch_size: int = 1


def normalize_object_info(value: Any) -> ObjectInfoCatalog:
    """Accept wrapped and raw ComfyUI object_info shapes."""

    if not isinstance(value, dict):
        return ObjectInfoCatalog(nodes={}, models={})
    nodes = value.get("nodes")
    if isinstance(nodes, dict):
        models = value.get("models")
        return ObjectInfoCatalog(nodes=nodes, models=models if isinstance(models, dict) else {})
    models = value.get("models")
    raw_nodes = {
        str(key): item for key, item in value.items() if key != "models" and isinstance(item, dict)
    }
    return ObjectInfoCatalog(nodes=raw_nodes, models=models if isinstance(models, dict) else {})


class ObjectInfoIndex:
    """Small schema index over ComfyUI's object_info payload."""

    def __init__(self, object_info: Any) -> None:
        self.catalog = normalize_object_info(object_info)
        self.nodes = self.catalog.nodes
        self.models = self.catalog.models

    def summary(self) -> dict[str, Any]:
        categories: dict[str, int] = {}
        for schema in self.nodes.values():
            category = _clean_str(schema.get("category")) if isinstance(schema, dict) else None
            if category:
                categories[category] = categories.get(category, 0) + 1
        families = detect_model_families(self.catalog)
        return {
            "nodeCount": len(self.nodes),
            "modelTypes": sorted(str(key) for key in self.models),
            "topCategories": [
                {"category": key, "nodeCount": count}
                for key, count in sorted(categories.items(), key=lambda item: (-item[1], item[0]))[
                    :20
                ]
            ],
            "modelFamilyInference": families,
            "keyNodes": {
                role: _first_existing(self.nodes, candidates)
                for role, candidates in {
                    "checkpointLoader": ("CheckpointLoaderSimple", "CheckpointLoader"),
                    "textEncode": ("CLIPTextEncodeSDXL", "CLIPTextEncode"),
                    "latent": ("EmptyLatentImage", "EmptySD3LatentImage"),
                    "sampler": ("KSampler",),
                    "decode": ("VAEDecode",),
                    "imageOutput": ("SaveImage", "PreviewImage"),
                }.items()
            },
        }

    def search(self, query: str = "", *, limit: int = 20, model_family: str | None = None) -> dict:
        terms = _terms(query)
        family = _clean_str(model_family)
        rows: list[tuple[int, str, dict[str, Any]]] = []
        for class_type, schema in self.nodes.items():
            if not isinstance(schema, dict):
                continue
            family_hints = node_family_hints(class_type, schema)
            if family and family not in family_hints:
                continue
            haystack = _node_search_text(class_type, schema)
            score = 1 if not terms else sum(1 for term in terms if term in haystack)
            if terms and score <= 0:
                continue
            if class_type.lower() == query.strip().lower():
                score += 10
            score += min(len(family_hints), 3)
            rows.append((score, class_type, node_schema_summary(class_type, schema)))
        rows.sort(key=lambda item: (-item[0], item[1].lower()))
        return {
            "query": query,
            "modelFamily": family,
            "limit": max(1, int(limit)),
            "matches": [row for _, _class_type, row in rows[: max(1, int(limit))]],
        }

    def get_node_schema(self, class_type: str, *, include_raw: bool = True) -> dict[str, Any]:
        schema = self.nodes.get(class_type)
        if not isinstance(schema, dict):
            return {"found": False, "classType": class_type}
        result = node_schema_summary(class_type, schema)
        result["found"] = True
        if include_raw:
            result["rawSchema"] = copy.deepcopy(schema)
        return result

    def agent_catalog(
        self,
        *,
        include_nodes: bool = True,
        include_raw: bool = False,
        role: str | None = None,
        model_family: str | None = None,
        query: str = "",
        limit: int = 200,
    ) -> dict[str, Any]:
        entries = self._agent_entries(include_raw=include_raw)
        limited, filter_schema = _filter_agent_entries(
            entries,
            role=role,
            model_family=model_family,
            query=query,
            limit=limit,
        )
        nodes: dict[str, Any]
        if include_nodes:
            nodes = {entry["classType"]: entry for entry in limited}
        else:
            nodes = {"omitted": True, "matchedNodeCount": filter_schema["matchedNodeCount"]}
        return {
            "schemaVersion": AGENT_CATALOG_SCHEMA_VERSION,
            "format": AGENT_CATALOG_FORMAT,
            "source": {
                "nodeCount": len(self.nodes),
                "modelTypes": sorted(str(key) for key in self.models),
                "objectInfoHash": _object_info_hash(self.catalog),
            },
            "filters": {
                **filter_schema,
                "includeNodes": include_nodes,
                "includeRaw": include_raw,
            },
            "roleDefinitions": dict(AGENT_ROLE_DEFINITIONS),
            "summary": {
                "roles": _agent_role_summaries(entries),
                "families": self.summary()["modelFamilyInference"],
                "topCategories": self.summary()["topCategories"],
            },
            "roles": _agent_role_map(entries),
            "nodes": nodes,
            "recipes": _agent_recipes(entries),
            "overrides": {},
        }

    def nodes_by_role(
        self,
        role: str,
        *,
        include_raw: bool = False,
        model_family: str | None = None,
        query: str = "",
        limit: int = 50,
    ) -> dict[str, Any]:
        entries = self._agent_entries(include_raw=include_raw)
        limited, filter_schema = _filter_agent_entries(
            entries,
            role=role,
            model_family=model_family,
            query=query,
            limit=limit,
        )
        return {
            "schemaVersion": AGENT_CATALOG_SCHEMA_VERSION,
            "format": AGENT_CATALOG_FORMAT,
            "role": filter_schema["role"],
            "filters": filter_schema,
            "nodes": limited,
        }

    def _agent_entries(self, *, include_raw: bool = False) -> list[dict[str, Any]]:
        entries: list[dict[str, Any]] = []
        for class_type, schema in self.nodes.items():
            if not isinstance(schema, dict):
                continue
            entries.append(agent_node_summary(class_type, schema, include_raw=include_raw))
        entries.sort(key=lambda item: str(item["classType"]).lower())
        return entries


def node_schema_summary(class_type: str, schema: dict[str, Any]) -> dict[str, Any]:
    inputs = schema.get("input") if isinstance(schema.get("input"), dict) else {}
    required = inputs.get("required") if isinstance(inputs.get("required"), dict) else {}
    optional = inputs.get("optional") if isinstance(inputs.get("optional"), dict) else {}
    return {
        "classType": class_type,
        "displayName": _clean_str(schema.get("display_name")) or _clean_str(schema.get("name")),
        "category": _clean_str(schema.get("category")),
        "familyHints": sorted(node_family_hints(class_type, schema)),
        "inputs": {
            "required": {
                key: normalize_input_spec(value) for key, value in sorted(required.items())
            },
            "optional": {
                key: normalize_input_spec(value) for key, value in sorted(optional.items())
            },
        },
        "outputs": _outputs_from_schema(schema),
    }


def agent_node_summary(
    class_type: str,
    schema: dict[str, Any],
    *,
    include_raw: bool = False,
) -> dict[str, Any]:
    summary = node_schema_summary(class_type, schema)
    inputs = summary["inputs"] if isinstance(summary.get("inputs"), dict) else {}
    connection_inputs = _connection_inputs_by_section(inputs)
    input_connection_types = sorted(
        {
            connection_type
            for section in connection_inputs.values()
            for connection_type in section.values()
        }
    )
    output_types = _output_types(summary)
    role_confidence = _agent_role_confidence(class_type, schema, summary)
    entry = {
        "classType": class_type,
        "displayName": summary.get("displayName"),
        "category": summary.get("category"),
        "roles": _sort_roles(role_confidence),
        "roleConfidence": role_confidence,
        "families": list(summary.get("familyHints", [])),
        "inputs": copy.deepcopy(inputs),
        "outputs": copy.deepcopy(summary.get("outputs", [])),
        "inputConnectionTypes": input_connection_types,
        "inputConnections": connection_inputs,
        "outputTypes": output_types,
        "scalarInputs": _scalar_input_names(inputs),
    }
    if include_raw:
        entry["rawSchema"] = _json_safe(schema)
    return entry


def normalize_input_spec(spec: Any) -> dict[str, Any]:
    head, options = _input_head_options(spec)
    result: dict[str, Any] = {"kind": "unknown"}
    if is_combo_head(head):
        result["kind"] = "enum"
        # Absent for remote combos, whose choices ComfyUI resolves at runtime.
        choices = combo_choices(head, options)
        if choices is not None:
            result["choices"] = choices
        if is_multiselect(options):
            result["multiselect"] = True
    elif isinstance(head, str):
        upper = head.upper()
        if upper in CONNECTION_TYPES:
            result["kind"] = "connection"
            result["connectionType"] = upper
        elif upper in SCALAR_TYPES:
            result["kind"] = upper.lower()
        else:
            result["kind"] = "custom"
            result["type"] = head
    if options:
        if "default" in options:
            result["default"] = copy.deepcopy(options["default"])
        range_values = {key: options[key] for key in ("min", "max", "step") if key in options}
        if range_values:
            result["range"] = range_values
        if options.get("multiline"):
            result["multiline"] = True
    return result


def detect_model_families(catalog: ObjectInfoCatalog) -> dict[str, Any]:
    evidence: dict[str, list[str]] = {family: [] for family in MODEL_FAMILY_PATTERNS}
    for class_type, schema in catalog.nodes.items():
        if not isinstance(schema, dict):
            continue
        text = _node_search_text(class_type, schema)
        for family, patterns in MODEL_FAMILY_PATTERNS.items():
            matched = [pattern for pattern in patterns if _pattern_in_text(pattern, text)]
            if matched:
                evidence[family].append(f"node:{class_type}:{matched[0]}")
    for model_type, models in catalog.models.items():
        if not isinstance(models, list):
            continue
        for model in models[:200]:
            text = str(model).lower()
            for family, patterns in MODEL_FAMILY_PATTERNS.items():
                matched = [pattern for pattern in patterns if _pattern_in_text(pattern, text)]
                if matched:
                    evidence[family].append(f"model:{model_type}:{model}")
    families = []
    for family, items in evidence.items():
        if not items:
            continue
        families.append(
            {
                "family": family,
                "score": len(items),
                "confidence": _confidence(len(items)),
                "evidence": items[:10],
            }
        )
    families.sort(key=lambda item: (-int(item["score"]), str(item["family"])))
    return {
        "families": families,
        "bestGuess": families[0]["family"] if families else "unknown",
    }


def node_family_hints(class_type: str, schema: dict[str, Any]) -> set[str]:
    text = _node_search_text(class_type, schema)
    return {
        family
        for family, patterns in MODEL_FAMILY_PATTERNS.items()
        if any(_pattern_in_text(pattern, text) for pattern in patterns)
    }


def build_agent_catalog(
    object_info: Any,
    *,
    include_nodes: bool = True,
    include_raw: bool = False,
    role: str | None = None,
    model_family: str | None = None,
    query: str = "",
    limit: int = 200,
) -> dict[str, Any]:
    return ObjectInfoIndex(object_info).agent_catalog(
        include_nodes=include_nodes,
        include_raw=include_raw,
        role=role,
        model_family=model_family,
        query=query,
        limit=limit,
    )


def build_workflow_draft(object_info: Any, request: WorkflowDraftRequest) -> dict[str, Any]:
    index = ObjectInfoIndex(object_info)
    if request.kind != "sdxl_t2i":
        raise ValueError(f"Unsupported ComfyUI workflow draft kind: {request.kind}")
    builder = _SdxlT2IBuilder(index, request)
    return builder.build()


class _SdxlT2IBuilder:
    def __init__(self, index: ObjectInfoIndex, request: WorkflowDraftRequest) -> None:
        self.index = index
        self.request = request
        self.nodes = index.nodes
        self.warnings: list[str] = []

    def build(self) -> dict[str, Any]:
        checkpoint = self._require_node("CheckpointLoaderSimple", "CheckpointLoader")
        text = self._require_node("CLIPTextEncodeSDXL", "CLIPTextEncode")
        latent = self._require_node("EmptyLatentImage")
        sampler = self._require_node("KSampler")
        decode = self._require_node("VAEDecode")
        output = self._require_node("SaveImage", "PreviewImage")

        checkpoint_node = "1"
        positive_node = "2"
        negative_node = "3"
        latent_node = "4"
        sampler_node = "5"
        decode_node = "6"
        output_node = "7"

        workflow: dict[str, Any] = {
            checkpoint_node: {
                "class_type": checkpoint,
                "inputs": self._checkpoint_inputs(checkpoint),
                "_meta": {"title": "Checkpoint"},
            },
            positive_node: {
                "class_type": text,
                "inputs": self._text_encode_inputs(
                    text,
                    self.request.positive_prompt,
                    [checkpoint_node, self._output_index(checkpoint, "CLIP", 1)],
                ),
                "_meta": {"title": "Positive Prompt"},
            },
            negative_node: {
                "class_type": text,
                "inputs": self._text_encode_inputs(
                    text,
                    self.request.negative_prompt,
                    [checkpoint_node, self._output_index(checkpoint, "CLIP", 1)],
                ),
                "_meta": {"title": "Negative Prompt"},
            },
            latent_node: {
                "class_type": latent,
                "inputs": self._latent_inputs(latent),
                "_meta": {"title": "Latent"},
            },
            sampler_node: {
                "class_type": sampler,
                "inputs": self._sampler_inputs(
                    sampler,
                    model_ref=[checkpoint_node, self._output_index(checkpoint, "MODEL", 0)],
                    positive_ref=[positive_node, 0],
                    negative_ref=[negative_node, 0],
                    latent_ref=[latent_node, 0],
                ),
                "_meta": {"title": "Sampler"},
            },
            decode_node: {
                "class_type": decode,
                "inputs": self._decode_inputs(
                    decode,
                    samples_ref=[sampler_node, self._output_index(sampler, "LATENT", 0)],
                    vae_ref=[checkpoint_node, self._output_index(checkpoint, "VAE", 2)],
                ),
                "_meta": {"title": "Decode"},
            },
            output_node: {
                "class_type": output,
                "inputs": self._output_inputs(
                    output,
                    image_ref=[decode_node, self._output_index(decode, "IMAGE", 0)],
                ),
                "_meta": {"title": "Output"},
            },
        }
        self._fill_missing_scalar_defaults(workflow)
        validation = validate_workflow(workflow, self.nodes)
        mappings, parameters = self._mappings(
            workflow, positive_node, negative_node, latent_node, sampler_node, output_node
        )
        gateway_file = {
            "schema_version": "1.0",
            "workflow": copy.deepcopy(workflow),
            "node_mappings": copy.deepcopy(mappings),
            "dependencies": {
                "custom_nodes": sorted({node["class_type"] for node in workflow.values()})
            },
            "target_server_id": "local",
        }
        return {
            "kind": self.request.kind,
            "modelFamily": {
                "requested": self.request.model_family,
                "inference": self.index.summary()["modelFamilyInference"],
            },
            "workflow": workflow,
            "gatewayFile": gateway_file,
            "nodeMappings": mappings,
            "parameters": parameters,
            "outputs": [
                {
                    "internalId": "output_image",
                    "displayName": "Output image",
                    "mode": "new_layer_above",
                    "gatewayRef": {"node_id": output_node},
                }
            ],
            "selected": {
                "checkpoint": workflow[checkpoint_node]["inputs"].get("ckpt_name"),
                "checkpointNode": checkpoint,
                "textEncodeNode": text,
                "latentNode": latent,
                "samplerNode": sampler,
                "decodeNode": decode,
                "outputNode": output,
            },
            "validation": validation,
            "warnings": self.warnings + validation.get("warnings", []),
        }

    def _require_node(self, *candidates: str) -> str:
        found = _first_existing(self.nodes, candidates)
        if found is None:
            raise ValueError(f"Missing required ComfyUI node. Tried: {', '.join(candidates)}")
        return found

    def _checkpoint_inputs(self, class_type: str) -> dict[str, Any]:
        inputs = self._default_scalar_inputs(class_type)
        choices = self._choices_for(class_type, "ckpt_name")
        selected = _select_checkpoint(
            choices,
            requested=self.request.checkpoint_name,
            model_family=self.request.model_family,
        )
        if selected is not None:
            inputs["ckpt_name"] = selected
        elif "ckpt_name" in self._input_specs(class_type):
            self.warnings.append("CheckpointLoaderSimple.ckpt_name has no choices or default.")
        return inputs

    def _text_encode_inputs(
        self, class_type: str, text: str, clip_ref: list[Any]
    ) -> dict[str, Any]:
        inputs = self._default_scalar_inputs(class_type)
        specs = self._input_specs(class_type)
        for key, spec in specs.items():
            kind = normalize_input_spec(spec)
            if kind.get("connectionType") == "CLIP":
                inputs[key] = clip_ref
            elif key in {"text", "text_g", "text_l", "prompt", "caption"}:
                inputs[key] = text
            elif key in {"width", "target_width"}:
                inputs[key] = self.request.width
            elif key in {"height", "target_height"}:
                inputs[key] = self.request.height
            elif key in {"crop_w", "crop_h"}:
                inputs[key] = 0
        return inputs

    def _latent_inputs(self, class_type: str) -> dict[str, Any]:
        inputs = self._default_scalar_inputs(class_type)
        inputs["width"] = self.request.width
        inputs["height"] = self.request.height
        inputs["batch_size"] = self.request.batch_size
        return inputs

    def _sampler_inputs(
        self,
        class_type: str,
        *,
        model_ref: list[Any],
        positive_ref: list[Any],
        negative_ref: list[Any],
        latent_ref: list[Any],
    ) -> dict[str, Any]:
        inputs = self._default_scalar_inputs(class_type)
        specs = self._input_specs(class_type)
        for key, spec in specs.items():
            kind = normalize_input_spec(spec)
            connection_type = kind.get("connectionType")
            if connection_type == "MODEL":
                inputs[key] = model_ref
            elif connection_type == "CONDITIONING" and "negative" in key.lower():
                inputs[key] = negative_ref
            elif connection_type == "CONDITIONING":
                inputs[key] = positive_ref
            elif connection_type == "LATENT":
                inputs[key] = latent_ref
        inputs["seed"] = self.request.seed
        inputs["steps"] = self.request.steps
        inputs["cfg"] = self.request.cfg
        inputs["sampler_name"] = _prefer_choice(
            self._choices_for(class_type, "sampler_name"),
            ("dpmpp_2m_sde_gpu", "dpmpp_2m_sde", "euler"),
        )
        inputs["scheduler"] = _prefer_choice(
            self._choices_for(class_type, "scheduler"),
            ("karras", "normal", "simple"),
        )
        inputs["denoise"] = 1.0
        return {key: value for key, value in inputs.items() if value is not None}

    def _decode_inputs(
        self,
        class_type: str,
        *,
        samples_ref: list[Any],
        vae_ref: list[Any],
    ) -> dict[str, Any]:
        inputs = self._default_scalar_inputs(class_type)
        for key, spec in self._input_specs(class_type).items():
            kind = normalize_input_spec(spec)
            if kind.get("connectionType") == "LATENT":
                inputs[key] = samples_ref
            elif kind.get("connectionType") == "VAE":
                inputs[key] = vae_ref
        return inputs

    def _output_inputs(self, class_type: str, *, image_ref: list[Any]) -> dict[str, Any]:
        inputs = self._default_scalar_inputs(class_type)
        for key, spec in self._input_specs(class_type).items():
            kind = normalize_input_spec(spec)
            if kind.get("connectionType") == "IMAGE":
                inputs[key] = image_ref
        inputs.setdefault("filename_prefix", "LoomStroke")
        return inputs

    def _mappings(
        self,
        workflow: dict[str, Any],
        positive_node: str,
        negative_node: str,
        latent_node: str,
        sampler_node: str,
        output_node: str,
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        mappings: dict[str, Any] = {"output_image": {"node_id": output_node}}
        params: list[dict[str, Any]] = []

        def add_param(
            internal_id: str,
            display_name: str,
            node_id: str,
            input_key: str,
            param_type: str,
        ) -> None:
            value = workflow[node_id]["inputs"].get(input_key)
            ref = {"node_id": node_id, "input": input_key}
            mappings[internal_id] = ref
            params.append(
                {
                    "internalId": internal_id,
                    "displayName": display_name,
                    "type": param_type,
                    "default": value,
                    "gatewayRef": dict(ref),
                }
            )

        positive_inputs = workflow[positive_node]["inputs"]
        if "text_l" in positive_inputs:
            add_param("prompt_content", "Prompt content", positive_node, "text_l", "string")
            if "text_g" in positive_inputs:
                add_param("prompt_style", "Prompt style", positive_node, "text_g", "string")
        elif "text" in positive_inputs:
            add_param("prompt_content", "Prompt", positive_node, "text", "string")

        negative_inputs = workflow[negative_node]["inputs"]
        if "text_l" in negative_inputs:
            add_param("negative_prompt", "Negative prompt", negative_node, "text_l", "string")
        elif "text" in negative_inputs:
            add_param("negative_prompt", "Negative prompt", negative_node, "text", "string")

        for internal_id, display_name, node_id, key, param_type in (
            ("width", "Width", latent_node, "width", "int"),
            ("height", "Height", latent_node, "height", "int"),
            ("seed", "Seed", sampler_node, "seed", "int"),
            ("steps", "Steps", sampler_node, "steps", "int"),
            ("cfg", "CFG", sampler_node, "cfg", "float"),
            ("sampler_name", "Sampler", sampler_node, "sampler_name", "string"),
            ("scheduler", "Scheduler", sampler_node, "scheduler", "string"),
        ):
            if key in workflow[node_id]["inputs"]:
                add_param(internal_id, display_name, node_id, key, param_type)

        return mappings, params

    def _default_scalar_inputs(self, class_type: str) -> dict[str, Any]:
        inputs: dict[str, Any] = {}
        for key, spec in self._input_specs(class_type).items():
            default = default_from_input_spec(spec)
            if default is not _MISSING:
                inputs[key] = default
        return inputs

    def _fill_missing_scalar_defaults(self, workflow: dict[str, Any]) -> None:
        for node in workflow.values():
            class_type = node.get("class_type")
            inputs = node.setdefault("inputs", {})
            if not isinstance(class_type, str) or not isinstance(inputs, dict):
                continue
            for key, spec in self._required_input_specs(class_type).items():
                if key in inputs:
                    continue
                default = default_from_input_spec(spec)
                if default is not _MISSING:
                    inputs[key] = default

    def _input_specs(self, class_type: str) -> dict[str, Any]:
        schema = self.nodes.get(class_type)
        if not isinstance(schema, dict):
            return {}
        inputs = schema.get("input")
        if not isinstance(inputs, dict):
            return {}
        merged: dict[str, Any] = {}
        for section in ("required", "optional"):
            values = inputs.get(section)
            if isinstance(values, dict):
                merged.update(values)
        return merged

    def _required_input_specs(self, class_type: str) -> dict[str, Any]:
        schema = self.nodes.get(class_type)
        inputs = schema.get("input") if isinstance(schema, dict) else None
        required = inputs.get("required") if isinstance(inputs, dict) else None
        return required if isinstance(required, dict) else {}

    def _choices_for(self, class_type: str, input_key: str) -> list[Any]:
        spec = self._input_specs(class_type).get(input_key)
        head, options = _input_head_options(spec)
        return combo_choices(head, options) or []

    def _output_index(self, class_type: str, output_type: str, fallback: int) -> int:
        schema = self.nodes.get(class_type)
        outputs = _outputs_from_schema(schema if isinstance(schema, dict) else {})
        for index, item in enumerate(outputs):
            if str(item.get("type", "")).upper() == output_type.upper():
                return index
        return fallback


def validate_workflow(workflow: dict[str, Any], nodes: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    link_count = 0
    for node_id, node in workflow.items():
        if not isinstance(node, dict):
            errors.append(f"Node {node_id} is not an object")
            continue
        class_type = node.get("class_type")
        if not isinstance(class_type, str) or not class_type:
            errors.append(f"Node {node_id} is missing class_type")
            continue
        schema = nodes.get(class_type)
        if not isinstance(schema, dict):
            errors.append(f"Unknown node type {class_type!r} at node {node_id}")
            continue
        inputs = node.get("inputs")
        if not isinstance(inputs, dict):
            errors.append(f"Node {node_id} has no inputs object")
            continue
        required = _required_specs(schema)
        for key, spec in required.items():
            if key not in inputs:
                errors.append(f"Node {node_id} {class_type}.{key} is required")
                continue
            kind = normalize_input_spec(spec)
            connection_type = kind.get("connectionType")
            if connection_type:
                link_count += 1
                _validate_connection(
                    workflow,
                    nodes,
                    node_id=str(node_id),
                    input_key=key,
                    value=inputs[key],
                    expected_type=str(connection_type),
                    errors=errors,
                    warnings=warnings,
                )
            else:
                _validate_scalar(
                    value=inputs[key],
                    spec=spec,
                    node_id=str(node_id),
                    class_type=class_type,
                    input_key=key,
                    errors=errors,
                    warnings=warnings,
                )
    return {
        "valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "nodeCount": len(workflow),
        "linkCount": link_count,
    }


_MISSING = object()


def default_from_input_spec(spec: Any) -> Any:
    head, options = _input_head_options(spec)
    if isinstance(head, str) and head.upper() in CONNECTION_TYPES:
        return _MISSING
    if "default" in options:
        return copy.deepcopy(options["default"])
    choices = combo_choices(head, options)
    if choices:
        return copy.deepcopy(choices[0])
    return _MISSING


def request_from_arguments(arguments: dict[str, Any]) -> WorkflowDraftRequest:
    return WorkflowDraftRequest(
        kind=str(arguments.get("kind") or "sdxl_t2i"),
        model_family=str(arguments.get("modelFamily") or arguments.get("model_family") or "sdxl"),
        positive_prompt=str(
            arguments.get("positivePrompt")
            or arguments.get("positive_prompt")
            or "a clean, high quality digital illustration"
        ),
        negative_prompt=str(
            arguments.get("negativePrompt")
            or arguments.get("negative_prompt")
            or "low quality, blurry, text, watermark"
        ),
        checkpoint_name=_clean_str(
            arguments.get("checkpointName") or arguments.get("checkpoint_name")
        ),
        width=_bounded_int(arguments.get("width"), default=1024, minimum=64, maximum=8192),
        height=_bounded_int(arguments.get("height"), default=1024, minimum=64, maximum=8192),
        steps=_bounded_int(arguments.get("steps"), default=20, minimum=1, maximum=200),
        cfg=float(arguments.get("cfg", 7.0) or 7.0),
        seed=_bounded_int(arguments.get("seed"), default=0, minimum=0, maximum=2**63 - 1),
        batch_size=_bounded_int(
            arguments.get("batchSize", arguments.get("batch_size")),
            default=1,
            minimum=1,
            maximum=16,
        ),
    )


def _agent_role_confidence(  # noqa: PLR0912, PLR0915
    class_type: str,
    schema: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, str]:
    inputs = summary["inputs"] if isinstance(summary.get("inputs"), dict) else {}
    input_types = set(_input_connection_types(inputs))
    output_types = set(_output_types(summary))
    input_names = {name.lower() for _section, name, _spec in _iter_normalized_inputs(inputs)}
    text = _node_identity_text(class_type, schema)
    compact = re.sub(r"[^a-z0-9]+", "", text)
    context_passthrough = "context" in compact and "ksampler" not in compact
    roles: dict[str, str] = {}

    def add(role: str, confidence: str) -> None:
        current = roles.get(role)
        if current is None or _confidence_rank(confidence) > _confidence_rank(current):
            roles[role] = confidence

    if "checkpointloader" in compact or (
        "ckpt_name" in input_names and {"MODEL", "CLIP", "VAE"}.issubset(output_types)
    ):
        add("checkpoint_loader", "high")
    elif ("checkpoint" in text or "ckpt" in text) and "MODEL" in output_types:
        add("checkpoint_loader", "medium")

    if "checkpoint_loader" not in roles and "MODEL" in output_types:
        if "unetloader" in compact or "modelloader" in compact:
            add("model_loader", "high")
        elif "model" in text and "loader" in text:
            add("model_loader", "medium")

    if "CLIP" in output_types and "text_encoder" not in roles:
        if "cliploader" in compact or "dualcliploader" in compact:
            add("clip_loader", "high")
        elif "clip" in text and "loader" in text:
            add("clip_loader", "medium")

    if "checkpoint_loader" not in roles and "VAE" in output_types:
        if "vaeloader" in compact:
            add("vae_loader", "high")
        elif "vae" in text and "loader" in text:
            add("vae_loader", "medium")

    if "lora" in text:
        add("lora_loader", "high" if "loader" in text else "medium")

    prompt_inputs = {"text", "text_g", "text_l", "prompt", "caption"}
    if "CONDITIONING" in output_types:
        if "textencode" in compact or ("CLIP" in input_types and input_names & prompt_inputs):
            add("text_encoder", "high")
        elif input_names & prompt_inputs:
            add("text_encoder", "medium")

    if "LATENT" in output_types and "LATENT" not in input_types:
        if "emptylatent" in compact or "latentimage" in compact:
            add("latent_source", "high")
        elif "latent" in text:
            add("latent_source", "medium")

    sampler_signature = {"MODEL", "CONDITIONING", "LATENT"}.issubset(input_types)
    if (
        "LATENT" in output_types
        and not context_passthrough
        and (sampler_signature or "ksampler" in compact)
    ):
        add("sampler", "high")
    elif (
        "LATENT" in output_types
        and not context_passthrough
        and ("sampler" in text or "sample" in text)
    ):
        add("sampler", "medium")

    if "IMAGE" in output_types and {"LATENT", "VAE"}.issubset(input_types):
        add("decoder", "high")
    elif "IMAGE" in output_types and ("vaedecode" in compact or "decode" in text):
        add("decoder", "medium")

    if "IMAGE" in output_types and "IMAGE" not in input_types:
        if "loadimage" in compact or "imageinput" in compact:
            add("image_input", "high")
        elif "image" in text and ("load" in text or "input" in text):
            add("image_input", "medium")

    if "IMAGE" in input_types:
        if any(word in text for word in ("save", "preview", "output", "export")):
            add("image_output", "high" if not output_types else "medium")
        elif "saveimage" in compact or "previewimage" in compact:
            add("image_output", "high")

    if "CONTROL_NET" in input_types or "CONTROL_NET" in output_types or "controlnet" in text:
        add("controlnet", "high" if "controlnet" in text else "medium")

    if "MASK" in input_types or "MASK" in output_types:
        add("mask", "high" if "mask" in text else "medium")
    elif "mask" in text:
        add("mask", "medium")

    if "upscale" in text or "upscaler" in text:
        add("upscaler", "high")

    if "VIDEO" in input_types or "VIDEO" in output_types:
        add("video", "high")
    elif any(_pattern_in_text(pattern, text) for pattern in MODEL_FAMILY_PATTERNS["video"]):
        add("video", "medium")

    return roles


def _filter_agent_entries(
    entries: list[dict[str, Any]],
    *,
    role: str | None,
    model_family: str | None,
    query: str,
    limit: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    normalized_role = _normalize_role(role)
    family_value = _clean_str(model_family)
    family = family_value.lower() if family_value else None
    terms = _terms(query)
    limit_value = _bounded_int(limit, default=200, minimum=1, maximum=500)
    filtered: list[dict[str, Any]] = []
    for entry in entries:
        if normalized_role and normalized_role not in entry.get("roles", []):
            continue
        families = set(entry.get("families", []))
        if family and families and family not in families:
            continue
        haystack = _agent_entry_search_text(entry)
        if terms and not all(term in haystack for term in terms):
            continue
        filtered.append(entry)
    filtered.sort(
        key=lambda entry: (
            _role_preference_index(entry, normalized_role),
            -_role_confidence_for(entry, normalized_role),
            str(entry.get("classType", "")).lower(),
        )
    )
    return filtered[:limit_value], {
        "role": normalized_role,
        "modelFamily": family,
        "query": query,
        "limit": limit_value,
        "matchedNodeCount": len(filtered),
        "returnedNodeCount": min(len(filtered), limit_value),
    }


def _agent_role_summaries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for role in AGENT_ROLE_ORDER:
        role_entries = [entry for entry in entries if role in entry.get("roles", [])]
        if not role_entries:
            continue
        confidence_counts = {"high": 0, "medium": 0, "low": 0}
        for entry in role_entries:
            confidence = str(entry.get("roleConfidence", {}).get(role, "low"))
            if confidence not in confidence_counts:
                confidence = "low"
            confidence_counts[confidence] += 1
        summaries.append(
            {
                "role": role,
                "description": AGENT_ROLE_DEFINITIONS[role],
                "nodeCount": len(role_entries),
                "confidence": confidence_counts,
            }
        )
    return summaries


def _agent_role_map(entries: list[dict[str, Any]]) -> dict[str, list[str]]:
    role_map: dict[str, list[str]] = {role: [] for role in AGENT_ROLE_ORDER}
    for entry in entries:
        class_type = str(entry.get("classType", ""))
        if not class_type:
            continue
        for role in entry.get("roles", []):
            if role in role_map:
                role_map[role].append(class_type)
    return {role: sorted(values) for role, values in role_map.items() if values}


def _agent_recipes(entries: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "sdxl_t2i": {
            "modelFamily": "sdxl",
            "workflowKind": "sdxl_t2i",
            "builderTool": "loomstroke_comfyui_build_workflow_draft",
            "requiredRoles": [
                "checkpoint_loader",
                "text_encoder",
                "latent_source",
                "sampler",
                "decoder",
                "image_output",
            ],
            "preferredNodes": {
                "checkpoint_loader": _first_role_node(
                    entries,
                    "checkpoint_loader",
                    ("CheckpointLoaderSimple", "CheckpointLoader"),
                ),
                "text_encoder": _first_role_node(
                    entries,
                    "text_encoder",
                    ("CLIPTextEncodeSDXL", "CLIPTextEncode"),
                ),
                "latent_source": _first_role_node(
                    entries,
                    "latent_source",
                    ("EmptyLatentImage", "EmptySD3LatentImage"),
                ),
                "sampler": _first_role_node(entries, "sampler", ("KSampler",)),
                "decoder": _first_role_node(entries, "decoder", ("VAEDecode",)),
                "image_output": _first_role_node(
                    entries,
                    "image_output",
                    ("SaveImage", "PreviewImage"),
                ),
            },
        }
    }


def _first_role_node(
    entries: list[dict[str, Any]],
    role: str,
    preferred: tuple[str, ...],
) -> str | None:
    by_class = {str(entry.get("classType", "")): entry for entry in entries}
    for class_type in preferred:
        entry = by_class.get(class_type)
        if entry and role in entry.get("roles", []):
            return class_type
    role_entries = [entry for entry in entries if role in entry.get("roles", [])]
    role_entries.sort(
        key=lambda entry: (-_role_confidence_for(entry, role), str(entry["classType"]).lower())
    )
    return str(role_entries[0]["classType"]) if role_entries else None


def _connection_inputs_by_section(inputs: dict[str, Any]) -> dict[str, dict[str, str]]:
    sections = {"required": {}, "optional": {}}
    for section, name, spec in _iter_normalized_inputs(inputs):
        connection_type = spec.get("connectionType")
        if isinstance(connection_type, str):
            sections[section][name] = connection_type
    return sections


def _input_connection_types(inputs: dict[str, Any]) -> list[str]:
    return sorted(
        {
            str(spec["connectionType"])
            for _section, _name, spec in _iter_normalized_inputs(inputs)
            if isinstance(spec.get("connectionType"), str)
        }
    )


def _scalar_input_names(inputs: dict[str, Any]) -> list[str]:
    return sorted(
        name
        for _section, name, spec in _iter_normalized_inputs(inputs)
        if not isinstance(spec.get("connectionType"), str)
    )


def _iter_normalized_inputs(
    inputs: dict[str, Any],
) -> Iterator[tuple[str, str, dict[str, Any]]]:
    for section in ("required", "optional"):
        values = inputs.get(section)
        if not isinstance(values, dict):
            continue
        for name, spec in values.items():
            if isinstance(spec, dict):
                yield section, str(name), spec


def _output_types(summary: dict[str, Any]) -> list[str]:
    types: list[str] = []
    for output in summary.get("outputs", []):
        if not isinstance(output, dict):
            continue
        output_type = str(output.get("type", "")).upper()
        if output_type and output_type not in types:
            types.append(output_type)
    return types


def _sort_roles(role_confidence: dict[str, str]) -> list[str]:
    return sorted(role_confidence, key=_role_sort_key)


def _role_sort_key(role: str) -> tuple[int, str]:
    try:
        return AGENT_ROLE_ORDER.index(role), role
    except ValueError:
        return len(AGENT_ROLE_ORDER), role


def _normalize_role(value: str | None) -> str | None:
    clean = _clean_str(value)
    if not clean:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "_", clean.lower()).strip("_")
    compact = normalized.replace("_", "")
    aliases = {
        "checkpointloader": "checkpoint_loader",
        "modelload": "model_loader",
        "modelloader": "model_loader",
        "cliploader": "clip_loader",
        "vaeloader": "vae_loader",
        "loraloader": "lora_loader",
        "textencode": "text_encoder",
        "textencoder": "text_encoder",
        "latentsource": "latent_source",
        "latentimage": "latent_source",
        "imageinput": "image_input",
        "imageoutput": "image_output",
        "controlnet": "controlnet",
        "upscale": "upscaler",
    }
    return aliases.get(compact, normalized)


def _agent_entry_search_text(entry: dict[str, Any]) -> str:
    chunks = [
        str(entry.get("classType", "")),
        str(entry.get("displayName", "")),
        str(entry.get("category", "")),
        " ".join(str(item) for item in entry.get("roles", [])),
        " ".join(str(item) for item in entry.get("families", [])),
        " ".join(str(item) for item in entry.get("inputConnectionTypes", [])),
        " ".join(str(item) for item in entry.get("outputTypes", [])),
        " ".join(str(item) for item in entry.get("scalarInputs", [])),
    ]
    return " ".join(chunks).lower()


def _role_confidence_for(entry: dict[str, Any], role: str | None) -> int:
    if not role:
        return 0
    confidence = str(entry.get("roleConfidence", {}).get(role, "low"))
    return _confidence_rank(confidence)


def _role_preference_index(entry: dict[str, Any], role: str | None) -> int:
    if not role:
        return 0
    preferred = AGENT_ROLE_PREFERRED_CLASSES.get(role, ())
    class_type = str(entry.get("classType", ""))
    for index, candidate in enumerate(preferred):
        if class_type == candidate:
            return index
    return len(preferred)


def _confidence_rank(confidence: str) -> int:
    return {"low": 1, "medium": 2, "high": 3}.get(confidence, 0)


def _object_info_hash(catalog: ObjectInfoCatalog) -> str:
    payload = json.dumps(
        _json_safe({"nodes": catalog.nodes, "models": catalog.models}),
        sort_keys=True,
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, ensure_ascii=False))


def _input_head_options(spec: Any) -> tuple[Any, dict[str, Any]]:
    if not isinstance(spec, (list, tuple)) or not spec:
        return None, {}
    head = spec[0]
    options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
    return head, options


def _outputs_from_schema(schema: dict[str, Any]) -> list[dict[str, Any]]:
    raw_outputs = schema.get("output")
    raw_names = schema.get("output_name")
    outputs = raw_outputs if isinstance(raw_outputs, list) else []
    names = raw_names if isinstance(raw_names, list) else []
    return [
        {
            "index": index,
            "type": str(output),
            "name": str(names[index]) if index < len(names) else str(output),
        }
        for index, output in enumerate(outputs)
    ]


def _required_specs(schema: dict[str, Any]) -> dict[str, Any]:
    inputs = schema.get("input")
    required = inputs.get("required") if isinstance(inputs, dict) else None
    return required if isinstance(required, dict) else {}


def _validate_connection(
    workflow: dict[str, Any],
    nodes: dict[str, Any],
    *,
    node_id: str,
    input_key: str,
    value: Any,
    expected_type: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    if not isinstance(value, list | tuple) or len(value) < 2:
        errors.append(f"Node {node_id}.{input_key} expects {expected_type} link")
        return
    source_id = str(value[0])
    try:
        output_index = int(value[1])
    except (TypeError, ValueError):
        errors.append(f"Node {node_id}.{input_key} has invalid output index")
        return
    source = workflow.get(source_id)
    if not isinstance(source, dict):
        errors.append(f"Node {node_id}.{input_key} links missing source node {source_id}")
        return
    source_class = source.get("class_type")
    source_schema = nodes.get(source_class) if isinstance(source_class, str) else None
    if not isinstance(source_schema, dict):
        warnings.append(f"Node {node_id}.{input_key} source schema is unavailable")
        return
    outputs = _outputs_from_schema(source_schema)
    if output_index >= len(outputs):
        errors.append(
            f"Node {node_id}.{input_key} links missing output {source_id}[{output_index}]"
        )
        return
    actual = str(outputs[output_index].get("type", "")).upper()
    if actual and actual != expected_type.upper():
        warnings.append(
            f"Node {node_id}.{input_key} expects {expected_type}, got {actual} from {source_id}"
        )


def _validate_combo(
    *,
    value: Any,
    head: Any,
    options: dict[str, Any],
    node_id: str,
    class_type: str,
    input_key: str,
    errors: list[str],
) -> None:
    choices = combo_choices(head, options)
    # Indeterminate (remote combo) or declared-empty: validating would reject
    # every value, which is worse than not validating at all.
    if not choices:
        return
    candidates = value if is_multiselect(options) and isinstance(value, list) else [value]
    for candidate in candidates:
        if candidate not in choices:
            errors.append(
                f"Node {node_id} {class_type}.{input_key} has invalid choice {candidate!r}"
            )


def _validate_scalar(
    *,
    value: Any,
    spec: Any,
    node_id: str,
    class_type: str,
    input_key: str,
    errors: list[str],
    warnings: list[str],
) -> None:
    head, options = _input_head_options(spec)
    if is_combo_head(head):
        _validate_combo(
            value=value,
            head=head,
            options=options,
            node_id=node_id,
            class_type=class_type,
            input_key=input_key,
            errors=errors,
        )
        return
    if not isinstance(head, str):
        return
    upper = head.upper()
    if upper == "INT" and (isinstance(value, bool) or not isinstance(value, int)):
        warnings.append(f"Node {node_id} {class_type}.{input_key} expects INT")
    elif upper == "FLOAT" and (isinstance(value, bool) or not isinstance(value, int | float)):
        warnings.append(f"Node {node_id} {class_type}.{input_key} expects FLOAT")
    elif upper == "STRING" and not isinstance(value, str):
        warnings.append(f"Node {node_id} {class_type}.{input_key} expects STRING")
    elif upper == "BOOLEAN" and not isinstance(value, bool):
        warnings.append(f"Node {node_id} {class_type}.{input_key} expects BOOLEAN")
    for bound in ("min", "max"):
        if bound in options and isinstance(value, int | float):
            limit = options[bound]
            if isinstance(limit, int | float):
                if bound == "min" and value < limit:
                    errors.append(f"Node {node_id} {class_type}.{input_key} is below minimum")
                elif bound == "max" and value > limit:
                    errors.append(f"Node {node_id} {class_type}.{input_key} is above maximum")


def _select_checkpoint(
    choices: list[Any],
    *,
    requested: str | None,
    model_family: str,
) -> Any:
    if not choices:
        return None
    if requested:
        for choice in choices:
            if str(choice) == requested:
                return choice
        requested_lower = requested.lower()
        for choice in choices:
            if requested_lower in str(choice).lower():
                return choice
    patterns = MODEL_FAMILY_PATTERNS.get(model_family, ())
    for choice in choices:
        text = str(choice).lower()
        if any(_pattern_in_text(pattern, text) for pattern in patterns):
            return choice
    return choices[0]


def _prefer_choice(choices: list[Any], preferred: tuple[str, ...]) -> Any:
    if not choices:
        return None
    lower_map = {str(choice).lower(): choice for choice in choices}
    for item in preferred:
        if item.lower() in lower_map:
            return lower_map[item.lower()]
    return choices[0]


def _first_existing(nodes: dict[str, Any], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in nodes:
            return candidate
    return None


def _node_search_text(class_type: str, schema: dict[str, Any]) -> str:
    chunks = [class_type]
    for key in ("name", "display_name", "category", "description"):
        value = schema.get(key)
        if isinstance(value, str):
            chunks.append(value)
    inputs = schema.get("input")
    if isinstance(inputs, dict):
        for section in ("required", "optional"):
            values = inputs.get(section)
            if not isinstance(values, dict):
                continue
            for key, spec in values.items():
                chunks.append(str(key))
                head, options = _input_head_options(spec)
                if isinstance(head, str):
                    chunks.append(head)
                choices = combo_choices(head, options)
                if choices:
                    chunks.extend(str(choice) for choice in choices[:100])
    return " ".join(chunks).lower()


def _node_identity_text(class_type: str, schema: dict[str, Any]) -> str:
    chunks = [class_type]
    for key in ("name", "display_name", "category", "description"):
        value = schema.get(key)
        if isinstance(value, str):
            chunks.append(value)
    return " ".join(chunks).lower()


def _pattern_in_text(pattern: str, text: str) -> bool:
    if pattern == "xl":
        return re.search(r"(^|[^a-z0-9])xl([^a-z0-9]|$)", text) is not None
    return pattern in text


def _terms(query: str) -> list[str]:
    return [term for term in re.split(r"\s+", query.lower().strip()) if term]


def _clean_str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _confidence(score: int) -> str:
    if score >= 5:
        return "high"
    if score >= 2:
        return "medium"
    return "low"


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


__all__ = [
    "AGENT_CATALOG_FORMAT",
    "AGENT_CATALOG_SCHEMA_VERSION",
    "ObjectInfoIndex",
    "WorkflowDraftRequest",
    "agent_node_summary",
    "build_agent_catalog",
    "build_workflow_draft",
    "normalize_object_info",
    "request_from_arguments",
    "validate_workflow",
]
