"""v2 CMYK payloads around the validated common layer/page manifest."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import jsonschema
import numpy as np

from loomstroke.core.native_cmyk.color_transform import CmykColorTransform
from loomstroke.core.native_cmyk.pixels import NativeCmykPixels

from .native_cmyk_manifest_schema import NativeCmykManifestSchema
from .native_cmyk_plane_codec import NativeCmykPlaneCodec


class NativeCmykArchiveCodec:
    @staticmethod
    def layers(pages):
        for page in pages:
            stack = list(reversed(page.layers))
            while stack:
                layer = stack.pop()
                yield page.id, layer
                stack.extend(reversed(layer.children))

    @classmethod
    def write(cls, folder: Path, pages, manifest: dict[str, Any]) -> None:
        layers = list(cls.layers(pages))
        natives = [layer.native_cmyk for _, layer in layers if layer.native_cmyk is not None]
        if not natives:
            return
        profile, bits = natives[0].profile, natives[0].bits
        entries = []
        for page_id, layer in layers:
            native = layer.native_cmyk
            if layer.layer_type == "raster" and native is None:
                raise ValueError("A CMYK document cannot contain an RGB raster payload")
            if native is None:
                continue
            if layer.layer_type != "raster" or native.profile != profile or native.bits != bits:
                raise ValueError("Native CMYK layers must share mode and profile")
            if layer.image.shape[:2] != native.alpha.shape:
                raise ValueError("Native CMYK preview dimensions do not match the source")
            key = hashlib.sha256(layer.id.encode()).hexdigest()[:32]
            height, width = native.alpha.shape
            entry = dict(page_id=page_id, layer_id=layer.id, width=width, height=height, planes=[])
            for index, channel in enumerate("cmyka"):
                plane = native.inks[:, :, index] if index < 4 else native.alpha
                member = f"pages/{page_id}/channels/{key}_{channel}.png"
                output = folder / member
                output.parent.mkdir(parents=True, exist_ok=True)
                payload = NativeCmykPlaneCodec.encode(plane)
                output.write_bytes(payload)
                entry["planes"].append(
                    dict(channel=channel, member=member, sha256=hashlib.sha256(payload).hexdigest())
                )
            entries.append(entry)
        output = folder / "profiles/document.icc"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(profile)
        manifest["format_version"] = "2.0.0"
        # Common raster payloads are sRGB display previews, not the original RGB space.
        manifest["color_profile"] = {
            "name": "sRGB IEC61966-2.1",
            "colorspace": "RGB",
            "icc_base64": None,
        }
        manifest["native_cmyk"] = dict(
            mode=f"cmyk{bits}",
            profile_member="profiles/document.icc",
            profile_sha256=hashlib.sha256(profile).hexdigest(),
            layers=entries,
        )
        cls.validate(manifest)

    @staticmethod
    def validate(manifest: dict[str, Any]) -> None:
        schema = NativeCmykManifestSchema.load()
        try:
            jsonschema.Draft202012Validator(schema).validate(manifest)
        except jsonschema.ValidationError as exc:
            raise ValueError(f"Native CMYK manifest is invalid: {exc.message}") from exc

    @classmethod
    def common_manifest(cls, manifest: dict[str, Any]) -> dict[str, Any]:
        if manifest.get("format_version") != "2.0.0":
            return manifest
        cls.validate(manifest)
        result = dict(manifest)
        result.pop("native_cmyk")
        result["format_version"] = "1.3.0"
        return result

    @classmethod
    def attach(cls, owner, archive, manifest, pages) -> None:
        if manifest.get("format_version") != "2.0.0":
            return
        cls.validate(manifest)
        native = manifest["native_cmyk"]
        bits = 8 if native["mode"] == "cmyk8" else 16
        try:
            profile_info = archive.getinfo(native["profile_member"])
        except KeyError as exc:
            raise ValueError("Native ICC profile is missing from the archive") from exc
        if profile_info.file_size > 4 * 1024 * 1024:
            raise ValueError("Native ICC profile exceeds its budget")
        profile = archive.read(profile_info)
        if hashlib.sha256(profile).hexdigest() != native["profile_sha256"]:
            raise ValueError("Native ICC profile digest mismatch")
        CmykColorTransform.validate_profile(profile)
        layers = {(page_id, layer.id): layer for page_id, layer in cls.layers(pages)}
        if len(layers) != sum(1 for _ in cls.layers(pages)):
            raise ValueError("Native page/layer identifiers are not unique")
        seen: set[tuple[str, str]] = set()
        members: set[str] = set()
        page_ids = {page.id for page in pages}
        for entry in native["layers"]:
            key = (entry["page_id"], entry["layer_id"])
            if key in seen:
                raise ValueError("Duplicate native layer descriptor")
            seen.add(key)
            if key[0] not in page_ids:
                continue  # Single-page lazy load: other pages remain encoded.
            layer = layers.get(key)
            if layer is None or layer.layer_type != "raster":
                raise ValueError("Native descriptor does not identify a raster layer")
            width, height = entry["width"], entry["height"]
            if layer.image.shape[:2] != (height, width):
                raise ValueError("Native and preview dimensions differ")
            decoded_bytes = width * height * 5 * (bits // 8)
            if decoded_bytes > owner._MAX_LAYER_IMAGE_BYTES:
                raise ValueError("Native layer exceeds its decode budget")
            owner._record_document_decode_budget("native CMYK", decoded_bytes)
            channels = cls._read_planes(owner, archive, entry, key, members, bits)
            layer.native_cmyk = NativeCmykPixels(
                np.stack([channels[c] for c in "cmyk"], axis=2), channels["a"], profile
            )
            # Derived preview is regenerated, never treated as the native source.
            layer.image = layer.native_cmyk.preview()
        if any(
            layer.layer_type == "raster" and layer.native_cmyk is None for layer in layers.values()
        ):
            raise ValueError("A native CMYK raster payload is missing")

    @staticmethod
    def _read_planes(owner, archive, entry, key, members, bits):
        channels = {}
        expected_key = hashlib.sha256(key[1].encode()).hexdigest()[:32]
        for plane in entry["planes"]:
            channel, member = plane["channel"], plane["member"]
            if (
                member != f"pages/{key[0]}/channels/{expected_key}_{channel}.png"
                or channel in channels
                or member in members
            ):
                raise ValueError("Native plane path/order is inconsistent")
            members.add(member)
            try:
                info = archive.getinfo(member)
            except KeyError as exc:
                raise ValueError("Native plane is missing from the archive") from exc
            if info.file_size > owner._MAX_LAYER_IMAGE_BYTES:
                raise ValueError("Encoded native plane exceeds its budget")
            with archive.open(info) as stream:
                header = stream.read(33)
            if len(header) < 33 or header[24] != bits or header[25] != 0:
                raise ValueError("Native PNG sample format mismatch")
            channels[channel] = NativeCmykPlaneCodec.decode(
                archive.read(info),
                width=entry["width"],
                height=entry["height"],
                bits=bits,
                digest=plane["sha256"],
            )
        if set(channels) != set("cmyka"):
            raise ValueError("Native CMYK requires four ink planes and alpha")
        return channels
