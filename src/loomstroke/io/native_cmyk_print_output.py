"""Flatten native ink alpha onto paper and preserve plate values in print output."""

from io import BytesIO

import numpy as np
from PIL import Image, ImageCms

from .color_profile_registry import _INTENTS
from .native_cmyk_tiff_writer import NativeCmykTiffWriter


class NativeCmykPrintOutput:
    @staticmethod
    def write(pixels, destination, *, profile, dpi, intent, black_point_compensation):
        maximum = np.iinfo(pixels.inks.dtype).max
        # White paper contains no ink. This is ink alpha flattening, not RGB separation.
        inks = np.empty_like(pixels.inks)
        for start in range(0, inks.shape[0], 64):
            rows = slice(start, start + 64)
            weighted = pixels.inks[rows].astype(np.uint64) * pixels.alpha[rows, :, None]
            inks[rows] = ((weighted + maximum // 2) // maximum).astype(inks.dtype)
        if pixels.profile != profile:
            if pixels.bits != 8:
                raise ValueError("16-bit CMYK output requires the document ICC profile")
            transform = ImageCms.buildTransform(
                ImageCms.ImageCmsProfile(BytesIO(pixels.profile)),
                ImageCms.ImageCmsProfile(BytesIO(profile)),
                "CMYK",
                "CMYK",
                renderingIntent=_INTENTS[intent],
                flags=(
                    ImageCms.Flags.BLACKPOINTCOMPENSATION
                    if black_point_compensation
                    else ImageCms.Flags.NONE
                ),
            )
            source = Image.frombytes("CMYK", (inks.shape[1], inks.shape[0]), inks.tobytes())
            inks = np.asarray(ImageCms.applyTransform(source, transform))
        return NativeCmykTiffWriter.write(destination, inks, profile=profile, dpi=dpi)
