"""Create portable presets from standalone image panel settings."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from .editor.export import (
    default_output_choice,
    default_param_choice,
    default_slot_choice,
    export_preset,
)
from .editor.metadata_form import ExportMetadata
from .gateway_protocol import AuthorChoices

REGISTER_PANEL_PRESET = "ai_effect.register_panel_preset"


@dataclass
class PanelPresetRequest:
    gateway_id: str
    name: str
    settings: dict[str, Any]
    preset: Any = None


def register_panel_preset(request: PanelPresetRequest, registry: Any, library: Any) -> Any:
    """Use the Editor's authoring and library import validation without network I/O."""
    if request.gateway_id not in {"nanobanana", "gptimage2"}:
        raise ValueError("Unsupported panel gateway")
    gateway = registry.get(request.gateway_id)
    candidates = gateway.detect_candidates({})
    settings = request.settings
    values = {**settings["options"], "prompt": settings["prompt"]}
    parameters = []
    for index, candidate in enumerate(candidates.parameters):
        choice = default_param_choice(candidate)
        key = candidate.gateway_ref["param"]
        value = values[key]
        # NanoBanana represents automatic resolution as an empty enum value.
        choice.default = "" if key == "image_size" and value is None else value
        if choice.type == "enum" and value and value not in (choice.choices or []):
            choice.choices = [*(choice.choices or []), value]
        choice.order = index
        parameters.append(choice)
    count = settings["input_count"]
    if not isinstance(count, int) or not 0 <= count <= len(candidates.input_slots):
        raise ValueError("Invalid input slot count")
    slots = []
    for index, candidate in enumerate(candidates.input_slots[:count]):
        choice = default_slot_choice(candidate)
        choice.order = index
        slots.append(choice)
    output = default_output_choice(candidates.outputs[0])
    output.mode = "self" if settings["replace_target"] else "new_layer_above"
    choices = AuthorChoices(slots, parameters, [output], [])
    # Library import supplies collision protection and atomic installation.
    with TemporaryDirectory(prefix="panel-preset-") as directory:
        preset_id = str(uuid4())
        path = export_preset(
            Path(directory) / f"{preset_id}.lsfx",
            gateway,
            None,
            choices,
            ExportMetadata(name=request.name),
            preset_id=preset_id,
        )
        return library.import_lsfx(path)
