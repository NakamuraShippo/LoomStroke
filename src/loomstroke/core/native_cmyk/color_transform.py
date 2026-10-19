"""Cached authoritative LittleCMS transforms with alpha detached."""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO
from threading import get_ident

import numpy as np
from PIL import Image, ImageCms

_INTENTS = {
    "perceptual": ImageCms.Intent.PERCEPTUAL,
    "relative": ImageCms.Intent.RELATIVE_COLORIMETRIC,
    "saturation": ImageCms.Intent.SATURATION,
    "absolute": ImageCms.Intent.ABSOLUTE_COLORIMETRIC,
}


class _OutputProfile(ImageCms.ImageCmsProfile):
    """A transform-owned profile whose unchanged embedding bytes are reused."""

    def __init__(self, profile: ImageCms.ImageCmsProfile) -> None:
        super().__init__(profile.profile)
        self._serialized: bytes | None = None

    def tobytes(self) -> bytes:
        # Pillow embeds this metadata on every applyTransform, even for tiles
        # whose callers only consume the pixels. Keep the same bytes once.
        if self._serialized is None:
            self._serialized = super().tobytes()
        return self._serialized


class CmykColorTransform:
    @staticmethod
    @lru_cache(maxsize=16)
    def validate_profile(profile: bytes) -> bytes:
        if not isinstance(profile, bytes) or not 128 <= len(profile) <= 4 * 1024 * 1024:
            raise ValueError("Invalid CMYK ICC profile size")
        try:
            parsed = ImageCms.ImageCmsProfile(BytesIO(profile))
        except (OSError, ImageCms.PyCMSError) as exc:
            raise ValueError("Invalid CMYK ICC profile") from exc
        if parsed.profile.xcolor_space.strip() != "CMYK":
            raise ValueError("A CMYK ICC profile is required")
        return profile

    @staticmethod
    @lru_cache(maxsize=16)
    def _transform(profile: bytes, intent: str, bpc: bool, to_rgb: bool, thread_id: int):
        CmykColorTransform.validate_profile(profile)
        source = ImageCms.ImageCmsProfile(BytesIO(profile))
        destination = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
        if not to_rgb:
            source, destination = destination, source
        return ImageCms.buildTransform(
            source,
            _OutputProfile(destination),
            "CMYK" if to_rgb else "RGB",
            "RGB" if to_rgb else "CMYK",
            renderingIntent=_INTENTS[intent],
            flags=ImageCms.Flags.BLACKPOINTCOMPENSATION if bpc else ImageCms.Flags.NONE,
        )

    @classmethod
    def to_rgb(
        cls, inks: np.ndarray, alpha: np.ndarray, profile: bytes, *, intent: str = "relative"
    ) -> np.ndarray:
        """16-bit input is reduced only for the RGBA8 display, never for storage."""
        if inks.dtype == np.uint16:
            display = ((inks.astype(np.uint32) + 128) // 257).astype(np.uint8)
            coverage = ((alpha.astype(np.uint32) + 128) // 257).astype(np.uint8)
        elif inks.dtype == np.uint8:
            display, coverage = inks, alpha
        else:
            raise ValueError("CMYK preview requires unsigned 8/16-bit samples")
        source = Image.frombytes("CMYK", (display.shape[1], display.shape[0]), display.tobytes())
        converted = ImageCms.applyTransform(
            source, cls._transform(profile, intent, True, True, get_ident())
        )
        result = np.empty((*coverage.shape, 4), np.uint8)
        result[:, :, :3] = np.asarray(converted)
        result[:, :, 3] = coverage
        return result

    @classmethod
    def from_rgb(
        cls, rgba: np.ndarray, profile: bytes, *, source_profile: bytes | None = None
    ) -> tuple[np.ndarray, np.ndarray]:
        if rgba.dtype != np.uint8 or rgba.ndim != 3 or rgba.shape[2] != 4:
            raise ValueError("RGB conversion requires straight RGBA8")
        source = Image.fromarray(np.ascontiguousarray(rgba[:, :, :3]))
        if source_profile is None:
            transform = cls._transform(profile, "relative", True, False, get_ident())
        else:
            cls.validate_profile(profile)
            transform = ImageCms.buildTransform(
                ImageCms.ImageCmsProfile(BytesIO(source_profile)),
                _OutputProfile(ImageCms.ImageCmsProfile(BytesIO(profile))),
                "RGB",
                "CMYK",
                renderingIntent=_INTENTS["relative"],
                flags=ImageCms.Flags.BLACKPOINTCOMPENSATION,
            )
        converted = ImageCms.applyTransform(source, transform)
        return np.asarray(converted).copy(), rgba[:, :, 3].copy()
