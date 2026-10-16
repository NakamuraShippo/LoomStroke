"""JSON contracts for GUI-equivalent paint and publication exports."""

from __future__ import annotations

from .paint_extension_arguments import ID, NAME, REVISION, object_schema

PATH = {"type": "string", "minLength": 1, "maxLength": 4096}
FILE = {"path": PATH, "overwrite": {"type": "boolean"}, **REVISION}
PAGE_IDS = {"type": "array", "items": ID, "minItems": 1, "maxItems": 5000, "uniqueItems": True}
RGBA = {
    "type": "array",
    "items": {"type": "integer", "minimum": 0, "maximum": 255},
    "minItems": 4,
    "maxItems": 4,
}
OPTIONS = {
    "png": object_schema(
        {
            "compress_level": {"type": "integer", "minimum": 0, "maximum": 9},
            "optimize": {"type": "boolean"},
        }
    ),
    "jpeg": object_schema(
        {
            "quality": {"type": "integer", "minimum": 1, "maximum": 100},
            "optimize": {"type": "boolean"},
            "progressive": {"type": "boolean"},
        }
    ),
    "tiff": object_schema(
        {"compression": {"enum": ["raw", "tiff_lzw", "tiff_adobe_deflate", "packbits", "jpeg"]}}
    ),
    "webp": object_schema(
        {
            "quality": {"type": "integer", "minimum": 1, "maximum": 100},
            "lossless": {"type": "boolean"},
            "method": {"type": "integer", "minimum": 0, "maximum": 6},
        }
    ),
    "bmp": object_schema({}),
}
IMAGE_SCHEMA = {
    "oneOf": [
        object_schema(
            {
                **FILE,
                "format": {"const": fmt},
                "colorPolicy": {"enum": ["embed", "untagged", "convert_srgb"]},
                "dpi": {
                    "oneOf": [
                        {"type": "number", "minimum": 1, "maximum": 9600},
                        {
                            "type": "array",
                            "items": {"type": "number", "minimum": 1, "maximum": 9600},
                            "minItems": 2,
                            "maxItems": 2,
                        },
                    ]
                },
                "alphaMode": {"enum": ["preserve", "matte"]},
                "matte": RGBA,
                "encoderOptions": options,
            },
            ("path", "format"),
        )
        for fmt, options in OPTIONS.items()
    ]
    + [object_schema({**FILE, "format": {"enum": ["psd", "svg"]}}, ("path", "format"))]
}
PDF_SCHEMA = object_schema(
    {**FILE, "pageIds": PAGE_IDS, "withMarks": {"type": "boolean"}}, ("path",)
)
SEGMENT = object_schema(
    {
        "pageId": ID,
        "crop": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0},
            "minItems": 4,
            "maxItems": 4,
        },
        "gapBefore": {"type": "integer", "minimum": 0, "maximum": 100000},
        "gapAfter": {"type": "integer", "minimum": 0, "maximum": 100000},
        "include": {"type": "boolean"},
        "noSplitRanges": {
            "type": "array",
            "maxItems": 256,
            "items": {
                "type": "array",
                "items": {"type": "integer", "minimum": 0},
                "minItems": 2,
                "maxItems": 2,
            },
        },
    },
    ("pageId",),
)
WEBTOON_SCHEMA = object_schema(
    {
        **FILE,
        "pageIds": PAGE_IDS,
        "segments": {"type": "array", "items": SEGMENT, "minItems": 1, "maxItems": 5000},
        "targetWidth": {"type": "integer", "minimum": 1, "maximum": 16384},
        "maxSliceHeight": {"type": "integer", "minimum": 1, "maximum": 16384},
        "minSliceHeight": {"type": "integer", "minimum": 1, "maximum": 16384},
        "maxFileBytes": {"type": "integer", "minimum": 1, "maximum": 536870912},
        "format": {"enum": ["png", "jpeg", "webp"]},
        "namingTemplate": NAME,
        "writeManifest": {"type": "boolean"},
        "collisionPolicy": {"enum": ["replace", "skip", "suffix"]},
        "episodeName": {"type": "string", "maxLength": 256},
    },
    ("path",),
)
FIELD = {"type": "string", "minLength": 1, "maxLength": 2000, "pattern": r"\S"}
PUBLICATION = object_schema(
    {
        "title": FIELD,
        "language": FIELD,
        "identifier": FIELD,
        "creators": {"type": "array", "items": FIELD, "maxItems": 128},
        "series": {"type": ["string", "null"], "maxLength": 2000},
        "volume": {"type": ["string", "null"], "maxLength": 2000},
        "description": {"type": ["string", "null"], "maxLength": 2000},
    },
    ("title", "language", "identifier"),
)
DIGITAL_SCHEMA = object_schema(
    {
        **FILE,
        "format": {"enum": ["cbz", "epub3_fxl", "image_manifest_v1"]},
        "metadata": PUBLICATION,
        "pageIds": PAGE_IDS,
        "binding": {"enum": ["left", "right"]},
        "coverPageId": ID,
        "imageFormat": {"enum": ["png", "jpeg", "webp"]},
        "includeLoomStrokeManifest": {"type": "boolean"},
        "runEpubCheck": {"type": "boolean"},
    },
    ("path", "format", "metadata"),
)
