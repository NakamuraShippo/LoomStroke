"""ICC profile registry + the authoritative CPU color transform
(CT-11 P1, spec 11 §6/§8).

Two rules carry the design:

- **Profiles are validated bytes, addressed by fingerprint.**  A profile
  enters the registry only after LittleCMS (Pillow ``ImageCms``) parses
  it; the id is a content hash, so equal bytes are one profile and no
  absolute path is ever stored or surfaced (spec 11 §8.3/§11).
- **LittleCMS is the output authority** (spec 11 §8.1).  ``transform_rgba``
  is the single CPU transform every later consumer (soft proof LUTs,
  CMYK output, PDF) is measured against.  RGB→CMYK is never a formula;
  it goes through the destination profile — that arrives with P3, built
  on this same entry point.

Alpha is detached before the transform and reattached unchanged
(straight alpha, spec 11 §8.1).  No wgpu, no Qt.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from io import BytesIO
from typing import Any, Literal

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageCms

logger = logging.getLogger(__name__)

__all__ = [
    "ColorProfileInfo",
    "ColorProfileRegistry",
    "RenderingIntent",
    "transform_rgba",
]

RenderingIntent = Literal["perceptual", "relative", "saturation", "absolute"]

_INTENTS: dict[str, int] = {
    "perceptual": ImageCms.Intent.PERCEPTUAL,
    "relative": ImageCms.Intent.RELATIVE_COLORIMETRIC,
    "saturation": ImageCms.Intent.SATURATION,
    "absolute": ImageCms.Intent.ABSOLUTE_COLORIMETRIC,
}


@dataclass(frozen=True, slots=True)
class ColorProfileInfo:
    """What the UI may show about a registered profile — never a path."""

    profile_id: str
    name: str
    colorspace: str
    byte_count: int


class ColorProfileRegistry:
    """Validated ICC bytes by content fingerprint (runtime only)."""

    def __init__(self) -> None:
        self._profiles: dict[str, tuple[ColorProfileInfo, bytes]] = {}
        self._srgb_id: str | None = None

    def register(self, data: bytes | bytearray) -> ColorProfileInfo:
        """Validate and store profile bytes; idempotent per content.

        Raises ``ValueError`` for anything LittleCMS cannot parse — an
        invalid profile never enters the registry, so later transform
        failures cannot come from unvalidated bytes (spec 11 §14).
        """
        payload = bytes(data)
        if not payload:
            raise ValueError("empty ICC profile")
        profile_id = hashlib.sha256(payload).hexdigest()[:16]
        cached = self._profiles.get(profile_id)
        if cached is not None:
            return cached[0]
        try:
            profile = ImageCms.ImageCmsProfile(BytesIO(payload))
            details: Any = profile.profile
            name = str(details.profile_description or "Unknown")
            colorspace = str(details.xcolor_space or "RGB").strip()
        except Exception as error:
            raise ValueError(f"invalid ICC profile: {error}") from error
        info = ColorProfileInfo(
            profile_id=profile_id,
            name=name,
            colorspace=colorspace,
            byte_count=len(payload),
        )
        self._profiles[profile_id] = (info, payload)
        return info

    def srgb(self) -> ColorProfileInfo:
        """The built-in sRGB profile (generated, license-free)."""
        if self._srgb_id is None:
            profile = ImageCms.createProfile("sRGB")
            payload = ImageCms.ImageCmsProfile(profile).tobytes()
            self._srgb_id = self.register(payload).profile_id
        return self._profiles[self._srgb_id][0]

    def info(self, profile_id: str) -> ColorProfileInfo | None:
        entry = self._profiles.get(profile_id)
        return entry[0] if entry is not None else None

    def profile_bytes(self, profile_id: str) -> bytes | None:
        entry = self._profiles.get(profile_id)
        return entry[1] if entry is not None else None

    def list_profiles(self) -> tuple[ColorProfileInfo, ...]:
        return tuple(info for info, _payload in self._profiles.values())


def transform_rgba(
    rgba: NDArray[np.uint8],
    registry: ColorProfileRegistry,
    *,
    source_profile_id: str,
    destination_profile_id: str,
    intent: RenderingIntent = "perceptual",
    black_point_compensation: bool = True,
) -> NDArray[np.uint8]:
    """The authoritative RGB->RGB CPU transform (LittleCMS).

    Alpha passes through untouched.  Unknown profile ids raise — silent
    identity would report a proof/export as color-managed when it was
    not (spec 11 §14).
    """
    if rgba.ndim != 3 or rgba.shape[2] != 4 or rgba.dtype != np.uint8:
        raise ValueError("transform_rgba expects straight-alpha RGBA8 (H x W x 4)")
    source_bytes = registry.profile_bytes(source_profile_id)
    destination_bytes = registry.profile_bytes(destination_profile_id)
    if source_bytes is None:
        raise ValueError(f"unknown source profile: {source_profile_id}")
    if destination_bytes is None:
        raise ValueError(f"unknown destination profile: {destination_profile_id}")
    if source_profile_id == destination_profile_id:
        return rgba.copy()

    flags = ImageCms.Flags.NONE
    if black_point_compensation:
        flags = ImageCms.Flags.BLACKPOINTCOMPENSATION
    transform = ImageCms.buildTransform(
        ImageCms.ImageCmsProfile(BytesIO(source_bytes)),
        ImageCms.ImageCmsProfile(BytesIO(destination_bytes)),
        "RGB",
        "RGB",
        renderingIntent=_INTENTS[intent],
        flags=flags,
    )
    rgb_image = Image.fromarray(np.ascontiguousarray(rgba[:, :, :3]), mode="RGB")
    converted = ImageCms.applyTransform(rgb_image, transform)
    result = rgba.copy()
    result[:, :, :3] = np.asarray(converted, dtype=np.uint8)
    return result
