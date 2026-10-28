"""File I/O module with ICC profile support and advanced formats.

Implements file reading and writing with ICC color profile preservation,
16-bit depth support, and multiple format handling following Master.md.
"""

from __future__ import annotations

import logging
import warnings
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from io import BytesIO
from pathlib import Path
from typing import Any, ClassVar, Protocol, cast

import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageCms
from PySide6.QtCore import QObject, Signal

logger = logging.getLogger(__name__)


def _sanitize_extra(info: Mapping[Any, Any]) -> dict[str, Any]:
    """Convert PIL info dict into a JSON-serialisable mapping."""
    sanitized: dict[str, Any] = {}
    for key, value in info.items():
        key_str = str(key)
        if key_str in {"dpi", "icc_profile"}:
            continue
        sanitized[key_str] = value
    return sanitized


class FileFormat(Enum):
    """Supported file formats."""

    PNG = "png"
    JPEG = "jpeg"
    TIFF = "tiff"
    BMP = "bmp"
    WEBP = "webp"
    EXR = "exr"  # Future OpenEXR support
    PSD = "psd"  # Future Photoshop support
    KRA = "kra"  # Future Krita support


@dataclass
class ColorProfile:
    """ICC color profile information."""

    name: str
    data: bytes | None = None
    description: str = ""
    colorspace: str = "RGB"

    @classmethod
    def srgb(cls) -> ColorProfile:
        """Get standard sRGB profile."""
        return cls(
            name="sRGB IEC61966-2.1",
            description="Standard RGB color space",
            colorspace="RGB",
        )

    @classmethod
    def from_image(cls, image: Image.Image) -> ColorProfile | None:
        """Extract color profile from PIL Image."""
        icc_profile = image.info.get("icc_profile")
        if icc_profile:
            try:
                profile_stream = (
                    BytesIO(icc_profile)
                    if isinstance(icc_profile, (bytes, bytearray))
                    else icc_profile
                )
                profile = ImageCms.ImageCmsProfile(profile_stream)
                profile_info = cast(Any, profile).profile
                name = profile_info.profile_description or "Unknown"
                return cls(
                    name=name,
                    data=icc_profile,
                    colorspace=profile_info.xcolor_space or "RGB",
                )
            except Exception as e:
                logger.warning(f"Failed to parse ICC profile: {e}")
        return None


@dataclass
class ImageMetadata:
    """Image metadata container."""

    width: int
    height: int
    channels: int
    bit_depth: int
    color_profile: ColorProfile | None = None
    dpi: tuple[float, float] = (72.0, 72.0)
    author: str | None = None
    copyright: str | None = None
    description: str | None = None
    software: str = "LoomStroke"
    extra: dict[str, Any] | None = None


class ImageImportLimitError(ValueError):
    """Raised when an image import exceeds beta safety limits."""


def _format_bytes(value: int) -> str:
    mib = value / (1024 * 1024)
    return f"{value:,} bytes ({mib:.1f} MiB)"


class FileIOProtocol(Protocol):
    """Protocol for file I/O operations."""

    def can_read(self, path: Path) -> bool:
        """Check if file can be read."""
        ...

    def can_write(self, format: FileFormat) -> bool:
        """Check if format can be written."""
        ...

    def read(self, path: Path) -> tuple[NDArray[Any], ImageMetadata]:
        """Read image file with metadata."""
        ...

    def write(
        self,
        path: Path,
        data: NDArray[Any],
        metadata: ImageMetadata,
        format: FileFormat | None = None,
    ) -> None:
        """Write image file with metadata."""
        ...


class FileIO(QObject):
    """File I/O handler with ICC and 16-bit support.

    Provides comprehensive file reading and writing with color profile
    preservation, 16-bit depth support, and metadata handling.
    """

    _MAX_IMPORTED_IMAGE_PIXELS = 67_108_864  # 8192 x 8192
    _MAX_IMPORTED_IMAGE_BYTES = 256 * 1024 * 1024
    _MAX_IMPORTED_FILE_BYTES = 512 * 1024 * 1024

    # Signals
    file_loaded = Signal(str, object, object)  # path, data, metadata
    file_saved = Signal(str)  # path
    error_occurred = Signal(str, str)  # operation, error message

    def __init__(self, parent: QObject | None = None) -> None:
        """Initialize file I/O handler.

        Args:
            parent: Optional parent QObject
        """
        super().__init__(parent)
        from loomstroke.platform.settings_manager import get_settings_manager  # noqa: PLC0415

        self._settings = get_settings_manager()
        self._supported_formats = {
            FileFormat.PNG,
            FileFormat.JPEG,
            FileFormat.TIFF,
            FileFormat.BMP,
            FileFormat.WEBP,
            FileFormat.PSD,
        }

    def can_read(self, path: Path | str) -> bool:
        """Check if file can be read.

        Args:
            path: File path to check

        Returns:
            True if file can be read
        """
        path = Path(path)
        if not path.exists() or not path.is_file():
            return False

        suffix = path.suffix.lower().lstrip(".")
        return suffix in {"png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp", "psd"}

    def can_write(self, format: FileFormat) -> bool:
        """Check if format can be written.

        Args:
            format: File format to check

        Returns:
            True if format is supported for writing
        """
        return format in self._supported_formats

    def read(self, path: Path | str) -> tuple[NDArray[Any], ImageMetadata] | None:
        """Read image file with metadata.

        Args:
            path: Path to image file

        Returns:
            Tuple of (image data, metadata) or None on error
        """
        path = Path(path)

        try:
            suffix = path.suffix.lower().lstrip(".")
            if suffix == "psd":
                return self._read_psd(path)

            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                # Open with PIL
                image = Image.open(path)
                try:
                    self._validate_import_image(path, image.width, image.height)

                    # Extract metadata
                    metadata = self._extract_metadata(image)

                    # Convert to numpy array
                    data = self._image_to_array(image, metadata.bit_depth)
                finally:
                    image.close()

            # Emit signal
            self.file_loaded.emit(str(path), data, metadata)

            logger.info(
                f"Loaded image: {path} ({metadata.width}x{metadata.height}, "
                f"{metadata.bit_depth}-bit, {metadata.channels} channels)"
            )

            return data, metadata

        except Exception as e:
            error_msg = f"Failed to read {path}: {e}"
            logger.error(error_msg)
            self.error_occurred.emit("read", error_msg)
            return None

    def write(
        self,
        path: Path | str,
        data: NDArray[Any],
        metadata: ImageMetadata | None = None,
        format: FileFormat | None = None,
    ) -> bool:
        """Write image file with metadata.

        Args:
            path: Output file path
            data: Image data as numpy array
            metadata: Optional metadata to embed
            format: Optional format override

        Returns:
            True if successful
        """
        path = Path(path)

        # Determine format
        if format is None:
            suffix = path.suffix.lower().lstrip(".")
            try:
                format = FileFormat(suffix)
            except ValueError:
                format = FileFormat.PNG

        if not self.can_write(format):
            error_msg = f"Unsupported format: {format}"
            self.error_occurred.emit("write", error_msg)
            return False

        try:
            # Convert array to image
            image = self._array_to_image(data, metadata)

            # Prepare save options
            save_kwargs = self._get_save_options(format, metadata)

            # Save image
            image.save(path, **save_kwargs)

            # Emit signal
            self.file_saved.emit(str(path))

            logger.info(f"Saved image: {path} (format: {format.value})")
            return True

        except Exception as e:
            error_msg = f"Failed to write {path}: {e}"
            logger.error(error_msg)
            self.error_occurred.emit("write", error_msg)
            return False

    def _extract_metadata(self, image: Image.Image) -> ImageMetadata:
        """Extract metadata from PIL Image.

        Args:
            image: PIL Image object

        Returns:
            Extracted metadata
        """
        # Basic properties
        width, height = image.size

        # Determine bit depth
        bit_depth = 8
        if image.mode in ("I", "F"):
            bit_depth = 32
        elif image.mode in ("I;16", "I;16L", "I;16B"):
            bit_depth = 16
        elif hasattr(image, "bits"):
            bit_depth = image.bits

        # Channel count
        channels = len(image.getbands())

        # Color profile
        color_profile = ColorProfile.from_image(image)
        if color_profile is None and image.mode in ("RGB", "RGBA"):
            color_profile = ColorProfile.srgb()

        # DPI
        dpi = image.info.get("dpi", (72.0, 72.0))
        if isinstance(dpi, (int, float)):
            dpi = (float(dpi), float(dpi))

        # Text metadata
        info = image.info
        extra = _sanitize_extra(info)

        return ImageMetadata(
            width=width,
            height=height,
            channels=channels,
            bit_depth=bit_depth,
            color_profile=color_profile,
            dpi=dpi,
            author=info.get("Author"),
            copyright=info.get("Copyright"),
            description=info.get("Description"),
            software=info.get("Software", "LoomStroke"),
            extra=extra or None,
        )

    def _image_to_array(self, image: Image.Image, target_bit_depth: int = 8) -> NDArray[Any]:
        """Convert PIL Image to numpy array.

        Args:
            image: PIL Image
            target_bit_depth: Target bit depth (8 or 16)

        Returns:
            Numpy array
        """
        # Ensure RGBA mode for consistency
        if image.mode not in ("RGBA", "RGB"):
            if image.mode in {"P", "L"}:
                image = image.convert("RGBA")
            elif image.mode in ("I", "F"):
                # High bit depth grayscale
                arr = np.array(image)
                # Normalize to 0-1 and convert to RGBA
                arr_norm = arr / arr.max() if arr.max() > 0 else arr
                rgba = np.zeros((*arr.shape, 4), dtype=np.float32)
                rgba[:, :, :3] = arr_norm[:, :, np.newaxis]
                rgba[:, :, 3] = 1.0
                dtype = np.uint16 if target_bit_depth == 16 else np.uint8
                converted = (rgba * (2**target_bit_depth - 1)).astype(dtype)
                return np.asarray(converted)

        # Convert to array
        arr = np.array(image)

        # Add alpha channel if needed
        if arr.shape[2] == 3:
            alpha = np.full((*arr.shape[:2], 1), 255, dtype=arr.dtype)
            arr = np.concatenate([arr, alpha], axis=2)

        # Convert bit depth if needed
        if target_bit_depth == 16 and arr.dtype == np.uint8:
            # 8 to 16 bit conversion
            arr = arr.astype(np.uint16) * 257  # 257 = 65535/255
        elif target_bit_depth == 8 and arr.dtype == np.uint16:
            # 16 to 8 bit conversion
            arr = (arr / 257).astype(np.uint8)

        return arr

    def _array_to_image(
        self,
        data: NDArray[Any],
        metadata: ImageMetadata | None = None,
    ) -> Image.Image:
        """Convert numpy array to PIL Image.

        Args:
            data: Image data
            metadata: Optional metadata for configuration

        Returns:
            PIL Image
        """
        # Determine mode based on data
        if data.ndim == 2:
            mode = "L"
        elif data.ndim == 3:
            if data.shape[2] == 3:
                mode = "RGB"
            elif data.shape[2] == 4:
                mode = "RGBA"
            else:
                raise ValueError(f"Unsupported channel count: {data.shape[2]}")
        else:
            raise ValueError(f"Unsupported array dimensions: {data.ndim}")

        # Handle 16-bit data
        if data.dtype == np.uint16 and mode == "L":
            mode = "I;16"
            # PIL doesn't support 16-bit RGB directly, need to use TIFF plugin

        # Create image
        image = Image.fromarray(data, mode=mode)

        # Apply color profile if available
        if metadata and metadata.color_profile and metadata.color_profile.data:
            raw_profile = metadata.color_profile.data
            profile_bytes = raw_profile if isinstance(raw_profile, bytes) else bytes(raw_profile)
            try:
                ImageCms.ImageCmsProfile(BytesIO(profile_bytes))
                # Note: Full color management would require conversion
                image.info["icc_profile"] = profile_bytes
            except Exception as e:
                logger.warning(f"Failed to apply color profile: {e}")

        return image

    def _get_save_options(
        self,
        format: FileFormat,
        metadata: ImageMetadata | None = None,
    ) -> dict[str, Any]:
        """Get save options for format.

        Args:
            format: Target format
            metadata: Optional metadata

        Returns:
            Save options dictionary
        """
        options: dict[str, Any] = {
            "format": format.value.upper(),
        }

        if metadata:
            # DPI
            options["dpi"] = metadata.dpi

            # ICC Profile
            if metadata.color_profile and metadata.color_profile.data:
                options["icc_profile"] = metadata.color_profile.data

            # Metadata
            if metadata.description:
                options["description"] = metadata.description
            if metadata.author:
                options["author"] = metadata.author
            if metadata.copyright:
                options["copyright"] = metadata.copyright
            options["software"] = metadata.software

        # Format-specific options
        if format == FileFormat.PNG:
            options["compress_level"] = self._settings.get("io.png.compress_level", 6)
            options["optimize"] = self._settings.get("io.png.optimize", True)

        elif format == FileFormat.JPEG:
            options["quality"] = self._settings.get("io.jpeg.quality", 95)
            options["optimize"] = self._settings.get("io.jpeg.optimize", True)
            options["progressive"] = self._settings.get("io.jpeg.progressive", False)

        elif format == FileFormat.TIFF:
            # TIFF supports 16-bit natively
            compression = self._settings.get("io.tiff.compression", "tiff_lzw")
            options["compression"] = compression

            # Enable 16-bit for TIFF if data is 16-bit
            if metadata and metadata.bit_depth == 16:
                options["save_all"] = True
                options["append_images"] = []

        elif format == FileFormat.WEBP:
            options["quality"] = self._settings.get("io.webp.quality", 90)
            options["lossless"] = self._settings.get("io.webp.lossless", False)
            options["method"] = self._settings.get("io.webp.method", 4)

        options.update(self._encoder_option_overrides(format, metadata))
        return options

    #: Per-export encoder overrides the Export dialog may pass through
    #: ``ImageMetadata.extra["encoder_options"]`` (CT-09 §6).  Anything not
    #: listed here never reaches ``Image.save``.
    _ENCODER_OPTION_KEYS: ClassVar[dict[FileFormat, frozenset[str]]] = {
        FileFormat.PNG: frozenset({"compress_level", "optimize"}),
        FileFormat.JPEG: frozenset({"quality", "optimize", "progressive"}),
        FileFormat.TIFF: frozenset({"compression"}),
        FileFormat.WEBP: frozenset({"quality", "lossless", "method"}),
    }

    @classmethod
    def _encoder_option_overrides(
        cls,
        format: FileFormat,
        metadata: ImageMetadata | None,
    ) -> dict[str, Any]:
        extra = getattr(metadata, "extra", None)
        raw = extra.get("encoder_options") if isinstance(extra, Mapping) else None
        if not isinstance(raw, Mapping):
            return {}
        allowed = cls._ENCODER_OPTION_KEYS.get(format, frozenset())
        return {str(key): value for key, value in raw.items() if key in allowed}

    # ------------------------------------------------------------------
    # PSD reading
    # ------------------------------------------------------------------
    def _validate_import_image(self, path: Path, width: int, height: int) -> None:
        file_size = path.stat().st_size
        if file_size > self._MAX_IMPORTED_FILE_BYTES:
            raise ImageImportLimitError(
                "Image import rejected: file size "
                f"{_format_bytes(file_size)} exceeds limit "
                f"{_format_bytes(self._MAX_IMPORTED_FILE_BYTES)}"
            )

        if width <= 0 or height <= 0:
            raise ValueError(f"image has invalid dimensions: {width}x{height}")

        pixels = width * height
        if pixels > self._MAX_IMPORTED_IMAGE_PIXELS:
            raise ImageImportLimitError(
                "Image import rejected: dimensions "
                f"{width}x{height} ({pixels:,} pixels) exceed pixel limit "
                f"{self._MAX_IMPORTED_IMAGE_PIXELS:,} pixels"
            )

        decoded_bytes = pixels * 4
        if decoded_bytes > self._MAX_IMPORTED_IMAGE_BYTES:
            raise ImageImportLimitError(
                "Image import rejected: decoded RGBA size "
                f"{_format_bytes(decoded_bytes)} exceeds limit "
                f"{_format_bytes(self._MAX_IMPORTED_IMAGE_BYTES)}"
            )

    def _read_psd(self, path: Path) -> tuple[NDArray[Any], ImageMetadata] | None:
        """Read a PSD file using psd-tools, returning the composite image."""
        try:
            from psd_tools import PSDImage  # noqa: PLC0415
        except ImportError:
            logger.error("psd-tools is required for PSD support: pip install psd-tools")
            self.error_occurred.emit("read", "psd-tools not installed")
            return None

        try:
            psd = PSDImage.open(path)
        except Exception as e:
            error_msg = f"Failed to read PSD {path}: {e}"
            logger.error(error_msg)
            self.error_occurred.emit("read", error_msg)
            return None
        return self._read_opened_psd(path, psd)

    def _read_opened_psd(self, path: Path, psd: Any) -> tuple[NDArray[Any], ImageMetadata] | None:
        """Read a composite from an already parsed PSD without reopening its source."""
        try:
            self._validate_import_image(path, int(psd.width), int(psd.height))
            composite = psd.composite()
            if composite is None:
                logger.error("PSD file has no composite image: %s", path)
                return None

            # Convert to RGBA numpy array
            if composite.mode != "RGBA":
                composite = composite.convert("RGBA")
            data = np.array(composite, dtype=np.uint8)

            icc_bytes = getattr(psd, "icc_profile", None) or b""
            color_profile = ColorProfile(name="sRGB IEC61966-2.1")
            if icc_bytes:
                try:
                    profile = ImageCms.ImageCmsProfile(BytesIO(icc_bytes))
                    color_profile = ColorProfile(
                        # PIL.ImageCms lacks type stubs for getProfileName.
                        name=ImageCms.getProfileName(profile).strip(),  # type: ignore[no-untyped-call]  # PIL.ImageCms without stubs
                        data=icc_bytes,
                    )
                except Exception:
                    logger.debug("psd icc profile parse failed", exc_info=True)

            metadata = ImageMetadata(
                width=psd.width,
                height=psd.height,
                channels=4,
                bit_depth=8,
                color_profile=color_profile,
            )

            self.file_loaded.emit(str(path), data, metadata)
            logger.info(
                "Loaded PSD: %s (%dx%d, %d layers)",
                path,
                psd.width,
                psd.height,
                len(psd),
            )
            return data, metadata

        except Exception as e:
            error_msg = f"Failed to read PSD {path}: {e}"
            logger.error(error_msg)
            self.error_occurred.emit("read", error_msg)
            return None

    def read_psd_layers(
        self, path: Path
    ) -> list[tuple[str, NDArray[Any], int, int, str, float, bool]] | None:
        """Read PSD file preserving layer structure.

        Returns a list of tuples:
            (name, rgba_array, offset_x, offset_y, blend_mode, opacity, visible)
        or None on failure.  Layers are ordered bottom-to-top.
        """
        try:
            from psd_tools import PSDImage  # noqa: PLC0415
        except ImportError:
            logger.error("psd-tools is required for PSD support: pip install psd-tools")
            return None

        _BLEND_MAP: dict[str, str] = {
            "normal": "normal",
            "multiply": "multiply",
            "screen": "screen",
            "overlay": "overlay",
            "darken": "darken",
            "lighten": "lighten",
            "color_dodge": "color_dodge",
            "color_burn": "color_burn",
            "hard_light": "hard_light",
            "soft_light": "soft_light",
            "difference": "difference",
            "exclusion": "exclusion",
        }

        try:
            psd = PSDImage.open(path)
            self._validate_import_image(path, int(psd.width), int(psd.height))
            layers: list[tuple[str, NDArray[Any], int, int, str, float, bool]] = []

            def _collect(layer_or_group: Any, depth: int = 0) -> None:
                # Skip group containers (flatten into individual layers)
                if hasattr(layer_or_group, "is_group") and layer_or_group.is_group():
                    for child in layer_or_group:
                        _collect(child, depth + 1)
                    return

                try:
                    img = layer_or_group.composite()
                except Exception:
                    img = None
                if img is None:
                    return
                if img.mode != "RGBA":
                    img = img.convert("RGBA")
                data = np.array(img, dtype=np.uint8)

                name = getattr(layer_or_group, "name", None) or f"Layer {len(layers) + 1}"
                ox = getattr(layer_or_group, "left", 0) or 0
                oy = getattr(layer_or_group, "top", 0) or 0
                raw_blend = str(getattr(layer_or_group, "blend_mode", "normal")).lower()
                # psd-tools uses BlendMode enum — extract name
                if "." in raw_blend:
                    raw_blend = raw_blend.rsplit(".", 1)[-1]
                blend = _BLEND_MAP.get(raw_blend, "normal")
                opacity = (getattr(layer_or_group, "opacity", 255) or 255) / 255.0
                visible = bool(getattr(layer_or_group, "visible", True))

                layers.append((name, data, ox, oy, blend, opacity, visible))

            for top_layer in psd:
                _collect(top_layer)

            if not layers:
                logger.warning("PSD has no readable layers, falling back to composite")
                return None

            logger.info("Read PSD layers: %s (%d layers)", path, len(layers))
            return layers

        except Exception as e:
            logger.error("Failed to read PSD layers %s: %s", path, e)
            return None

    # ------------------------------------------------------------------
    # PSD writing
    # ------------------------------------------------------------------

    # LoomStroke BlendMode.value → psd_tools BlendMode name mapping.
    # Keys that match 1:1 are listed explicitly for clarity.
    _PSD_BLEND_MAP: ClassVar[dict[str, str]] = {
        "pass_through": "PASS_THROUGH",
        "normal": "NORMAL",
        "dissolve": "DISSOLVE",
        "darken": "DARKEN",
        "multiply": "MULTIPLY",
        "color_burn": "COLOR_BURN",
        "linear_burn": "LINEAR_BURN",
        "darker_color": "DARKER_COLOR",
        "lighten": "LIGHTEN",
        "screen": "SCREEN",
        "color_dodge": "COLOR_DODGE",
        "linear_dodge": "LINEAR_DODGE",
        "lighter_color": "LIGHTER_COLOR",
        "overlay": "OVERLAY",
        "soft_light": "SOFT_LIGHT",
        "hard_light": "HARD_LIGHT",
        "vivid_light": "VIVID_LIGHT",
        "linear_light": "LINEAR_LIGHT",
        "pin_light": "PIN_LIGHT",
        "hard_mix": "HARD_MIX",
        "difference": "DIFFERENCE",
        "exclusion": "EXCLUSION",
        "subtract": "SUBTRACT",
        "divide": "DIVIDE",
        "hue": "HUE",
        "saturation": "SATURATION",
        "color": "COLOR",
        "luminosity": "LUMINOSITY",
    }

    def write_psd(
        self,
        path: Path,
        canvas_width: int,
        canvas_height: int,
        layers: list[tuple[str, NDArray[Any], int, int, str, float, bool]],
        *,
        groups: list[tuple[str, list[int], str, float, bool]] | None = None,
    ) -> bool:
        """Write a PSD file with layer structure.

        Args:
            path: Destination file path.
            canvas_width: Canvas width in pixels.
            canvas_height: Canvas height in pixels.
            layers: List of (name, rgba_array, offset_x, offset_y,
                     blend_mode_str, opacity_0_1, visible).
                     Ordered bottom-to-top (same as ``read_psd_layers``).
            groups: Optional group definitions. Each entry is
                    (group_name, child_indices, blend_mode, opacity, visible).
                    ``child_indices`` refers to indices in *layers*.

        Returns:
            True on success.
        """
        try:
            from psd_tools import PSDImage  # noqa: PLC0415
            from psd_tools.constants import BlendMode as PsdBlend  # noqa: PLC0415
            from psd_tools.constants import Compression  # noqa: PLC0415
        except ImportError:
            logger.error("psd-tools is required for PSD export: pip install psd-tools")
            self.error_occurred.emit("write", "psd-tools not installed")
            return False

        def _to_psd_blend(mode_str: str) -> PsdBlend:
            attr = self._PSD_BLEND_MAP.get(mode_str, "NORMAL")
            return getattr(PsdBlend, attr, PsdBlend.NORMAL)

        try:
            psd = PSDImage.new("RGB", (canvas_width, canvas_height))

            # Build pixel layers (bottom-to-top order preserved)
            psd_layers: list[Any] = []
            for name, rgba, ox, oy, blend_str, opacity, visible in layers:
                pil_img = Image.fromarray(rgba, "RGBA")
                pixel = psd.create_pixel_layer(
                    image=pil_img,
                    name=name,
                    top=oy,
                    left=ox,
                    compression=Compression.RLE,
                    opacity=max(0, min(255, int(opacity * 255 + 0.5))),
                    blend_mode=_to_psd_blend(blend_str),
                )
                pixel.visible = visible
                psd_layers.append(pixel)

            # Build groups if provided
            if groups:
                for grp_name, child_indices, blend_str, opacity, visible in groups:
                    children = [psd_layers[i] for i in child_indices if 0 <= i < len(psd_layers)]
                    if not children:
                        continue
                    group = psd.create_group(
                        layer_list=children,
                        name=grp_name,
                        opacity=max(0, min(255, int(opacity * 255 + 0.5))),
                        blend_mode=_to_psd_blend(blend_str),
                    )
                    group.visible = visible

            psd.save(str(path))

            logger.info(
                "Exported PSD: %s (%dx%d, %d layers)",
                path,
                canvas_width,
                canvas_height,
                len(layers),
            )
            return True

        except Exception as e:
            error_msg = f"Failed to write PSD {path}: {e}"
            logger.error(error_msg)
            self.error_occurred.emit("write", error_msg)
            return False
