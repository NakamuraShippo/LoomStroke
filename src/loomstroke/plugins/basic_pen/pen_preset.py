"""Validated, portable settings for the LoomStroke pen format."""

from __future__ import annotations

import base64
import binascii
import math
import struct
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
from types import MappingProxyType
from typing import Any

from PIL import Image, UnidentifiedImageError

PEN_EXTENSION = ".lspn"
PEN_FORMAT = "loomstroke.pen"
PEN_VERSION = 1
MAX_PRESET_BYTES = 3 * 1024 * 1024
MAX_TIP_BYTES = 2 * 1024 * 1024
MAX_TIP_DIMENSION = 2048
MAX_TIP_PIXELS = 4 * 1024 * 1024
MAX_NAME_LENGTH = 120

_NUMERIC_SETTINGS: dict[str, tuple[float, float, float]] = {
    "size": (0.1, 1000.0, 5.0),
    "opacity": (0.0, 1.0, 1.0),
    "hardness": (0.0, 1.0, 1.0),
    "spacing": (0.01, 3.0, 0.1),
    "flow": (0.0, 1.0, 1.0),
    "tone_range": (0.0, 1.0, 1.0),
    "tone_gamma": (0.1, 2.0, 0.6),
    "smoothing": (0.0, 1.0, 0.0),
    "recontour_smoothness": (0.0, 100.0, 30.0),
    "taper_start_length": (0.0, 2000.0, 0.0),
    "taper_end_length": (0.0, 2000.0, 0.0),
}
_BOOLEAN_SETTINGS = {"pressure_sensitive": True, "tilt_angle": False, "recontour": False}
PEN_SETTING_KEYS = frozenset(_NUMERIC_SETTINGS) | frozenset(_BOOLEAN_SETTINGS)


def validate_preset_id(value: Any) -> str:
    """Restrict library filenames to canonical UUIDs."""
    if not isinstance(value, str):
        raise ValueError("Pen preset id must be a UUID string")
    try:
        canonical = str(uuid.UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError("Pen preset id must be a UUID string") from exc
    if value != canonical:
        raise ValueError("Pen preset id must be a canonical UUID")
    return value


def _validate_settings(settings: Mapping[str, Any]) -> dict[str, float | bool]:
    if not isinstance(settings, Mapping) or set(settings) != PEN_SETTING_KEYS:
        raise ValueError("Pen settings must contain exactly the supported setting keys")
    result: dict[str, float | bool] = {}
    for key, (minimum, maximum, _default) in _NUMERIC_SETTINGS.items():
        value = settings[key]
        if type(value) not in (int, float):
            raise ValueError(f"Pen setting {key} must be a number")
        try:
            number = float(value)
        except OverflowError as exc:
            raise ValueError(f"Pen setting {key} is out of range") from exc
        if not math.isfinite(number) or not minimum <= number <= maximum:
            raise ValueError(f"Pen setting {key} is out of range")
        result[key] = number
    for key in _BOOLEAN_SETTINGS:
        if type(settings[key]) is not bool:
            raise ValueError(f"Pen setting {key} must be a boolean")
        result[key] = settings[key]
    return result


def validate_tip_png(data: bytes) -> None:
    """Inspect the header before decoding and reject oversized/non-PNG tips."""
    if not isinstance(data, bytes) or not 0 < len(data) <= MAX_TIP_BYTES:
        raise ValueError("Pen tip exceeds the PNG byte limit")
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Pen tip must be a PNG image")
    if len(data) < 33 or data[12:16] != b"IHDR":
        raise ValueError("Pen tip contains an invalid PNG header")
    width, height = struct.unpack(">II", data[16:24])
    if (
        not 0 < width <= MAX_TIP_DIMENSION
        or not 0 < height <= MAX_TIP_DIMENSION
        or width * height > MAX_TIP_PIXELS
    ):
        raise ValueError("Pen tip exceeds the PNG dimension limit")
    try:
        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            if (
                image.format != "PNG"
                or not 0 < width <= MAX_TIP_DIMENSION
                or not 0 < height <= MAX_TIP_DIMENSION
                or width * height > MAX_TIP_PIXELS
                or getattr(image, "n_frames", 1) != 1
            ):
                raise ValueError("Pen tip exceeds the PNG dimension limit")
            image.verify()
        # Verification checks PNG chunks; loading also checks the compressed raster.
        with Image.open(BytesIO(data)) as image:
            image.load()
    except (OSError, SyntaxError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise ValueError("Pen tip contains an invalid PNG image") from exc


@dataclass(frozen=True)
class PenPreset:
    """One named pen, with its optional tip embedded instead of a local path."""

    id: str
    name: str
    settings: Mapping[str, float | bool]
    tip_png: bytes | None = None
    hardness_mode: str = "radial"

    def __post_init__(self) -> None:
        validate_preset_id(self.id)
        if (
            not isinstance(self.name, str)
            or not self.name.strip()
            or len(self.name) > MAX_NAME_LENGTH
            or any(ord(character) < 32 for character in self.name)
        ):
            raise ValueError("Pen preset name must be non-empty and at most 120 characters")
        object.__setattr__(self, "settings", MappingProxyType(_validate_settings(self.settings)))
        if not isinstance(self.hardness_mode, str) or self.hardness_mode not in {
            "radial",
            "texture",
        }:
            raise ValueError("Unknown pen hardness mode")
        if self.tip_png is not None:
            validate_tip_png(self.tip_png)
        elif self.hardness_mode != "radial":
            raise ValueError("Texture hardness requires an embedded pen tip")

    @classmethod
    def capture(
        cls,
        name: str,
        settings: Mapping[str, Any],
        *,
        tip_png: bytes | None = None,
        hardness_mode: str = "radial",
        preset_id: str | None = None,
    ) -> PenPreset:
        """Capture supported tool settings, retaining the current foreground colour."""
        if not isinstance(settings, Mapping):
            raise ValueError("Pen settings must be a mapping")
        values = {key: settings.get(key, limits[2]) for key, limits in _NUMERIC_SETTINGS.items()}
        values.update({key: settings.get(key, value) for key, value in _BOOLEAN_SETTINGS.items()})
        return cls(preset_id or str(uuid.uuid4()), name, values, tip_png, hardness_mode)

    def to_dict(self) -> dict[str, Any]:
        tip = None
        if self.tip_png is not None:
            tip = {"encoding": "png-base64", "data": base64.b64encode(self.tip_png).decode("ascii")}
        return {
            "format": PEN_FORMAT,
            "version": PEN_VERSION,
            "id": self.id,
            "name": self.name,
            "settings": dict(self.settings),
            "hardness_mode": self.hardness_mode,
            "tip": tip,
        }

    @classmethod
    def from_dict(cls, document: Any) -> PenPreset:
        expected = {"format", "version", "id", "name", "settings", "hardness_mode", "tip"}
        if not isinstance(document, dict) or set(document) != expected:
            raise ValueError("Invalid LoomStroke pen document")
        if document["format"] != PEN_FORMAT:
            raise ValueError("Unknown LoomStroke pen format")
        if type(document["version"]) is not int or document["version"] != PEN_VERSION:
            raise ValueError("Unsupported LoomStroke pen version")
        tip_png = None
        tip = document["tip"]
        if tip is not None:
            if (
                not isinstance(tip, dict)
                or set(tip) != {"encoding", "data"}
                or tip["encoding"] != "png-base64"
                or not isinstance(tip["data"], str)
                or len(tip["data"]) > ((MAX_TIP_BYTES + 2) // 3) * 4
            ):
                raise ValueError("Invalid embedded pen tip")
            try:
                tip_png = base64.b64decode(tip["data"], validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError("Invalid embedded pen tip encoding") from exc
        return cls(
            document["id"],
            document["name"],
            document["settings"],
            tip_png,
            document["hardness_mode"],
        )
