"""Typed structures for the GPT Image 2 plugin."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

try:  # pragma: no cover - fallback for type checkers
    from typing import Literal
except ImportError:  # pragma: no cover
    from typing import Literal  # type: ignore

from .input_images import normalize_layer_ids

Model = Literal[
    "gpt-image-2",
    "gpt-image-2-2026-04-21",
    "gpt-image-2.5-sunburst",
    "gpt-image-2.5-sunburst-2026-09-08",
    "gpt-image-2.5-flare",
    "gpt-image-2.5-flare-2026-09-08",
]
ImageSize = str
Quality = Literal["auto", "low", "medium", "high"]
OutputFormat = Literal["png", "jpeg", "webp"]
Background = Literal["auto", "opaque"]
Moderation = Literal["auto", "low"]

DEFAULT_MODEL: Model = "gpt-image-2"
ALL_MODELS: list[Model] = [
    "gpt-image-2",
    "gpt-image-2-2026-04-21",
    "gpt-image-2.5-sunburst",
    "gpt-image-2.5-flare",
    "gpt-image-2.5-sunburst-2026-09-08",
    "gpt-image-2.5-flare-2026-09-08",
]
MODEL_DISPLAY_NAMES: dict[Model, str] = {
    "gpt-image-2": "GPT Image 2",
    "gpt-image-2-2026-04-21": "GPT Image 2 (2026-04-21)",
    "gpt-image-2.5-sunburst": "GPT Image 2.5 Sunburst",
    "gpt-image-2.5-flare": "GPT Image 2.5 Flare",
    "gpt-image-2.5-sunburst-2026-09-08": "GPT Image 2.5 Sunburst (2026-09-08)",
    "gpt-image-2.5-flare-2026-09-08": "GPT Image 2.5 Flare (2026-09-08)",
}

ALL_SIZES: list[ImageSize] = [
    "auto",
    "1024x1024",
    "1024x1536",
    "1536x1024",
    "1536x864",
    "864x1536",
    "2048x2048",
    "2048x1152",
    "1152x2048",
    "3840x2160",
    "2160x3840",
]
ALL_QUALITIES: list[Quality] = ["auto", "low", "medium", "high"]
ALL_OUTPUT_FORMATS: list[OutputFormat] = ["png", "jpeg", "webp"]
ALL_BACKGROUNDS: list[Background] = ["auto", "opaque"]
ALL_MODERATION: list[Moderation] = ["auto", "low"]


def _coerce_choice(value: Any | None, choices: list[str], default: str, label: str) -> str:
    if value in (None, "", b""):
        return default
    if isinstance(value, str):
        normalized = value.strip()
        if normalized in choices:
            return normalized
    raise ValueError(f"Invalid GPT Image 2 {label}: {value!r}")


def _coerce_target(value: Any | None) -> str | None:
    if value in (None, "", b""):
        return None
    return str(value)


def _coerce_size(value: Any | None) -> ImageSize:
    if value in (None, "", b""):
        return "1024x1024"
    if isinstance(value, str):
        normalized = value.strip()
        if normalized == "auto":
            return normalized
        match = re.fullmatch(r"([0-9]{1,4})x([0-9]{1,4})", normalized)
        if match:
            width, height = map(int, match.groups())
            if (
                0 < width <= 3840
                and 0 < height <= 3840
                and width % 16 == 0
                and height % 16 == 0
                and max(width, height) <= 3 * min(width, height)
                and 655_360 <= width * height <= 8_294_400
            ):
                return normalized
    raise ValueError(f"Invalid GPT Image 2 size: {value!r}")


def _coerce_output_compression(value: Any | None) -> int | None:
    if value in (None, "", b""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid GPT Image 2 output compression: {value!r}") from exc
    if not 0 <= parsed <= 100:
        raise ValueError("GPT Image 2 output compression must be between 0 and 100")
    return parsed


@dataclass(frozen=True)
class GPTImage2Options:
    """User-configurable options for GPT Image 2 jobs."""

    target_layer_id: str | None = None
    model: Model = DEFAULT_MODEL  # noqa: RUF009
    size: ImageSize = cast(ImageSize, "1024x1024")  # noqa: RUF009
    quality: Quality = cast(Quality, "auto")  # noqa: RUF009
    output_format: OutputFormat = cast(OutputFormat, "png")  # noqa: RUF009
    output_compression: int | None = None
    background: Background = cast(Background, "auto")  # noqa: RUF009
    moderation: Moderation = cast(Moderation, "auto")  # noqa: RUF009

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | GPTImage2Options | None) -> GPTImage2Options:
        if payload is None:
            return cls()
        if isinstance(payload, cls):
            return payload
        output_format = cast(
            OutputFormat,
            _coerce_choice(
                payload.get("output_format"),
                list(ALL_OUTPUT_FORMATS),
                cls.output_format,
                "output format",
            ),
        )
        compression = _coerce_output_compression(payload.get("output_compression"))
        if output_format == "png":
            compression = None
        return cls(
            target_layer_id=_coerce_target(payload.get("target_layer_id")),
            model=cast(
                Model,
                _coerce_choice(payload.get("model"), list(ALL_MODELS), DEFAULT_MODEL, "model"),
            ),
            size=_coerce_size(payload.get("size")),
            quality=cast(
                Quality,
                _coerce_choice(
                    payload.get("quality"),
                    list(ALL_QUALITIES),
                    cls.quality,
                    "quality",
                ),
            ),
            output_format=output_format,
            output_compression=compression,
            background=cast(
                Background,
                _coerce_choice(
                    payload.get("background"),
                    list(ALL_BACKGROUNDS),
                    cls.background,
                    "background",
                ),
            ),
            moderation=cast(
                Moderation,
                _coerce_choice(
                    payload.get("moderation"),
                    list(ALL_MODERATION),
                    cls.moderation,
                    "moderation",
                ),
            ),
        )

    def as_mapping(self) -> dict[str, Any]:
        return {
            "target_layer_id": self.target_layer_id,
            "model": self.model,
            "size": self.size,
            "quality": self.quality,
            "output_format": self.output_format,
            "output_compression": self.output_compression,
            "background": self.background,
            "moderation": self.moderation,
        }


@dataclass(frozen=True)
class GPTImage2Params:
    """Resolved parameters for GPT Image 2 job submission."""

    prompt: str
    options: GPTImage2Options
    input_layer_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> GPTImage2Params:
        prompt = str(payload.get("prompt", "")).strip()
        options = GPTImage2Options.from_mapping(payload.get("options"))
        input_layer_ids = normalize_layer_ids(payload.get("input_layer_ids", []))
        return cls(prompt=prompt, options=options, input_layer_ids=input_layer_ids)

    def as_mapping(self) -> dict[str, Any]:
        return {
            "prompt": self.prompt,
            "options": self.options.as_mapping(),
            "input_layer_ids": list(self.input_layer_ids),
        }
