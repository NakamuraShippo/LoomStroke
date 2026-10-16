"""``.lsfx`` ZIP read/write with atomic writes.

Spec reference:
- ``AIeffect_design/02_file_format/lsfx_specification_v1_1.md``
- ``AIeffect_design/04_python_design/preset_authoring.md`` §4.2

``.lsfx`` is a standard ZIP containing:

.. code-block:: text

    preset.lsfx (ZIP)
    ├── manifest.json        (required)
    ├── icon.png             (optional)
    ├── preview.png          (optional)
    └── gateways/
        └── {gateway_id}.json

Atomic write (``write_lsfx_atomic``) uses a temp file in the same directory
plus ``os.replace`` so partial writes never leave a corrupt file in place.
Critical when a user overwrites a .lsfx that another process has open.
"""

from __future__ import annotations

import hashlib
import json
import logging
import zipfile
from pathlib import Path
from typing import Any

from loomstroke.plugins.ai_effect._atomic_io import atomic_write_binary
from loomstroke.plugins.ai_effect.preset import AIEffectPreset

logger = logging.getLogger(__name__)


MANIFEST_ENTRY = "manifest.json"
GATEWAY_DIR = "gateways/"
ICON_ENTRY = "icon.png"
PREVIEW_ENTRY = "preview.png"

# Zip-bomb defence: per-entry uncompressed-size ceilings checked BEFORE
# decompression. A malicious ``.lsfx`` that pointed its icon.png entry at
# 5 GiB of compressed zeroes would OOM the process without these caps.
# Values are conservative — legitimate authoring stays well under them.
MAX_MANIFEST_SIZE = 5 * 1024 * 1024  # 5 MiB — manifest.json is tiny JSON
MAX_GATEWAY_FILE_SIZE = 10 * 1024 * 1024  # 10 MiB — Gateway workflows
MAX_ASSET_SIZE = 20 * 1024 * 1024  # 20 MiB — icon.png / preview.png


class LsfxReadError(ValueError):
    """Raised when a ``.lsfx`` archive is corrupt, missing required entries,
    or otherwise unreadable."""


def _check_entry_size(
    archive: zipfile.ZipFile,
    entry: str,
    max_size: int,
    archive_path: Path,
) -> None:
    """Reject .lsfx entries whose declared uncompressed size exceeds ``max_size``.

    Checked via ``ZipInfo.file_size`` (header-declared uncompressed length)
    before ``archive.read()`` is called, so an attacker-crafted zip bomb
    never gets a chance to allocate.
    """
    info = archive.getinfo(entry)
    if info.file_size > max_size:
        raise LsfxReadError(
            f"{archive_path}: entry {entry!r} exceeds max size "
            f"({info.file_size} > {max_size} bytes)"
        )


def read_lsfx(
    path: Path | str,
    *,
    source: str = "imported",
) -> AIEffectPreset:
    """Read a ``.lsfx`` ZIP into an :class:`AIEffectPreset` in memory.

    Args:
        path: Absolute or relative path to the ``.lsfx`` file.
        source: Origin tag for the loaded preset (``"builtin"`` / ``"user"``
            / ``"imported"``). Does not affect on-disk content.

    Raises:
        LsfxReadError: Archive malformed, manifest.json missing, or
            required gateway file missing.
        FileNotFoundError: Path does not exist.
    """
    archive_path = Path(path)
    if not archive_path.exists():
        raise FileNotFoundError(f".lsfx not found: {archive_path}")

    try:
        with zipfile.ZipFile(archive_path, "r") as archive:
            names = set(archive.namelist())

            if MANIFEST_ENTRY not in names:
                raise LsfxReadError(f"{archive_path}: missing manifest.json")

            _check_entry_size(archive, MANIFEST_ENTRY, MAX_MANIFEST_SIZE, archive_path)
            manifest_raw = archive.read(MANIFEST_ENTRY)
            try:
                manifest = json.loads(manifest_raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise LsfxReadError(f"{archive_path}: manifest.json parse error: {exc}") from exc

            if not isinstance(manifest, dict):
                raise LsfxReadError(f"{archive_path}: manifest.json is not an object")

            gateway_id = manifest.get("gateway")
            if not isinstance(gateway_id, str) or not gateway_id:
                raise LsfxReadError(f"{archive_path}: manifest.json has no gateway key")

            # Load all gateway files present (spec allows multiple for future
            # multi-gateway presets; primary is manifest['gateway'])
            gateway_files = _load_gateway_files(archive, names, archive_path)

            primary = gateway_files.get(gateway_id)
            if primary is None:
                raise LsfxReadError(
                    f"{archive_path}: manifest references gateway={gateway_id!r} "
                    f"but gateways/{gateway_id}.json is missing"
                )
            primary_hash = _sha256_json(primary)

            icon_image: bytes | None = None
            if ICON_ENTRY in names:
                _check_entry_size(archive, ICON_ENTRY, MAX_ASSET_SIZE, archive_path)
                icon_image = archive.read(ICON_ENTRY)
            preview_image: bytes | None = None
            if PREVIEW_ENTRY in names:
                _check_entry_size(archive, PREVIEW_ENTRY, MAX_ASSET_SIZE, archive_path)
                preview_image = archive.read(PREVIEW_ENTRY)
    except zipfile.BadZipFile as exc:
        raise LsfxReadError(f"{archive_path}: not a valid ZIP: {exc}") from exc

    preset_id = manifest.get("preset_id", "")
    return AIEffectPreset(
        preset_id=str(preset_id),
        manifest=manifest,
        gateway_files=gateway_files,
        gateway_file_hash=primary_hash,
        source=source,
        lsfx_path=archive_path,
        icon_image=icon_image,
        preview_image=preview_image,
    )


def write_lsfx_atomic(
    output_path: Path | str,
    manifest: dict[str, Any],
    gateway_files: dict[str, dict[str, Any]],
    *,
    icon_bytes: bytes | None = None,
    preview_bytes: bytes | None = None,
) -> None:
    """Write a ``.lsfx`` ZIP atomically.

    Algorithm (preset_authoring.md §4.2):

    1. Create temp file ``{name}.lsfx.tmp.{pid}`` in the target directory
    2. Build ZIP with manifest.json + gateway files (+ optional assets)
    3. ``os.replace`` the temp to the final path
    4. On any error, remove the temp file

    Atomic replacement means a crash between steps 1 and 3 never leaves a
    half-written ``.lsfx``; the original file (if any) survives intact.

    Args:
        output_path: Destination .lsfx path.
        manifest: Parsed manifest.json dict. Serialized with ``indent=2``.
        gateway_files: gateway_id → gateway_file dict. Each is written at
            ``gateways/{gateway_id}.json``.
        icon_bytes: Optional icon.png bytes.
        preview_bytes: Optional preview.png bytes.
    """
    target = Path(output_path)

    def _write_zip(tmp_path: Path) -> None:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                MANIFEST_ENTRY,
                json.dumps(manifest, indent=2, ensure_ascii=False),
            )
            for gw_id, gw_data in gateway_files.items():
                archive.writestr(
                    f"{GATEWAY_DIR}{gw_id}.json",
                    json.dumps(gw_data, indent=2, ensure_ascii=False),
                )
            if icon_bytes is not None:
                archive.writestr(ICON_ENTRY, icon_bytes)
            if preview_bytes is not None:
                archive.writestr(PREVIEW_ENTRY, preview_bytes)

    # Shared helper guarantees fsync + atomic replace + temp cleanup on failure.
    atomic_write_binary(target, _write_zip)


def _load_gateway_files(
    archive: zipfile.ZipFile,
    names: set[str],
    archive_path: Path,
) -> dict[str, dict[str, Any]]:
    """Read every ``gateways/{id}.json`` entry into a dict keyed by id.

    Enforces the per-entry size cap before decompression and rejects
    non-object payloads. Ignores subdirectory entries and non-.json files
    under ``gateways/`` so foreign tooling can't smuggle extra payloads in.
    """
    gateway_files: dict[str, dict[str, Any]] = {}
    for name in names:
        if not name.startswith(GATEWAY_DIR) or name == GATEWAY_DIR:
            continue
        rel = name[len(GATEWAY_DIR) :]
        if "/" in rel or not rel.endswith(".json"):
            continue
        gw_id = rel[: -len(".json")]
        _check_entry_size(archive, name, MAX_GATEWAY_FILE_SIZE, archive_path)
        try:
            gw_data = json.loads(archive.read(name).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise LsfxReadError(f"{archive_path}: {name} parse error: {exc}") from exc
        if not isinstance(gw_data, dict):
            raise LsfxReadError(f"{archive_path}: {name} is not an object")
        gateway_files[gw_id] = gw_data
    return gateway_files


def _sha256_json(payload: dict[str, Any]) -> str:
    """Canonical sha256 digest of a JSON dict.

    Uses ``sort_keys=True`` + compact separators so the same logical payload
    always produces the same digest regardless of formatting quirks.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()
