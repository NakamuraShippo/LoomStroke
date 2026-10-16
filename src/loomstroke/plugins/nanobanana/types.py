"""Typed structures for the NanoBanana plugin."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, cast

try:  # pragma: no cover - fallback for type checkers
    from typing import Literal
except ImportError:  # pragma: no cover
    from typing import Literal  # type: ignore

Aspect = Literal[
    "1:1",
    "1:4",
    "1:8",
    "2:3",
    "3:2",
    "3:4",
    "4:1",
    "4:3",
    "4:5",
    "5:4",
    "8:1",
    "9:16",
    "16:9",
    "21:9",
]
Modality = Literal["Image", "Text+Image"]
ImageSize = Literal["512", "1K", "2K", "4K"]
ThinkingLevel = Literal["minimal", "High"]
Model = Literal[
    "gemini-nano-banana-2.1",
    "gemini-3.1-flash-image",
    "gemini-3-pro-image",
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-image-preview",
    "gemini-3-pro-image-preview",
]

ASPECT_TO_RESOLUTION: dict[Aspect, tuple[int, int]] = {
    "1:1": (1024, 1024),
    "1:4": (512, 2048),
    "1:8": (384, 3072),
    "2:3": (832, 1248),
    "3:2": (1248, 832),
    "3:4": (864, 1184),
    "4:1": (2048, 512),
    "4:3": (1184, 864),
    "4:5": (896, 1152),
    "5:4": (1152, 896),
    "8:1": (3072, 384),
    "9:16": (768, 1344),
    "16:9": (1344, 768),
    "21:9": (1536, 672),
}

DEFAULT_MODEL: Model = "gemini-2.5-flash-image"
ALL_MODELS: list[Model] = [
    "gemini-nano-banana-2.1",
    "gemini-3.1-flash-image",
    "gemini-3-pro-image",
    "gemini-2.5-flash-image",
    "gemini-3.1-flash-image-preview",
    "gemini-3-pro-image-preview",
]
MODEL_DISPLAY_NAMES: dict[Model, str] = {
    "gemini-nano-banana-2.1": "Nano Banana 2.1",
    "gemini-3.1-flash-image": "Nano Banana 2 (Gemini 3.1 Flash)",
    "gemini-3-pro-image": "Nano Banana Pro (Gemini 3 Pro)",
    "gemini-2.5-flash-image": "Gemini 2.5 Flash",
    "gemini-3.1-flash-image-preview": "Gemini 3.1 Flash",
    "gemini-3-pro-image-preview": "Gemini 3 Pro",
}

MAX_INPUT_LAYERS = 14


def _coerce_modality(value: Any) -> Modality:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized in ("Image", "Text+Image"):
            return cast(Modality, normalized)
    raise ValueError(f"Invalid NanoBanana modality: {value!r}")


def _coerce_aspect(value: Any) -> Aspect:
    if isinstance(value, str):
        normalized = value.strip()
        if normalized in ASPECT_TO_RESOLUTION:
            return cast(Aspect, normalized)
    raise ValueError(f"Invalid NanoBanana aspect ratio: {value!r}")


def _coerce_target(value: Any | None) -> str | None:
    if value in (None, "", b""):
        return None
    return str(value)


def _coerce_image_size(value: Any | None) -> ImageSize | None:
    if value in (None, "", b""):
        return None
    if isinstance(value, str):
        normalized = value.strip()
        if normalized in ("512", "1K", "2K", "4K"):
            return cast(ImageSize, normalized)
    raise ValueError(f"Invalid NanoBanana image size: {value!r}")


def _coerce_thinking_level(value: Any | None) -> ThinkingLevel:
    if value in (None, "", b""):
        return "minimal"
    if isinstance(value, str):
        normalized = value.strip()
        if normalized in ("minimal", "High"):
            return cast(ThinkingLevel, normalized)
    raise ValueError(f"Invalid NanoBanana thinking level: {value!r}")


def _coerce_model(value: Any | None) -> Model:
    if value in (None, "", b""):
        return DEFAULT_MODEL
    if isinstance(value, str):
        normalized = value.strip()
        if normalized in ALL_MODELS:
            return cast(Model, normalized)
    raise ValueError(f"Invalid NanoBanana model: {value!r}")


@dataclass(frozen=True)
class NanoBananaOptions:
    """User-configurable options for NanoBanana jobs."""

    modality: Modality = cast(Modality, "Text+Image")  # noqa: RUF009
    aspect_ratio: Aspect = cast(Aspect, "1:1")  # noqa: RUF009
    target_layer_id: str | None = None
    model: Model = DEFAULT_MODEL  # noqa: RUF009
    image_size: ImageSize | None = None
    thinking_level: ThinkingLevel = cast(ThinkingLevel, "minimal")  # noqa: RUF009
    include_thoughts: bool = False
    google_search: bool = False

    @classmethod
    def from_mapping(
        cls, payload: Mapping[str, Any] | NanoBananaOptions | None
    ) -> NanoBananaOptions:
        if payload is None:
            return cls()
        if isinstance(payload, cls):
            return payload
        modality = _coerce_modality(payload.get("modality", cls.modality))
        aspect_ratio = _coerce_aspect(payload.get("aspect_ratio", cls.aspect_ratio))
        target = _coerce_target(payload.get("target_layer_id"))
        model = _coerce_model(payload.get("model"))
        image_size = _coerce_image_size(payload.get("image_size"))
        thinking_level = _coerce_thinking_level(payload.get("thinking_level"))
        include_thoughts = bool(payload.get("include_thoughts", False))
        google_search = bool(payload.get("google_search", False))
        return cls(
            modality=modality,
            aspect_ratio=aspect_ratio,
            target_layer_id=target,
            model=model,
            image_size=image_size,
            thinking_level=thinking_level,
            include_thoughts=include_thoughts,
            google_search=google_search,
        )


@dataclass(frozen=True)
class NanoBananaParams:
    """Resolved parameters for job submission."""

    prompt: str
    input_layer_ids: list[str]
    options: NanoBananaOptions

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any]) -> NanoBananaParams:
        prompt = str(payload.get("prompt", "")).strip()
        input_ids_raw = payload.get("input_layer_ids", [])
        if not isinstance(input_ids_raw, Sequence):
            raise ValueError("input_layer_ids must be a sequence")
        input_ids: list[str] = []
        for value in input_ids_raw:
            if value is None:
                continue
            text = str(value).strip()
            if text and text not in input_ids:
                input_ids.append(text)
            if len(input_ids) >= MAX_INPUT_LAYERS:
                break
        options = NanoBananaOptions.from_mapping(payload.get("options"))
        return cls(prompt=prompt, input_layer_ids=input_ids, options=options)

    def as_mapping(self) -> dict[str, Any]:
        """Return a JSON-serialisable representation."""
        return {
            "prompt": self.prompt,
            "input_layer_ids": list(self.input_layer_ids),
            "options": {
                "modality": self.options.modality,
                "aspect_ratio": self.options.aspect_ratio,
                "target_layer_id": self.options.target_layer_id,
                "model": self.options.model,
                "image_size": self.options.image_size,
                "thinking_level": self.options.thinking_level,
                "include_thoughts": self.options.include_thoughts,
                "google_search": self.options.google_search,
            },
        }
