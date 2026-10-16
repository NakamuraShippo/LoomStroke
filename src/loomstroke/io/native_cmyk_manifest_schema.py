"""Dedicated v2 envelope schema, bundled as Python without changing packaging."""

from __future__ import annotations

import json

_SCHEMA = r"""
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "LoomStroke native CMYK v2 envelope; common fields use the v1 validator",
  "type": "object",
  "required": ["format", "format_version", "pages", "native_cmyk"],
  "properties": {
    "format": {"const": "LoomStrokeDocument"},
    "format_version": {"const": "2.0.0"},
    "native_cmyk": {
      "type": "object",
      "additionalProperties": false,
      "required": ["mode", "profile_member", "profile_sha256", "layers"],
      "properties": {
        "mode": {"enum": ["cmyk8", "cmyk16"]},
        "profile_member": {"const": "profiles/document.icc"},
        "profile_sha256": {"$ref": "#/$defs/digest"},
        "layers": {
          "type": "array", "minItems": 1, "maxItems": 10000,
          "items": {
            "type": "object", "additionalProperties": false,
            "required": ["page_id", "layer_id", "width", "height", "planes"],
            "properties": {
              "page_id": {"type": "string", "minLength": 1, "maxLength": 64},
              "layer_id": {"type": "string", "minLength": 1, "maxLength": 256},
              "width": {"type": "integer", "minimum": 1, "maximum": 67108864},
              "height": {"type": "integer", "minimum": 1, "maximum": 67108864},
              "planes": {
                "type": "array", "minItems": 5, "maxItems": 5,
                "items": {
                  "type": "object", "additionalProperties": false,
                  "required": ["channel", "member", "sha256"],
                  "properties": {
                    "channel": {"enum": ["c", "m", "y", "k", "a"]},
                    "member": {
                      "type": "string",
                      "pattern": "^pages/[A-Za-z0-9_-]{1,64}/channels/[a-f0-9]{32}_[cmyka]\\.png$"
                    },
                    "sha256": {"$ref": "#/$defs/digest"}
                  }
                }
              }
            }
          }
        }
      }
    }
  },
  "$defs": {"digest": {"type": "string", "pattern": "^[a-f0-9]{64}$"}}
}
"""


class NativeCmykManifestSchema:
    @staticmethod
    def load() -> dict:
        return json.loads(_SCHEMA)
