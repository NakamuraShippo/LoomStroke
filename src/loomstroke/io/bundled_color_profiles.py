"""Load the licensed, unmodified CMYK resources into a session registry."""

from __future__ import annotations

import hashlib
import json
import logging
from functools import lru_cache
from importlib.resources import files
from pathlib import PurePosixPath

from .color_profile_registry import ColorProfileInfo, ColorProfileRegistry

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def bundled_profile_payloads() -> tuple[bytes, ...]:
    """Validate the complete bundle before exposing any of its profile bytes.

    The catalog fixes the original descriptions and SHA-256 hashes. This
    uses package resources, including in frozen apps, and never accesses
    the network, OS profile directories, or user storage. Validation is
    cached once per process; transform construction stays off the startup
    path and is exercised in the profile bundle tests instead.
    """
    root = files("loomstroke.io").joinpath("resources", "icc")
    catalog = json.loads(root.joinpath("catalog.json").read_text(encoding="utf-8"))
    if (
        not isinstance(catalog, dict)
        or catalog.get("version") != 1
        or not isinstance(catalog.get("profiles"), list)
        or not catalog["profiles"]
    ):
        raise ValueError("Invalid bundled ICC catalog")
    registry = ColorProfileRegistry()
    payloads: list[bytes] = []
    for entry in catalog["profiles"]:
        if not isinstance(entry, dict):
            raise ValueError("Invalid bundled ICC entry")
        filename = entry["filename"]
        if (
            not isinstance(filename, str)
            or PurePosixPath(filename).name != filename
            or "\\" in filename
            or ":" in filename
            or not filename.lower().endswith(".icc")
        ):
            raise ValueError("Invalid bundled ICC filename")
        license_file = entry["license_file"]
        if license_file not in ("LICENSE-ZLIB-bICC.txt", "LICENSE-APTEC.txt"):
            raise ValueError("Unknown bundled ICC license")
        if not root.joinpath(license_file).is_file():
            raise ValueError(f"Missing bundled ICC license: {license_file}")
        payload = root.joinpath(filename).read_bytes()
        if not 128 <= len(payload) <= 4 * 1024 * 1024 or len(payload) != entry["byte_count"]:
            raise ValueError(f"Invalid bundled ICC size: {filename}")
        if hashlib.sha256(payload).hexdigest() != entry["sha256"]:
            raise ValueError(f"Bundled ICC checksum mismatch: {filename}")
        info = registry.register(payload)
        if info.colorspace != "CMYK" or info.name != entry["description"]:
            raise ValueError(f"Bundled ICC metadata mismatch: {filename}")
        payloads.append(payload)
    if len(registry.list_profiles()) != len(payloads):
        raise ValueError("Duplicate bundled ICC profiles")
    return tuple(payloads)


def register_bundled_color_profiles(
    registry: ColorProfileRegistry,
) -> tuple[ColorProfileInfo, ...]:
    """Add the validated set idempotently, preserving existing imported profiles."""
    payloads = bundled_profile_payloads()
    return tuple(registry.register(payload) for payload in payloads)


def create_print_profile_registry() -> ColorProfileRegistry:
    """Create the default UI registry; damaged packages retain manual ICC loading."""
    registry = ColorProfileRegistry()
    try:
        register_bundled_color_profiles(registry)
    except (OSError, ValueError, KeyError, TypeError):
        logger.error(
            "Bundled ICC profiles unavailable; manual ICC loading remains available", exc_info=True
        )
    return registry
