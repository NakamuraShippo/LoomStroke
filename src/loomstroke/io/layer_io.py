"""Layer-based document I/O module for LoomStroke.

Handles reading/writing the `.lstroke` archive along with manifest validation,
layer snapshots, and UI layout metadata per Master.md.
"""

from __future__ import annotations

import base64
import importlib
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import numpy as np
from numpy.typing import NDArray
from PIL import Image

from loomstroke.core.native_cmyk.pixels import NativeCmykPixels

from .file_io import ColorProfile, FileFormat, FileIO, ImageMetadata
from .lstroke_archive_policy import LstrokeArchiveMemberPolicy, png_dimensions_from_header
from .native_cmyk_archive_codec import NativeCmykArchiveCodec

logger = logging.getLogger(__name__)

try:
    from loomstroke.plugins.vector_tools.common import Anchor as VectorAnchor
    from loomstroke.plugins.vector_tools.common import (
        VectorObject,
        VectorPath,
        VectorStyle,
        ensure_vector_element_ids,
        ensure_vector_object_id,
    )
except Exception:  # pragma: no cover - fallback when vector plugin is unavailable
    # mypy sees these as redefinitions of the imported names; the try/except
    # fallback pattern is the canonical way to handle optional dependencies.

    @dataclass(frozen=True)
    class VectorAnchor:  # type: ignore[no-redef]  # vector plugin fallback definition
        pos: tuple[float, float]
        kind: str = "corner"
        h_in: tuple[float, float] | None = None
        h_out: tuple[float, float] | None = None

    @dataclass(frozen=True)
    class VectorStyle:  # type: ignore[no-redef]  # vector plugin fallback definition
        stroke_color: tuple[int, int, int] = (0, 0, 0)
        stroke_width: float = 1.0
        fill_enabled: bool = False
        fill_color: tuple[int, int, int] | None = None
        join: str = "miter"
        cap: str = "butt"
        miter_limit: float = 4.0
        dash: tuple[float, ...] = ()

    @dataclass(frozen=True)
    class VectorPath:  # type: ignore[no-redef]  # vector plugin fallback definition
        anchors: Sequence[VectorAnchor]
        closed: bool = False

    @dataclass(frozen=True)
    class VectorObject:  # type: ignore[no-redef]  # vector plugin fallback definition
        id: str
        paths: list[VectorPath]
        style: VectorStyle
        meta: dict[str, object] = field(default_factory=dict)
        preview: bool = False

    def ensure_vector_object_id(value: object, *, fallback_index: int = 0) -> str:
        del value
        return f"vec-fallback-{max(0, int(fallback_index)):04d}"

    def ensure_vector_element_ids(
        obj: VectorObject,
        *,
        object_index: int = 0,
    ) -> VectorObject:
        del object_index
        return obj


DOCUMENT_FORMAT = "LoomStrokeDocument"
DOCUMENT_FORMAT_VERSION = "1.3.0"
DOCUMENT_EXTENSION = ".lstroke"
MANIFEST_FILE = "manifest.json"
LAYERS_DIR = "layers"
THUMBNAILS_DIR = "thumbnails"
PAGES_DIR = "pages"
PAGE_THUMBNAIL_NAME = "page_thumb.png"
DOC_THUMBNAIL_NAME = "doc_thumb.png"
THUMBNAIL_SIZE = (256, 256)
PAGE_THUMBNAIL_SIZE = (512, 512)
DOC_THUMBNAIL_SIZE = (512, 512)
SUPPORTED_FORMAT_VERSIONS = ("1.0.0", "1.1.0", "1.2.0", "1.3.0")


@dataclass(slots=True)
class LayerSnapshot:
    """In-memory representation of a layer used for persistence."""

    id: str
    name: str
    image: NDArray[np.uint8]
    visible: bool = True
    opacity: float = 1.0
    blend_mode: str = "normal"
    locked: bool = False
    alpha_lock: bool = False
    position: tuple[int, int] = (0, 0)
    metadata: dict[str, Any] | None = None
    bounds: tuple[int, int, int, int] | None = None
    layer_type: str = "raster"
    vector_kind: str = "draw"
    vector_objects: list[Any] = field(default_factory=list)
    # --- Group / tree ---
    children: list[LayerSnapshot] = field(default_factory=list)
    # --- Clipping mask ---
    clipping: bool = False
    # --- Layer-attached mask ---
    layer_mask_image: NDArray[np.uint8] | None = None
    mask_enabled: bool = True
    # --- Effect layer ---
    effect_type: str | None = None
    effect_params: dict[str, Any] = field(default_factory=dict)
    effect_mask_image: NDArray[np.uint8] | None = None
    # --- AI Effect Layer (format_version 1.2.0) ---
    # Stored as opaque dicts at the persistence layer; the actual dataclasses
    # live in loomstroke.plugins.ai_effect.data_model but we don't import them
    # here to keep core → plugin dependency direction unidirectional.
    ai_effect: dict[str, Any] | None = None
    origin: dict[str, Any] | None = None
    # --- 3D Layer (format_version 1.3.0) ---
    # ``scene_3d`` may hold a loaded Scene3DDocument in memory; the manifest
    # stores only a safe relative GLB reference plus optional metadata.
    scene_3d: Any = None
    camera_3d: dict[str, Any] | None = None
    lighting_3d: dict[str, Any] | None = None
    display_mode_3d: str = "solid"
    gen_3d: dict[str, Any] | None = None
    native_cmyk: NativeCmykPixels | None = None

    # Canonical filter names share legacy dataclass storage and constructor kwargs.

    @property
    def filter_type(self) -> str | None:
        return self.effect_type

    @filter_type.setter
    def filter_type(self, value: str | None) -> None:
        self.effect_type = value

    @property
    def filter_params(self) -> dict[str, Any]:
        return self.effect_params

    @filter_params.setter
    def filter_params(self, value: dict[str, Any]) -> None:
        self.effect_params = value

    @property
    def filter_mask_image(self) -> NDArray[np.uint8] | None:
        return self.effect_mask_image

    @filter_mask_image.setter
    def filter_mask_image(self, value: NDArray[np.uint8] | None) -> None:
        self.effect_mask_image = value


@dataclass(slots=True)
class DocumentMetadata:
    """Metadata describing the full document/canvas state."""

    width: int
    height: int
    background_color: tuple[int, int, int, int] = (255, 255, 255, 255)
    dpi: tuple[float, float] = (72.0, 72.0)
    color_profile: ColorProfile | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    app_version: str = "0.0.0"
    format_version: str = "1.1.0"
    created: str | None = None
    modified: str | None = None
    ui_layout: dict[str, Any] | None = None

    def ensure_timestamps(self) -> None:
        """Populate created/modified timestamps when missing."""

        now = datetime.now(UTC).isoformat()
        if self.created is None:
            self.created = now
        if self.modified is None:
            self.modified = now


@dataclass(slots=True)
class CanvasSpec:
    """Canvas configuration for a page."""

    width: int
    height: int
    background_color: tuple[int, int, int, int]
    dpi: tuple[float, float]


@dataclass(slots=True)
class PageSnapshot:
    """In-memory representation of a single page."""

    id: str
    name: str | None = None
    index: int | None = None
    layers: list[LayerSnapshot] = field(default_factory=list)
    canvas: CanvasSpec | None = None
    page_thumbnail: str | None = None
    metadata: dict[str, Any] | None = None
    view_state: dict[str, Any] | None = None


@dataclass(slots=True)
class DocumentSnapshot:
    """In-memory representation of a document with pages."""

    metadata: DocumentMetadata
    pages: list[PageSnapshot]
    #: Saved per-page thumbnail PNG bytes, keyed by page id (runtime only;
    #: filled on load so the Pages panel can show every page immediately
    #: instead of placeholders until each page is activated).
    page_thumbnail_pngs: dict[str, bytes] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PageDirectoryEntry:
    """One page's manifest-level description without decoded content
    (CT-08 V1, spec 08 §8).  Runtime-only; never persisted."""

    page_id: str
    index: int
    name: str
    canvas: CanvasSpec | None
    metadata: dict[str, Any] | None
    view_state: dict[str, Any] | None
    layer_count: int
    thumbnail_member: str | None


class LayerIO:
    """Read/write `.lstroke` layered documents."""

    def __init__(self, file_io: FileIO | None = None) -> None:
        self._file_io = file_io or FileIO()

    # ------------------------------------------------------------------
    # Public API
    def save_document(
        self,
        path: Path | str,
        layers: Sequence[LayerSnapshot],
        metadata: DocumentMetadata,
        *,
        pages: Sequence[PageSnapshot] | None = None,
    ) -> bool:
        """Serialize the given layer snapshots into a `.lstroke` archive."""

        page_list = list(pages) if pages is not None else None
        if page_list is None:
            page_list = [
                PageSnapshot(
                    id="p_0001",
                    name="Page 1",
                    index=0,
                    layers=list(layers),
                    canvas=self._canvas_from_metadata(metadata),
                )
            ]
        snapshot = DocumentSnapshot(metadata=metadata, pages=page_list)
        return self.save_document_snapshot(path, snapshot)

    def save_document_snapshot(self, path: Path | str, snapshot: DocumentSnapshot) -> bool:
        """Serialize the given document snapshot into a `.lstroke` archive."""

        target = Path(path)
        if target.suffix.lower() != DOCUMENT_EXTENSION:
            target = target.with_suffix(DOCUMENT_EXTENSION)

        metadata = snapshot.metadata
        metadata.format_version = DOCUMENT_FORMAT_VERSION
        metadata.ensure_timestamps()
        temp_dir = self._create_temp_dir(target)

        try:
            (temp_dir / PAGES_DIR).mkdir(parents=True, exist_ok=True)

            if not snapshot.pages:
                raise ValueError("document snapshot must contain at least one page")
            normalized_pages = self._normalize_pages(snapshot.pages)
            for page in normalized_pages:
                self._write_page_snapshot(page, metadata, temp_dir)

            self._write_doc_thumbnail(normalized_pages, metadata, temp_dir)

            manifest = self._create_manifest_for_pages(normalized_pages, metadata)
            NativeCmykArchiveCodec.write(temp_dir, normalized_pages, manifest)
            metadata.format_version = manifest["format_version"]
            (temp_dir / MANIFEST_FILE).write_text(
                json.dumps(manifest, indent=2),
                encoding="utf-8",
            )

            # Write to a temporary file first, then atomically replace
            tmp_target = target.with_suffix(".lstroke.tmp")
            try:
                with zipfile.ZipFile(tmp_target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    for file_path in temp_dir.rglob("*"):
                        if file_path.is_file():
                            # PNGs are already compressed: store them as-is so
                            # the archive never double-deflates image data
                            # (double deflate is what pushed uniform 4K layers
                            # past the member compression-ratio policy).
                            # Manifest/JSON/GLB keep ZIP_DEFLATED.
                            compress_type = (
                                zipfile.ZIP_STORED
                                if file_path.suffix.lower() == ".png"
                                else zipfile.ZIP_DEFLATED
                            )
                            archive.write(
                                file_path,
                                file_path.relative_to(temp_dir),
                                compress_type=compress_type,
                            )
                # Atomic replace — original file is safe until this succeeds
                os.replace(tmp_target, target)
            except Exception:
                # Clean up partial temp file on failure (e.g. disk full)
                if tmp_target.exists():
                    tmp_target.unlink(missing_ok=True)
                raise

            logger.info(
                "Saved document: %s (%d pages)",
                target,
                len(normalized_pages),
            )
            return True
        except Exception as exc:  # pragma: no cover - logged for diagnosis
            logger.exception("Failed to save document: %s", exc)
            return False
        finally:
            self._cleanup_temp_dir(temp_dir)

    def load_document(
        self,
        path: Path | str,
        *,
        validate_manifest: bool = True,
        strict: bool = True,
    ) -> tuple[list[LayerSnapshot], DocumentMetadata]:
        """Load a `.lstroke` archive and return decoded layers/metadata."""

        snapshot = self.load_document_snapshot(
            path,
            validate_manifest=validate_manifest,
            strict=strict,
        )
        if not snapshot.pages:
            return [], snapshot.metadata
        return list(snapshot.pages[0].layers), snapshot.metadata

    def load_document_snapshot(
        self,
        path: Path | str,
        *,
        validate_manifest: bool = True,
        strict: bool = True,
    ) -> DocumentSnapshot:
        """Load a `.lstroke` archive and return decoded pages/metadata."""

        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(f"document not found: {source}")

        self._document_decode_bytes = 0
        try:
            with zipfile.ZipFile(source, "r") as archive:
                self._validate_archive_limits(archive)
                try:
                    # Bounded read: the manifest size cap was already checked
                    # against ZipInfo.file_size, and the stream read never
                    # materializes more than cap + 1 bytes.
                    manifest_raw = LstrokeArchiveMemberPolicy.read_manifest_bounded(archive)
                except KeyError as exc:
                    raise ValueError("manifest.json is missing from archive") from exc

                manifest = json.loads(manifest_raw.decode("utf-8"))
                if validate_manifest:
                    self._validate_manifest(manifest, strict=strict)

                self._validate_format_version(manifest, strict=strict)
                metadata = self._metadata_from_manifest(manifest)
                pages = self._pages_from_manifest(manifest, archive, metadata, strict=strict)
                NativeCmykArchiveCodec.attach(self, archive, manifest, pages)
                thumbnails = self._read_page_thumbnails(archive, pages)
        finally:
            self._document_decode_bytes = 0

        return DocumentSnapshot(metadata=metadata, pages=pages, page_thumbnail_pngs=thumbnails)

    # ------------------------------------------------------------------
    # Manifest helpers
    def _create_manifest_for_pages(
        self,
        pages: Sequence[PageSnapshot],
        metadata: DocumentMetadata,
        *,
        include_legacy_layers: bool = False,
    ) -> dict[str, Any]:
        """Build the JSON manifest payload for multi-page documents."""

        manifest: dict[str, Any] = {
            "format": DOCUMENT_FORMAT,
            "format_version": metadata.format_version,
            "app_version": metadata.app_version,
            "created": metadata.created,
            "modified": metadata.modified,
            "canvas": self._canvas_payload(self._canvas_from_metadata(metadata)),
            "pages": [],
            "metadata": metadata.metadata or {},
        }

        if metadata.color_profile is not None:
            manifest["color_profile"] = self._encode_color_profile(metadata.color_profile)

        if metadata.ui_layout is not None:
            manifest["ui_layout"] = metadata.ui_layout

        doc_canvas = self._canvas_from_metadata(metadata)
        for idx, page in enumerate(pages):
            page_entry: dict[str, Any] = {
                "id": page.id,
                "layers": [],
            }
            name = (page.name or "").strip()
            if not name:
                name = f"Page {idx + 1}"
            page_entry["name"] = name
            if page.index is not None:
                page_entry["index"] = int(page.index)
            if page.canvas is not None and not self._canvas_equals(page.canvas, doc_canvas):
                page_entry["canvas"] = self._canvas_payload(page.canvas)
            if page.metadata is not None:
                page_entry["metadata"] = dict(page.metadata)
            if page.view_state is not None:
                page_entry["view_state"] = dict(page.view_state)

            base_dir = f"{PAGES_DIR}/{page.id}"
            counter = [0]
            for _layer_index, snapshot in enumerate(page.layers):
                page_entry["layers"].append(
                    self._layer_entry(snapshot, counter[0], base_dir=base_dir, counter=counter)
                )
                counter[0] += 1

            if page.page_thumbnail:
                page_entry["page_thumbnail"] = page.page_thumbnail

            manifest["pages"].append(page_entry)

        if include_legacy_layers and pages:
            legacy_base = f"{PAGES_DIR}/{pages[0].id}"
            legacy_counter = [0]
            legacy_entries: list[dict[str, Any]] = []
            for _li, snapshot in enumerate(pages[0].layers):
                legacy_entries.append(
                    self._layer_entry(
                        snapshot, legacy_counter[0], base_dir=legacy_base, counter=legacy_counter
                    )
                )
                legacy_counter[0] += 1
            manifest["layers"] = legacy_entries

        return manifest

    def _layer_entry(  # noqa: PLR0912
        self,
        snapshot: LayerSnapshot,
        index: int,
        *,
        base_dir: str = "",
        counter: list[int] | None = None,
    ) -> dict[str, Any]:
        if counter is None:
            counter = [index]
        bounds = snapshot.bounds or self._compute_bounds(snapshot.image)
        prefix = f"{base_dir}/" if base_dir else ""
        idx = counter[0]
        entry: dict[str, Any] = {
            "id": snapshot.id,
            "name": snapshot.name,
            "visible": bool(snapshot.visible),
            "opacity": float(snapshot.opacity),
            "blend_mode": snapshot.blend_mode,
            "locked": bool(snapshot.locked),
            "alpha_lock": bool(snapshot.alpha_lock),
            "bounds": list(bounds),
            "position": list(snapshot.position),
            "image": f"{prefix}{LAYERS_DIR}/layer_{idx:03d}.png",
            "thumbnail": f"{prefix}{THUMBNAILS_DIR}/thumb_{idx:03d}.png",
            "metadata": snapshot.metadata or {},
        }
        layer_type = snapshot.layer_type or "raster"
        # ``ai_effect`` is part of the v1.2.0 schema (see SUPPORTED_FORMAT_VERSIONS).
        # Omitting it from the allow-list silently downgraded saved AI Effect
        # layers to ``"raster"`` on serialise; the matching ``ai_effect``
        # dict was still written but the layer came back as a plain raster
        # on reload because ``layer_type`` controls render path. Round-trip
        # bug: "AI Effect Layer becomes pixel layer after save/reopen".
        if layer_type not in {"raster", "vector", "mask", "group", "effect", "ai_effect", "3d"}:
            layer_type = "raster"
        if layer_type not in {"vector", "3d"} and snapshot.vector_objects:
            layer_type = "vector"
        entry["layer_type"] = layer_type
        vector_kind = getattr(snapshot, "vector_kind", "draw") or "draw"
        if layer_type == "vector" and vector_kind != "draw":
            entry["vector_kind"] = vector_kind
        if layer_type == "vector" or snapshot.vector_objects:
            entry["vector_objects"] = self._serialize_vector_objects(snapshot.vector_objects)
        # --- Clipping mask ---
        if snapshot.clipping:
            entry["clipping"] = True
        # --- Layer-attached mask ---
        if snapshot.layer_mask_image is not None:
            entry["layer_mask"] = f"{prefix}{LAYERS_DIR}/mask_{idx:03d}.png"
            entry["mask_enabled"] = bool(snapshot.mask_enabled)
        # --- Effect layer fields ---
        if snapshot.effect_type:
            entry["effect_type"] = snapshot.effect_type
            entry["effect_params"] = dict(snapshot.effect_params) if snapshot.effect_params else {}
            if snapshot.effect_mask_image is not None:
                entry["effect_mask"] = f"{prefix}{LAYERS_DIR}/effect_mask_{idx:03d}.png"
        # --- AI Effect Layer (format_version 1.2.0) ---
        if snapshot.ai_effect is not None:
            entry["ai_effect"] = dict(snapshot.ai_effect)
        if snapshot.origin is not None:
            entry["origin"] = dict(snapshot.origin)
        # --- 3D Layer (format_version 1.3.0) ---
        if layer_type == "3d":
            scene_3d = self._scene_3d_manifest_payload(
                snapshot.scene_3d,
                prefix=prefix,
                idx=idx,
                layer_id=snapshot.id,
            )
            if scene_3d is not None:
                entry["scene_3d"] = scene_3d
            camera_3d = self._json_object_payload(snapshot.camera_3d)
            if camera_3d is not None:
                entry["camera_3d"] = camera_3d
            lighting_3d = self._json_object_payload(snapshot.lighting_3d)
            if lighting_3d is not None:
                entry["lighting_3d"] = lighting_3d
            entry["display_mode_3d"] = str(snapshot.display_mode_3d or "solid")
            gen_3d = self._masked_json_object_payload(snapshot.gen_3d)
            if gen_3d is not None:
                entry["gen_3d"] = gen_3d
        # --- Children (recursive) ---
        if snapshot.children:
            child_entries: list[dict[str, Any]] = []
            for child in snapshot.children:
                counter[0] += 1
                child_entries.append(
                    self._layer_entry(child, counter[0], base_dir=base_dir, counter=counter)
                )
            entry["children"] = child_entries
        return entry

    def _normalize_pages(self, pages: Sequence[PageSnapshot]) -> list[PageSnapshot]:
        normalized: list[PageSnapshot] = []
        seen: set[str] = set()
        for idx, page in enumerate(pages):
            page_id = self._normalize_page_id(page.id, idx, seen, strict=False)
            seen.add(page_id)
            page_thumbnail = f"{PAGES_DIR}/{page_id}/{PAGE_THUMBNAIL_NAME}"
            normalized.append(
                PageSnapshot(
                    id=page_id,
                    name=page.name,
                    index=page.index,
                    layers=list(page.layers),
                    canvas=page.canvas,
                    page_thumbnail=page_thumbnail,
                    metadata=dict(page.metadata) if isinstance(page.metadata, dict) else None,
                    view_state=dict(page.view_state) if isinstance(page.view_state, dict) else None,
                )
            )
        return normalized

    def _normalize_page_id(
        self,
        page_id: str,
        index: int,
        seen: set[str],
        *,
        strict: bool,
    ) -> str:
        candidate = page_id.strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", candidate):
            if strict:
                raise ValueError(f"invalid page id: {page_id!r}")
            candidate = f"p_{index + 1:04d}"

        if candidate not in seen:
            return candidate

        if strict:
            raise ValueError(f"duplicate page id: {candidate}")

        base = candidate
        suffix = 1
        while True:
            alt = f"{base}_{suffix}"
            if len(alt) > 64:
                alt = f"p_{index + 1:04d}_{uuid4().hex[:8]}"
            if alt not in seen:
                return alt
            suffix += 1

    def _metadata_from_manifest(self, manifest: Mapping[str, Any]) -> DocumentMetadata:
        canvas = manifest.get("canvas", {})
        color_profile = None
        cp_payload = manifest.get("color_profile")
        if isinstance(cp_payload, Mapping):
            color_profile = self._decode_color_profile(cp_payload)

        raw_metadata = manifest.get("metadata")
        custom_metadata: dict[str, Any] = raw_metadata if isinstance(raw_metadata, dict) else {}

        metadata = DocumentMetadata(
            width=int(canvas.get("width", 0)),
            height=int(canvas.get("height", 0)),
            background_color=self._tuple4(canvas.get("background_color", [255, 255, 255, 255])),
            dpi=self._tuple2(canvas.get("dpi", [72.0, 72.0])),
            color_profile=color_profile,
            metadata=custom_metadata,
            app_version=str(manifest.get("app_version", "0.0.0")),
            format_version=str(manifest.get("format_version", "1.0.0")),
            created=manifest.get("created"),
            modified=manifest.get("modified"),
            ui_layout=(
                manifest.get("ui_layout") if isinstance(manifest.get("ui_layout"), dict) else None
            ),
        )
        metadata.ensure_timestamps()
        return metadata

    def _layers_from_manifest(
        self,
        manifest: Mapping[str, Any],
        archive: zipfile.ZipFile,
        *,
        strict: bool,
    ) -> list[LayerSnapshot]:
        entries = manifest.get("layers", [])
        if not isinstance(entries, list):
            raise ValueError("manifest.layers must be an array")
        return self._layer_snapshots_from_entries(
            entries,
            archive,
            strict=strict,
            context="manifest.layers",
        )

    def _layer_snapshots_from_entries(  # noqa: PLR0912, PLR0915
        self,
        entries: Sequence[Any],
        archive: zipfile.ZipFile,
        *,
        strict: bool,
        context: str,
    ) -> list[LayerSnapshot]:
        snapshots: list[LayerSnapshot] = []
        for idx, entry in enumerate(entries):
            if not isinstance(entry, Mapping):
                if strict:
                    raise ValueError(f"{context}[{idx}] is not an object")
                logger.warning("Skipping malformed layer entry at %s[%s]", context, idx)
                continue

            image_path = entry.get("image")
            if not isinstance(image_path, str):
                if strict:
                    raise ValueError(f"{context}[{idx}].image missing or invalid")
                logger.warning(
                    "Skipping layer entry without image reference at %s[%s]", context, idx
                )
                continue

            try:
                image = self._read_image_from_archive(archive, image_path)
            except ValueError as exc:
                if strict:
                    raise
                logger.warning("Skipping layer entry at %s[%s]: %s", context, idx, exc)
                continue

            bounds_value = entry.get("bounds")
            bounds = self._tuple4(bounds_value) if isinstance(bounds_value, (list, tuple)) else None
            position_f = self._tuple2(entry.get("position", (0, 0)))
            position = (int(position_f[0]), int(position_f[1]))
            vector_objects = self._deserialize_vector_objects(entry.get("vector_objects"))
            raw_layer_type = entry.get("layer_type")
            layer_type = str(raw_layer_type) if isinstance(raw_layer_type, str) else ""
            # ``ai_effect`` belongs to the v1.2.0 schema; without it in the
            # allow-list a correctly-saved AI Effect layer would round-trip
            # as ``"raster"`` even when the matching ``ai_effect`` dict is
            # present in the manifest entry. See sibling allow-list in the
            # serialise path for the same fix.
            if layer_type not in {"raster", "vector", "mask", "group", "effect", "ai_effect", "3d"}:
                layer_type = "vector" if vector_objects else "raster"
            raw_vector_kind = entry.get("vector_kind")
            vector_kind = str(raw_vector_kind) if isinstance(raw_vector_kind, str) else "draw"

            # --- Clipping ---
            clipping = bool(entry.get("clipping", False))
            # --- Layer mask ---
            layer_mask_image: NDArray[np.uint8] | None = None
            mask_enabled = bool(entry.get("mask_enabled", True))
            layer_mask_path = entry.get("layer_mask")
            if isinstance(layer_mask_path, str):
                try:
                    layer_mask_image = self._read_image_from_archive(archive, layer_mask_path)
                except ValueError:
                    logger.warning("Failed to load layer mask at %s", layer_mask_path)
            # --- Effect fields ---
            effect_type: str | None = None
            effect_params: dict[str, Any] = {}
            effect_mask_image: NDArray[np.uint8] | None = None
            raw_effect_type = entry.get("effect_type")
            if isinstance(raw_effect_type, str) and raw_effect_type:
                effect_type = raw_effect_type
                raw_params = entry.get("effect_params")
                effect_params = dict(raw_params) if isinstance(raw_params, dict) else {}
                effect_mask_path = entry.get("effect_mask")
                if isinstance(effect_mask_path, str):
                    try:
                        effect_mask_image = self._read_image_from_archive(archive, effect_mask_path)
                    except ValueError:
                        logger.warning("Failed to load effect mask at %s", effect_mask_path)
            # --- Children (recursive) ---
            children: list[LayerSnapshot] = []
            raw_children = entry.get("children")
            if isinstance(raw_children, list) and raw_children:
                children = self._layer_snapshots_from_entries(
                    raw_children,
                    archive,
                    strict=strict,
                    context=f"{context}[{idx}].children",
                )

            # --- AI Effect Layer (format_version 1.2.0) ---
            raw_ai_effect = entry.get("ai_effect")
            ai_effect = dict(raw_ai_effect) if isinstance(raw_ai_effect, dict) else None
            raw_origin = entry.get("origin")
            origin = dict(raw_origin) if isinstance(raw_origin, dict) else None
            # --- 3D Layer (format_version 1.3.0) ---
            scene_3d: Any = None
            camera_3d: dict[str, Any] | None = None
            lighting_3d: dict[str, Any] | None = None
            display_mode_3d = "solid"
            gen_3d: dict[str, Any] | None = None
            if layer_type == "3d":
                raw_scene_3d = entry.get("scene_3d")
                if isinstance(raw_scene_3d, Mapping):
                    scene_3d = dict(raw_scene_3d)
                    scene_path = raw_scene_3d.get("path")
                    if isinstance(scene_path, str) and scene_path:
                        try:
                            scene_3d = self._read_scene_3d_from_archive(archive, scene_path)
                        except ValueError as exc:
                            if strict:
                                raise
                            logger.warning(
                                "Failed to load 3D scene at %s[%s]: %s",
                                context,
                                idx,
                                exc,
                            )
                camera_3d = self._json_object_payload(entry.get("camera_3d"))
                lighting_3d = self._json_object_payload(entry.get("lighting_3d"))
                raw_display_mode = entry.get("display_mode_3d")
                if isinstance(raw_display_mode, str) and raw_display_mode:
                    display_mode_3d = raw_display_mode
                gen_3d = self._json_object_payload(entry.get("gen_3d"))

            snapshots.append(
                LayerSnapshot(
                    id=str(entry.get("id", f"layer-{idx}")),
                    name=str(entry.get("name", f"Layer {idx}")),
                    image=image,
                    visible=bool(entry.get("visible", True)),
                    opacity=float(entry.get("opacity", 1.0)),
                    blend_mode=str(entry.get("blend_mode", "normal")),
                    locked=bool(entry.get("locked", False)),
                    alpha_lock=bool(entry.get("alpha_lock", False)),
                    position=position,
                    metadata=(
                        entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
                    ),
                    bounds=bounds,
                    layer_type=layer_type,
                    vector_kind=vector_kind,
                    vector_objects=vector_objects,
                    children=children,
                    clipping=clipping,
                    layer_mask_image=layer_mask_image,
                    mask_enabled=mask_enabled,
                    effect_type=effect_type,
                    effect_params=effect_params,
                    effect_mask_image=effect_mask_image,
                    ai_effect=ai_effect,
                    origin=origin,
                    scene_3d=scene_3d,
                    camera_3d=camera_3d,
                    lighting_3d=lighting_3d,
                    display_mode_3d=display_mode_3d,
                    gen_3d=gen_3d,
                )
            )
        return snapshots

    def _pages_from_manifest(  # noqa: PLR0912, PLR0915
        self,
        manifest: Mapping[str, Any],
        archive: zipfile.ZipFile,
        metadata: DocumentMetadata,
        *,
        strict: bool,
    ) -> list[PageSnapshot]:
        raw_pages = manifest.get("pages")
        if isinstance(raw_pages, list) and raw_pages:
            legacy_layers = manifest.get("layers")
            if strict and isinstance(legacy_layers, list):
                self._validate_legacy_mirror(legacy_layers, raw_pages)

            pages: list[PageSnapshot] = []
            seen: set[str] = set()
            for idx, entry in enumerate(raw_pages):
                page = self._page_snapshot_from_entry(entry, idx, archive, seen=seen, strict=strict)
                if page is not None:
                    pages.append(page)

            if strict and not pages:
                raise ValueError("manifest.pages is empty or invalid")
            return pages

        legacy_layers = manifest.get("layers")
        if not isinstance(legacy_layers, list):
            if strict:
                raise ValueError("manifest.pages missing and manifest.layers invalid")
            return []

        layers = self._layers_from_manifest(manifest, archive, strict=strict)
        return [
            PageSnapshot(
                id="p_0001",
                name="Page 1",
                index=0,
                layers=layers,
                canvas=self._canvas_from_metadata(metadata),
            )
        ]

    def _page_snapshot_from_entry(
        self,
        entry: Any,
        idx: int,
        archive: zipfile.ZipFile,
        *,
        seen: set[str],
        strict: bool,
    ) -> PageSnapshot | None:
        """Decode one manifest page entry (CT-08 V1 extraction).

        Mechanically extracted from the ``_pages_from_manifest`` loop so a
        single page can be decoded without touching the others.  ``None``
        means the entry was skipped in lenient mode — strict mode raises
        exactly as the eager loop always did.
        """
        if not isinstance(entry, Mapping):
            if strict:
                raise ValueError(f"pages[{idx}] is not an object")
            logger.warning("Skipping malformed page entry at pages[%s]", idx)
            return None

        raw_id = entry.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            if strict:
                raise ValueError(f"pages[{idx}].id missing or invalid")
            raw_id = f"p_{idx + 1:04d}"

        page_id = self._normalize_page_id(str(raw_id), idx, seen, strict=strict)
        seen.add(page_id)

        name = entry.get("name")
        page_name = str(name) if isinstance(name, str) else None
        index_val = entry.get("index")
        page_index = int(index_val) if isinstance(index_val, int) else None

        canvas_payload = entry.get("canvas")
        page_canvas = (
            self._canvas_from_payload(canvas_payload)
            if isinstance(canvas_payload, Mapping)
            else None
        )

        layer_entries = entry.get("layers")
        if not isinstance(layer_entries, list):
            if strict:
                raise ValueError(f"pages[{page_id}].layers missing or invalid")
            logger.warning("Skipping page without layers at pages[%s]", page_id)
            return None

        layers = self._layer_snapshots_from_entries(
            layer_entries,
            archive,
            strict=strict,
            context=f"pages[{page_id}].layers",
        )

        page_thumbnail = entry.get("page_thumbnail")
        if isinstance(page_thumbnail, str):
            try:
                page_thumbnail = self._normalize_archive_path(page_thumbnail)
            except ValueError as exc:
                if strict:
                    raise
                logger.warning("Ignoring invalid page_thumbnail for %s: %s", page_id, exc)
                page_thumbnail = None
        else:
            page_thumbnail = None

        metadata_value = entry.get("metadata")
        page_metadata = dict(metadata_value) if isinstance(metadata_value, dict) else None
        view_state_value = entry.get("view_state")
        view_state = dict(view_state_value) if isinstance(view_state_value, dict) else None

        return PageSnapshot(
            id=page_id,
            name=page_name,
            index=page_index,
            layers=layers,
            canvas=page_canvas,
            page_thumbnail=page_thumbnail,
            metadata=page_metadata,
            view_state=view_state,
        )

    # ------------------------------------------------------------------
    # CT-08 V1: lazy page directory + single-page decode.
    def read_page_directory(
        self,
        path: Path | str,
        *,
        validate_manifest: bool = True,
        strict: bool = True,
    ) -> tuple[DocumentMetadata, list[PageDirectoryEntry]]:
        """Manifest-level page directory without decoding any layer image.

        Same archive/manifest validation as the eager load; only the
        layer *entries* are counted, never opened.  Legacy single-page
        manifests surface as one synthetic ``p_0001`` entry, mirroring
        ``load_document_snapshot``.
        """
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(f"document not found: {source}")
        with zipfile.ZipFile(source, "r") as archive:
            self._validate_archive_limits(archive)
            manifest = self._read_validated_manifest(
                archive, validate_manifest=validate_manifest, strict=strict
            )
            metadata = self._metadata_from_manifest(manifest)
            entries: list[PageDirectoryEntry] = []
            raw_pages = manifest.get("pages")
            if isinstance(raw_pages, list) and raw_pages:
                seen: set[str] = set()
                for idx, entry in enumerate(raw_pages):
                    directory_entry = self._directory_entry_from_manifest(
                        entry, idx, seen=seen, strict=strict
                    )
                    if directory_entry is not None:
                        entries.append(directory_entry)
                if strict and not entries:
                    raise ValueError("manifest.pages is empty or invalid")
            elif isinstance(manifest.get("layers"), list):
                entries.append(
                    PageDirectoryEntry(
                        page_id="p_0001",
                        index=0,
                        name="Page 1",
                        canvas=self._canvas_from_metadata(metadata),
                        metadata=None,
                        view_state=None,
                        layer_count=len(manifest["layers"]),
                        thumbnail_member=None,
                    )
                )
            elif strict:
                raise ValueError("manifest.pages missing and manifest.layers invalid")
        return metadata, entries

    def read_page_thumbnail_pngs(
        self, path: Path | str, *, strict: bool = True
    ) -> dict[str, bytes]:
        """Saved page thumbnails by page id, without decoding any layer
        (CT-08 V2 lazy open)."""
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(f"document not found: {source}")
        _metadata, entries = self.read_page_directory(source, strict=strict)
        thumbnails: dict[str, bytes] = {}
        with zipfile.ZipFile(source, "r") as archive:
            for entry in entries:
                if not entry.thumbnail_member:
                    continue
                try:
                    member = self._normalize_archive_path(entry.thumbnail_member)
                    payload = archive.read(member)
                except (KeyError, OSError, ValueError, zipfile.BadZipFile):
                    continue
                if payload:
                    thumbnails[entry.page_id] = payload
        return thumbnails

    def load_single_page_snapshot(
        self,
        path: Path | str,
        page_id: str,
        *,
        strict: bool = True,
    ) -> PageSnapshot | None:
        """Decode exactly one page from the archive (CT-08 V1).

        Walks the manifest with the same id normalization the eager load
        uses (duplicate/invalid ids resolve identically), decodes only
        the matching entry, and returns ``None`` when no page carries
        ``page_id``.
        """
        source = Path(path)
        if not source.exists():
            raise FileNotFoundError(f"document not found: {source}")
        self._document_decode_bytes = 0
        try:
            with zipfile.ZipFile(source, "r") as archive:
                self._validate_archive_limits(archive)
                manifest = self._read_validated_manifest(
                    archive, validate_manifest=True, strict=strict
                )
                metadata = self._metadata_from_manifest(manifest)
                raw_pages = manifest.get("pages")
                if isinstance(raw_pages, list) and raw_pages:
                    seen: set[str] = set()
                    for idx, entry in enumerate(raw_pages):
                        normalized = self._peek_entry_page_id(entry, idx, seen, strict=strict)
                        if normalized is None:
                            continue
                        if normalized != page_id:
                            continue
                        page = self._page_snapshot_from_entry(
                            entry, idx, archive, seen=set(seen) - {normalized}, strict=strict
                        )
                        NativeCmykArchiveCodec.attach(self, archive, manifest, [page])
                        return page
                    return None
                if page_id == "p_0001" and isinstance(manifest.get("layers"), list):
                    layers = self._layers_from_manifest(manifest, archive, strict=strict)
                    return PageSnapshot(
                        id="p_0001",
                        name="Page 1",
                        index=0,
                        layers=layers,
                        canvas=self._canvas_from_metadata(metadata),
                    )
                return None
        finally:
            self._document_decode_bytes = 0

    @staticmethod
    def _read_page_thumbnails(
        archive: zipfile.ZipFile, pages: Sequence[PageSnapshot]
    ) -> dict[str, bytes]:
        """Saved page-thumbnail PNGs, best effort (a missing or broken
        member falls back to the runtime placeholder, never fails the
        load).  Member paths were already normalized by the page decode."""
        thumbnails: dict[str, bytes] = {}
        for page in pages:
            member = page.page_thumbnail
            if not member:
                continue
            try:
                payload = archive.read(member)
            except (KeyError, OSError, zipfile.BadZipFile):
                logger.debug("page thumbnail member unreadable: %s", member)
                continue
            if payload:
                thumbnails[page.id] = payload
        return thumbnails

    def _read_validated_manifest(
        self,
        archive: zipfile.ZipFile,
        *,
        validate_manifest: bool,
        strict: bool,
    ) -> dict[str, Any]:
        try:
            manifest_raw = LstrokeArchiveMemberPolicy.read_manifest_bounded(archive)
        except KeyError as exc:
            raise ValueError("manifest.json is missing from archive") from exc
        manifest = json.loads(manifest_raw.decode("utf-8"))
        if validate_manifest:
            self._validate_manifest(manifest, strict=strict)
        self._validate_format_version(manifest, strict=strict)
        return manifest

    def _peek_entry_page_id(
        self, entry: Any, idx: int, seen: set[str], *, strict: bool
    ) -> str | None:
        """The normalized id an entry WOULD get, advancing ``seen`` the
        same way the eager loop does (so duplicate ids resolve
        identically) without decoding anything."""
        if not isinstance(entry, Mapping):
            if strict:
                raise ValueError(f"pages[{idx}] is not an object")
            return None
        raw_id = entry.get("id")
        if not isinstance(raw_id, str) or not raw_id.strip():
            if strict:
                raise ValueError(f"pages[{idx}].id missing or invalid")
            raw_id = f"p_{idx + 1:04d}"
        page_id = self._normalize_page_id(str(raw_id), idx, seen, strict=strict)
        seen.add(page_id)
        return page_id

    def _directory_entry_from_manifest(
        self, entry: Any, idx: int, *, seen: set[str], strict: bool
    ) -> PageDirectoryEntry | None:
        page_id = self._peek_entry_page_id(entry, idx, seen, strict=strict)
        if page_id is None:
            return None
        assert isinstance(entry, Mapping)
        layer_entries = entry.get("layers")
        if not isinstance(layer_entries, list):
            if strict:
                raise ValueError(f"pages[{page_id}].layers missing or invalid")
            return None
        canvas_payload = entry.get("canvas")
        thumbnail = entry.get("page_thumbnail")
        metadata_value = entry.get("metadata")
        view_state_value = entry.get("view_state")
        index_val = entry.get("index")
        name = entry.get("name")
        return PageDirectoryEntry(
            page_id=page_id,
            index=int(index_val) if isinstance(index_val, int) else idx,
            name=str(name) if isinstance(name, str) else f"Page {idx + 1}",
            canvas=(
                self._canvas_from_payload(canvas_payload)
                if isinstance(canvas_payload, Mapping)
                else None
            ),
            metadata=dict(metadata_value) if isinstance(metadata_value, dict) else None,
            view_state=dict(view_state_value) if isinstance(view_state_value, dict) else None,
            layer_count=len(layer_entries),
            thumbnail_member=thumbnail if isinstance(thumbnail, str) else None,
        )

    # ------------------------------------------------------------------
    # Persistence helpers
    def _write_layer_snapshot(
        self,
        snapshot: LayerSnapshot,
        index: int,
        layers_dir: Path,
        thumbnails_dir: Path,
        *,
        counter: list[int] | None = None,
    ) -> None:
        if counter is None:
            counter = [index]
        idx = counter[0]
        image = self._ensure_uint8(snapshot.image)
        meta = ImageMetadata(
            width=image.shape[1],
            height=image.shape[0],
            channels=image.shape[2],
            bit_depth=8,
            color_profile=None,
        )
        layer_path = layers_dir / f"layer_{idx:03d}.png"
        self._file_io.write(layer_path, image, meta, format=FileFormat.PNG)
        thumb_path = thumbnails_dir / f"thumb_{idx:03d}.png"
        self._write_thumbnail(image, thumb_path)
        # --- Layer mask ---
        if snapshot.layer_mask_image is not None:
            mask_img = self._ensure_uint8(snapshot.layer_mask_image)
            mask_meta = ImageMetadata(
                width=mask_img.shape[1],
                height=mask_img.shape[0],
                channels=mask_img.shape[2],
                bit_depth=8,
                color_profile=None,
            )
            mask_path = layers_dir / f"mask_{idx:03d}.png"
            self._file_io.write(mask_path, mask_img, mask_meta, format=FileFormat.PNG)
        # --- Effect mask ---
        if snapshot.effect_mask_image is not None:
            emask_img = self._ensure_uint8(snapshot.effect_mask_image)
            emask_meta = ImageMetadata(
                width=emask_img.shape[1],
                height=emask_img.shape[0],
                channels=emask_img.shape[2],
                bit_depth=8,
                color_profile=None,
            )
            emask_path = layers_dir / f"effect_mask_{idx:03d}.png"
            self._file_io.write(emask_path, emask_img, emask_meta, format=FileFormat.PNG)
        # --- 3D scene payload ---
        if snapshot.layer_type == "3d" and snapshot.scene_3d is not None:
            scene_path = layers_dir / self._scene_3d_file_name(snapshot.id, idx)
            self._write_scene_3d_payload(snapshot.scene_3d, scene_path)
        # --- Children ---
        for child in snapshot.children:
            counter[0] += 1
            self._write_layer_snapshot(
                child, counter[0], layers_dir, thumbnails_dir, counter=counter
            )

    def _write_thumbnail(self, image: NDArray[np.uint8], path: Path) -> None:
        pil_image = Image.fromarray(self._ensure_uint8(image))
        pil_image.thumbnail(THUMBNAIL_SIZE)
        path.parent.mkdir(parents=True, exist_ok=True)
        pil_image.save(path, format="PNG")

    def _write_page_snapshot(
        self,
        page: PageSnapshot,
        metadata: DocumentMetadata,
        root: Path,
    ) -> None:
        page_root = root / PAGES_DIR / page.id
        layers_dir = page_root / LAYERS_DIR
        thumbnails_dir = page_root / THUMBNAILS_DIR
        layers_dir.mkdir(parents=True, exist_ok=True)
        thumbnails_dir.mkdir(parents=True, exist_ok=True)

        counter = [0]
        for _index, snapshot in enumerate(page.layers):
            self._write_layer_snapshot(
                snapshot, counter[0], layers_dir, thumbnails_dir, counter=counter
            )
            counter[0] += 1

        page_thumb_path = page_root / PAGE_THUMBNAIL_NAME
        try:
            self._write_page_thumbnail(page, metadata, page_thumb_path)
            page.page_thumbnail = f"{PAGES_DIR}/{page.id}/{PAGE_THUMBNAIL_NAME}"
        except Exception as exc:  # pragma: no cover - non-critical
            page.page_thumbnail = None
            logger.debug("Failed to write page thumbnail for %s: %s", page.id, exc)

    def _write_page_thumbnail(
        self,
        page: PageSnapshot,
        metadata: DocumentMetadata,
        path: Path,
    ) -> None:
        canvas = page.canvas or self._canvas_from_metadata(metadata)
        composite = self._render_composite(page.layers, canvas)
        composite.thumbnail(PAGE_THUMBNAIL_SIZE)
        path.parent.mkdir(parents=True, exist_ok=True)
        composite.save(path, format="PNG")

    def _write_doc_thumbnail(
        self,
        pages: Sequence[PageSnapshot],
        metadata: DocumentMetadata,
        root: Path,
    ) -> None:
        if not pages:
            return
        first_page = pages[0]
        try:
            canvas = first_page.canvas or self._canvas_from_metadata(metadata)
            composite = self._render_composite(first_page.layers, canvas)
            composite.thumbnail(DOC_THUMBNAIL_SIZE)
            doc_path = root / DOC_THUMBNAIL_NAME
            composite.save(doc_path, format="PNG")
        except Exception as exc:  # pragma: no cover - non-critical
            logger.debug("Failed to write doc thumbnail: %s", exc)

    def _render_composite(
        self,
        layers: Sequence[LayerSnapshot],
        canvas: CanvasSpec,
    ) -> Image.Image:
        base = Image.new(
            "RGBA",
            (int(canvas.width), int(canvas.height)),
            cast(tuple[int, int, int, int], tuple(canvas.background_color)),
        )
        for snapshot in layers:
            if not snapshot.visible:
                continue
            layer_image = Image.fromarray(self._ensure_uint8(snapshot.image), mode="RGBA")
            if snapshot.opacity < 1.0:
                opacity = float(snapshot.opacity)
                alpha = layer_image.getchannel("A").point(
                    lambda a, _opacity=opacity: int(a * _opacity)
                )
                layer_image.putalpha(alpha)
            base.paste(layer_image, snapshot.position, layer_image)
        return base

    # ------------------------------------------------------------------
    # Validation helpers
    # Archive safety limits
    _MAX_ARCHIVE_MEMBERS = 10_000
    _MAX_ARCHIVE_UNCOMPRESSED_BYTES = 4 * 1024 * 1024 * 1024  # 4 GB
    # Compression-ratio limits are content-aware and live in
    # ``lstroke_archive_policy``: non-image members keep the legacy 200x
    # limit, and only verified LoomStroke PNG members get the bounded
    # backward-compat allowance for past double-deflated saves.
    _MAX_LAYER_IMAGE_PIXELS = 67_108_864  # 8192 x 8192
    _MAX_LAYER_IMAGE_BYTES = 256 * 1024 * 1024
    _MAX_3D_SCENE_BYTES = 256 * 1024 * 1024
    _MAX_DOCUMENT_DECODE_BYTES = 1024 * 1024 * 1024

    def _validate_archive_limits(self, archive: zipfile.ZipFile) -> None:
        """Guard against ZIP bombs and malformed archives."""
        members = archive.infolist()
        if len(members) > self._MAX_ARCHIVE_MEMBERS:
            raise ValueError(
                f"Archive member count ({len(members)}) exceeds limit ({self._MAX_ARCHIVE_MEMBERS})"
            )
        policy = LstrokeArchiveMemberPolicy(
            max_image_bytes=self._MAX_LAYER_IMAGE_BYTES,
            max_image_pixels=self._MAX_LAYER_IMAGE_PIXELS,
            max_document_decode_bytes=self._MAX_DOCUMENT_DECODE_BYTES,
        )
        total_uncompressed = 0
        seen_members: set[str] = set()
        for info in members:
            safe_name = self._normalize_archive_path(info.filename)
            if safe_name in seen_members:
                raise ValueError(f"Archive contains duplicate entry: {info.filename}")
            seen_members.add(safe_name)

            total_uncompressed += info.file_size
            policy.validate_member(archive, info, safe_name)
            if safe_name == MANIFEST_FILE:
                # Reject an oversized manifest before load ever reads it.
                policy.validate_manifest_info(info)
        if total_uncompressed > self._MAX_ARCHIVE_UNCOMPRESSED_BYTES:
            raise ValueError(
                f"Total uncompressed size ({total_uncompressed:,} bytes) exceeds limit "
                f"({self._MAX_ARCHIVE_UNCOMPRESSED_BYTES:,} bytes)"
            )

    def _validate_manifest(self, manifest: Mapping[str, Any], *, strict: bool) -> None:
        manifest = NativeCmykArchiveCodec.common_manifest(dict(manifest))
        schema_validation = importlib.import_module("loomstroke.io.schema_validation")
        factory = schema_validation.get_validator_factory()
        if factory is None:
            if strict:
                self._basic_manifest_checks(manifest)
            return

        schema = schema_validation.load_manifest_schema()
        validator = factory(schema)
        try:
            validator.validate(manifest)
        except schema_validation.JsonSchemaValidationError as exc:
            raise ValueError(f"manifest schema validation failed: {exc}") from exc

    def _basic_manifest_checks(self, manifest: Mapping[str, Any]) -> None:
        required = {"format", "format_version", "canvas"}
        missing = [key for key in required if key not in manifest]
        if missing:
            raise ValueError(f"manifest missing required keys: {', '.join(missing)}")
        if manifest.get("format") != DOCUMENT_FORMAT:
            raise ValueError(f"unexpected manifest format: {manifest.get('format')}")
        if "layers" not in manifest and "pages" not in manifest:
            raise ValueError("manifest missing layers/pages payload")

    def _validate_format_version(self, manifest: Mapping[str, Any], *, strict: bool) -> None:
        version = str(manifest.get("format_version", "1.0.0"))
        if version == "2.0.0":
            NativeCmykArchiveCodec.validate(dict(manifest))
            return
        if version in SUPPORTED_FORMAT_VERSIONS:
            return
        if strict:
            raise ValueError(f"unsupported format_version: {version}")
        if not version.startswith("1."):
            raise ValueError(f"unsupported format_version: {version}")
        logger.warning("Unknown format_version %s; attempting lenient load", version)

    def _validate_legacy_mirror(
        self,
        legacy_layers: Sequence[Any],
        pages: Sequence[Any],
    ) -> None:
        if not pages:
            return
        first_page = pages[0]
        if not isinstance(first_page, Mapping):
            raise ValueError("pages[0] is not an object")
        page_layers = first_page.get("layers")
        if not isinstance(page_layers, list):
            raise ValueError("pages[0].layers missing or invalid")
        if len(legacy_layers) != len(page_layers):
            raise ValueError("manifest.layers conflicts with pages[0].layers")
        for _idx, (legacy, page_layer) in enumerate(zip(legacy_layers, page_layers, strict=False)):
            if not isinstance(legacy, Mapping) or not isinstance(page_layer, Mapping):
                raise ValueError("manifest.layers conflicts with pages[0].layers")
            if legacy.get("id") != page_layer.get("id") or legacy.get("image") != page_layer.get(
                "image"
            ):
                raise ValueError("manifest.layers conflicts with pages[0].layers")

    # ------------------------------------------------------------------
    # Utility helpers
    def _canvas_from_metadata(self, metadata: DocumentMetadata) -> CanvasSpec:
        return CanvasSpec(
            width=int(metadata.width),
            height=int(metadata.height),
            background_color=metadata.background_color,
            dpi=metadata.dpi,
        )

    def _canvas_from_payload(self, payload: Mapping[str, Any]) -> CanvasSpec:
        return CanvasSpec(
            width=int(payload.get("width", 0)),
            height=int(payload.get("height", 0)),
            background_color=self._tuple4(payload.get("background_color", [255, 255, 255, 255])),
            dpi=self._tuple2(payload.get("dpi", [72.0, 72.0])),
        )

    @staticmethod
    def _canvas_payload(canvas: CanvasSpec) -> dict[str, Any]:
        return {
            "width": int(canvas.width),
            "height": int(canvas.height),
            "background_color": list(canvas.background_color),
            "dpi": [float(canvas.dpi[0]), float(canvas.dpi[1])],
        }

    @staticmethod
    def _canvas_equals(left: CanvasSpec, right: CanvasSpec) -> bool:
        return (
            left.width == right.width
            and left.height == right.height
            and left.background_color == right.background_color
            and left.dpi == right.dpi
        )

    def _scene_3d_manifest_payload(
        self,
        value: Any,
        *,
        prefix: str,
        idx: int,
        layer_id: str,
    ) -> dict[str, Any] | None:
        if value is None:
            return None
        if self._is_scene_3d_document(value):
            return {"path": f"{prefix}{LAYERS_DIR}/{self._scene_3d_file_name(layer_id, idx)}"}
        payload = self._json_object_payload(value)
        if payload is None:
            return None
        raw_path = payload.get("path")
        if isinstance(raw_path, str) and raw_path:
            payload["path"] = self._normalize_archive_path(raw_path)
        return payload

    def _write_scene_3d_payload(self, value: Any, path: Path) -> None:
        if self._is_scene_3d_document(value):
            from loomstroke.core.assets_3d import save_scene  # noqa: PLC0415

            path.parent.mkdir(parents=True, exist_ok=True)
            save_scene(value, path)
            return

        if not isinstance(value, Mapping):
            return
        raw_source = value.get("local_path") or value.get("source_path")
        if not isinstance(raw_source, str) or not raw_source:
            return
        source = Path(raw_source).expanduser().resolve()
        if source.suffix.lower() != ".glb" or not source.is_file():
            return
        if source.stat().st_size > self._MAX_3D_SCENE_BYTES:
            raise ValueError(
                f"3D scene source size ({source.stat().st_size:,} bytes) exceeds "
                f"limit ({self._MAX_3D_SCENE_BYTES:,} bytes)"
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, path)

    def _read_scene_3d_from_archive(self, archive: zipfile.ZipFile, relative_path: str) -> Any:
        safe_path = self._normalize_archive_path(relative_path)
        if not safe_path.lower().endswith(".glb"):
            raise ValueError(f"3D scene '{safe_path}' must be a GLB file")
        try:
            info = archive.getinfo(safe_path)
        except KeyError as exc:
            raise ValueError(f"3D scene '{safe_path}' missing from archive") from exc
        if info.file_size > self._MAX_3D_SCENE_BYTES:
            raise ValueError(
                f"3D scene '{safe_path}' size ({info.file_size:,} bytes) exceeds "
                f"limit ({self._MAX_3D_SCENE_BYTES:,} bytes)"
            )
        self._record_document_decode_budget(safe_path, info.file_size)
        data = archive.read(safe_path)

        from loomstroke.core.assets_3d import load_scene  # noqa: PLC0415

        with tempfile.TemporaryDirectory(prefix="loomstroke_scene3d_") as temp_root:
            temp_path = Path(temp_root) / Path(safe_path).name
            temp_path.write_bytes(data)
            return load_scene(temp_path)

    @staticmethod
    def _scene_3d_file_name(layer_id: str, idx: int) -> str:
        slug = re.sub(r"[^A-Za-z0-9_-]+", "_", str(layer_id)).strip("_")
        if not slug:
            slug = "layer"
        return f"scene_3d_{idx:03d}_{slug[:48]}.glb"

    @staticmethod
    def _is_scene_3d_document(value: Any) -> bool:
        try:
            from loomstroke.core.assets_3d import Scene3DDocument  # noqa: PLC0415
        except ImportError:  # pragma: no cover - optional 3D package unavailable
            return False
        return isinstance(value, Scene3DDocument)

    def _json_object_payload(self, value: Any) -> dict[str, Any] | None:
        if isinstance(value, Mapping):
            sanitized = self._sanitize_json_value(value)
            return dict(sanitized) if isinstance(sanitized, dict) else None
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            raw = to_dict()
            if isinstance(raw, Mapping):
                sanitized = self._sanitize_json_value(raw)
                return dict(sanitized) if isinstance(sanitized, dict) else None
        return None

    def _masked_json_object_payload(self, value: Any) -> dict[str, Any] | None:
        from loomstroke.core.assets_3d.provider_contract import (  # noqa: PLC0415
            mask_sensitive_metadata,
        )

        payload = self._json_object_payload(value)
        if payload is None:
            return None

        masked = mask_sensitive_metadata(payload)
        return dict(masked) if isinstance(masked, dict) else payload

    def _serialize_vector_objects(self, objects: Sequence[Any]) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for obj in objects or []:
            payload = self._serialize_vector_object(obj)
            if payload is not None:
                payloads.append(payload)
        return payloads

    def _serialize_vector_object(self, obj: Any) -> dict[str, Any] | None:
        if obj is None:
            return None
        paths_raw = getattr(obj, "paths", None) or []
        paths: list[dict[str, Any]] = []
        for path in paths_raw:
            path_payload = self._serialize_vector_path(path)
            if path_payload is not None:
                paths.append(path_payload)
        style_payload = self._serialize_vector_style(getattr(obj, "style", None))
        payload: dict[str, Any] = {
            "id": str(getattr(obj, "id", "")),
            "paths": paths,
            "style": style_payload,
        }
        meta = getattr(obj, "meta", None)
        if meta is not None:
            meta_payload = self._sanitize_json_value(meta)
            payload["meta"] = meta_payload if isinstance(meta_payload, dict) else {}
        preview = getattr(obj, "preview", None)
        if preview is not None:
            payload["preview"] = bool(preview)
        return payload

    def _serialize_vector_path(self, path: Any) -> dict[str, Any] | None:
        anchors_raw = getattr(path, "anchors", None) or []
        anchors: list[dict[str, Any]] = []
        for anchor in anchors_raw:
            anchor_payload = self._serialize_vector_anchor(anchor)
            if anchor_payload is not None:
                anchors.append(anchor_payload)
        if not anchors:
            return None
        return {
            "anchors": anchors,
            "closed": bool(getattr(path, "closed", False)),
        }

    def _serialize_vector_anchor(self, anchor: Any) -> dict[str, Any] | None:
        pos = self._coerce_point(getattr(anchor, "pos", None))
        if pos is None:
            return None
        return {
            "pos": pos,
            "kind": str(getattr(anchor, "kind", "corner") or "corner"),
            "h_in": self._coerce_point(getattr(anchor, "h_in", None), allow_none=True),
            "h_out": self._coerce_point(getattr(anchor, "h_out", None), allow_none=True),
        }

    def _serialize_vector_style(self, style: Any) -> dict[str, Any]:
        stroke_color = self._coerce_color(getattr(style, "stroke_color", None))
        if stroke_color is None:
            stroke_color = [0, 0, 0]
        fill_color = self._coerce_color(getattr(style, "fill_color", None))
        return {
            "stroke_color": stroke_color,
            "stroke_width": float(getattr(style, "stroke_width", 1.0) or 1.0),
            "fill_enabled": bool(getattr(style, "fill_enabled", False)),
            "fill_color": fill_color,
            "join": str(getattr(style, "join", "miter") or "miter"),
            "cap": str(getattr(style, "cap", "butt") or "butt"),
            "miter_limit": float(getattr(style, "miter_limit", 4.0) or 4.0),
            "dash": self._coerce_dash(getattr(style, "dash", ())),
        }

    def _deserialize_vector_objects(self, raw: Any) -> list[Any]:
        if not isinstance(raw, list):
            return []
        objects: list[Any] = []
        for index, entry in enumerate(raw):
            obj = self._deserialize_vector_object(entry, object_index=index)
            if obj is not None:
                objects.append(obj)
        return objects

    def _deserialize_vector_object(self, raw: Any, *, object_index: int = 0) -> Any | None:
        if not isinstance(raw, Mapping):
            return None
        obj_id = ensure_vector_object_id(raw, fallback_index=object_index)
        paths_raw = raw.get("paths", [])
        paths: list[VectorPath] = []
        if isinstance(paths_raw, list):
            for path_entry in paths_raw:
                path = self._deserialize_vector_path(path_entry)
                if path is not None:
                    paths.append(path)
        style = self._deserialize_vector_style(raw.get("style"))
        meta = raw.get("meta")
        meta_dict = dict(meta) if isinstance(meta, Mapping) else {}
        preview = bool(raw.get("preview", False))
        obj = VectorObject(
            id=str(obj_id), paths=paths, style=style, meta=meta_dict, preview=preview
        )
        return ensure_vector_element_ids(obj, object_index=object_index)

    def _deserialize_vector_path(self, raw: Any) -> VectorPath | None:
        if not isinstance(raw, Mapping):
            return None
        anchors_raw = raw.get("anchors", [])
        anchors: list[VectorAnchor] = []
        if isinstance(anchors_raw, list):
            for anchor_entry in anchors_raw:
                anchor = self._deserialize_vector_anchor(anchor_entry)
                if anchor is not None:
                    anchors.append(anchor)
        if not anchors:
            return None
        closed = bool(raw.get("closed", False))
        return VectorPath(anchors=anchors, closed=closed)

    def _deserialize_vector_anchor(self, raw: Any) -> VectorAnchor | None:
        if not isinstance(raw, Mapping):
            return None
        pos = self._parse_point(raw.get("pos"))
        if pos is None:
            return None
        h_in = self._parse_point(raw.get("h_in"), allow_none=True)
        h_out = self._parse_point(raw.get("h_out"), allow_none=True)
        kind = str(raw.get("kind", "corner") or "corner")
        return VectorAnchor(pos=pos, kind=kind, h_in=h_in, h_out=h_out)

    def _deserialize_vector_style(self, raw: Any) -> VectorStyle:
        if not isinstance(raw, Mapping):
            return VectorStyle()
        stroke_color = self._parse_color(raw.get("stroke_color")) or (0, 0, 0)
        fill_color = self._parse_color(raw.get("fill_color"))
        dash = self._parse_dash(raw.get("dash"))
        stroke_width = float(raw.get("stroke_width", 1.0) or 1.0)
        miter_limit = float(raw.get("miter_limit", 4.0) or 4.0)
        return VectorStyle(
            stroke_color=stroke_color,
            stroke_width=stroke_width,
            fill_enabled=bool(raw.get("fill_enabled", False)),
            fill_color=fill_color,
            join=str(raw.get("join", "miter") or "miter"),
            cap=str(raw.get("cap", "butt") or "butt"),
            miter_limit=miter_limit,
            dash=dash,
        )

    @staticmethod
    def _coerce_point(value: Any, *, allow_none: bool = False) -> list[float] | None:
        if value is None:
            return None if allow_none else None  # noqa: RUF034
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                return [float(value[0]), float(value[1])]
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _parse_point(value: Any, *, allow_none: bool = False) -> tuple[float, float] | None:
        if value is None:
            return None if allow_none else None  # noqa: RUF034
        if isinstance(value, (list, tuple)) and len(value) >= 2:
            try:
                return (float(value[0]), float(value[1]))
            except (TypeError, ValueError):
                return None
        return None

    @staticmethod
    def _coerce_color(value: Any) -> list[int] | None:
        if value is None:
            return None
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            try:
                r_f, g_f, b_f = (float(value[0]), float(value[1]), float(value[2]))
            except (TypeError, ValueError):
                return None
            scale = 255.0 if max(r_f, g_f, b_f) <= 1.0 else 1.0
            r = round(r_f * scale)
            g = round(g_f * scale)
            b = round(b_f * scale)
            r = max(0, min(255, r))
            g = max(0, min(255, g))
            b = max(0, min(255, b))
            return [r, g, b]
        return None

    @staticmethod
    def _parse_color(value: Any) -> tuple[int, int, int] | None:
        if value is None:
            return None
        if isinstance(value, (list, tuple)) and len(value) >= 3:
            try:
                r_f, g_f, b_f = (float(value[0]), float(value[1]), float(value[2]))
            except (TypeError, ValueError):
                return None
            scale = 255.0 if max(r_f, g_f, b_f) <= 1.0 else 1.0
            r = round(r_f * scale)
            g = round(g_f * scale)
            b = round(b_f * scale)
            r = max(0, min(255, r))
            g = max(0, min(255, g))
            b = max(0, min(255, b))
            return (r, g, b)
        return None

    @staticmethod
    def _coerce_dash(value: Any) -> list[float]:
        if isinstance(value, (list, tuple)):
            dash: list[float] = []
            for item in value:
                try:
                    dash.append(float(item))
                except (TypeError, ValueError):
                    continue
            return dash
        return []

    @staticmethod
    def _parse_dash(value: Any) -> tuple[float, ...]:
        if isinstance(value, (list, tuple)):
            dash: list[float] = []
            for item in value:
                try:
                    dash.append(float(item))
                except (TypeError, ValueError):
                    continue
            return tuple(dash)
        return ()

    def _sanitize_json_value(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (list, tuple)):
            return [self._sanitize_json_value(item) for item in value]
        if isinstance(value, Mapping):
            return {str(key): self._sanitize_json_value(val) for key, val in value.items()}
        return str(value)

    def _normalize_archive_path(self, relative_path: str) -> str:
        normalized = relative_path.replace("\\", "/").strip()
        if not normalized:
            raise ValueError("empty path")
        if normalized.startswith(("/", "\\")):
            raise ValueError(f"absolute path not allowed: {relative_path}")
        if ":" in normalized.split("/")[0]:
            raise ValueError(f"absolute path not allowed: {relative_path}")
        parts = [part for part in normalized.split("/") if part]
        if any(part == ".." for part in parts):
            raise ValueError(f"path traversal not allowed: {relative_path}")
        return "/".join(parts)

    def _create_temp_dir(self, target: Path) -> Path:
        base_dir = target.parent
        base_dir.mkdir(parents=True, exist_ok=True)
        # Truncate stem to avoid exceeding Windows MAX_PATH (260 chars).
        # Keep first 40 chars for debuggability + short UUID suffix.
        stem = target.stem[:40]
        while True:
            candidate = base_dir / f".lstroke_tmp_{stem}_{uuid4().hex[:12]}"
            try:
                candidate.mkdir()
            except FileExistsError:
                continue
            return candidate

    def _cleanup_temp_dir(self, path: Path) -> None:
        try:
            shutil.rmtree(path)
        except Exception as exc:  # pragma: no cover - best effort cleanup
            logger.debug("Failed to clean temp directory: %s", exc)

    def _read_image_from_archive(
        self, archive: zipfile.ZipFile, relative_path: str
    ) -> NDArray[np.uint8]:
        safe_path = self._normalize_archive_path(relative_path)
        width, height = self._validate_image_member_limits(archive, safe_path)
        self._record_document_decode_budget(safe_path, width * height * 4)
        try:
            data = archive.read(safe_path)
        except KeyError as exc:
            raise ValueError(f"layer image '{safe_path}' missing from archive") from exc

        with Image.open(BytesIO(data)) as img:
            rgba = img.convert("RGBA")
            arr = np.array(rgba, dtype=np.uint8)
        return arr

    def _validate_image_member_limits(
        self,
        archive: zipfile.ZipFile,
        safe_path: str,
    ) -> tuple[int, int]:
        try:
            info = archive.getinfo(safe_path)
        except KeyError as exc:
            raise ValueError(f"layer image '{safe_path}' missing from archive") from exc

        if info.file_size > self._MAX_LAYER_IMAGE_BYTES:
            raise ValueError(
                f"layer image '{safe_path}' encoded size ({info.file_size:,} bytes) exceeds "
                f"limit ({self._MAX_LAYER_IMAGE_BYTES:,} bytes)"
            )

        with archive.open(info, "r") as handle:
            header = handle.read(33)
        width, height = self._png_dimensions_from_header(header, safe_path)
        pixels = width * height
        decoded_bytes = pixels * 4
        if pixels > self._MAX_LAYER_IMAGE_PIXELS:
            raise ValueError(
                f"layer image '{safe_path}' dimensions ({width}x{height}) exceed pixel limit "
                f"({self._MAX_LAYER_IMAGE_PIXELS:,})"
            )
        if decoded_bytes > self._MAX_LAYER_IMAGE_BYTES:
            raise ValueError(
                f"layer image '{safe_path}' decoded size ({decoded_bytes:,} bytes) exceeds "
                f"limit ({self._MAX_LAYER_IMAGE_BYTES:,} bytes)"
            )
        return width, height

    @staticmethod
    def _png_dimensions_from_header(header: bytes, safe_path: str) -> tuple[int, int]:
        return png_dimensions_from_header(header, safe_path)

    def _record_document_decode_budget(self, safe_path: str, decoded_bytes: int) -> None:
        current = int(getattr(self, "_document_decode_bytes", 0))
        total = current + decoded_bytes
        if total > self._MAX_DOCUMENT_DECODE_BYTES:
            raise ValueError(
                f"document decoded image budget exceeded while reading '{safe_path}' "
                f"({total:,} bytes > {self._MAX_DOCUMENT_DECODE_BYTES:,} bytes)"
            )
        self._document_decode_bytes = total

    @staticmethod
    def _ensure_uint8(image: NDArray[np.uint8]) -> NDArray[np.uint8]:
        if image.dtype != np.uint8:
            image = np.clip(image, 0, 255).astype(np.uint8)
        if not image.flags["C_CONTIGUOUS"]:
            image = np.ascontiguousarray(image)
        return image

    @staticmethod
    def _tuple2(value: Sequence[float]) -> tuple[float, float]:
        seq = list(value)
        if not seq:
            return 0.0, 0.0
        if len(seq) == 1:
            seq.append(seq[0])
        return float(seq[0]), float(seq[1])

    @staticmethod
    def _tuple4(value: Sequence[int]) -> tuple[int, int, int, int]:
        padded = list(value) + [255] * (4 - len(value))
        clamped = [int(max(0, min(255, component))) for component in padded[:4]]
        return clamped[0], clamped[1], clamped[2], clamped[3]

    @staticmethod
    def _encode_color_profile(profile: ColorProfile) -> dict[str, Any]:
        payload = {
            "name": profile.name,
            "colorspace": profile.colorspace,
            "icc_base64": base64.b64encode(profile.data).decode("ascii") if profile.data else None,
        }
        return payload

    @staticmethod
    def _decode_color_profile(payload: Mapping[str, Any]) -> ColorProfile:
        data_b64 = payload.get("icc_base64")
        data = base64.b64decode(data_b64) if isinstance(data_b64, str) else None
        return ColorProfile(
            name=str(payload.get("name", "Unknown")),
            data=data,
            colorspace=str(payload.get("colorspace", "RGB")),
        )

    @staticmethod
    def _compute_bounds(image: NDArray[np.uint8]) -> tuple[int, int, int, int]:
        mask = image[..., 3] > 0 if image.shape[2] >= 4 else image.max(axis=2) > 0
        coords = np.column_stack(np.where(mask))
        if coords.size == 0:
            return (0, 0, image.shape[1], image.shape[0])
        y_min, x_min = coords.min(axis=0)
        y_max, x_max = coords.max(axis=0)
        return (int(x_min), int(y_min), int(x_max - x_min + 1), int(y_max - y_min + 1))


# ---------------------------------------------------------------------
# Public vector-object codec (CT-07 T2 fix).  The page template store is
# a JSON file, so live VectorObject instances must go through the same
# dict round-trip .lstroke uses — one codec, one wire format.
_vector_codec_instance: LayerIO | None = None


def _vector_codec() -> LayerIO:
    global _vector_codec_instance  # noqa: PLW0603 - lazy module singleton
    if _vector_codec_instance is None:
        _vector_codec_instance = LayerIO()
    return _vector_codec_instance


def serialize_vector_objects(objects: Sequence[Any]) -> list[dict[str, Any]]:
    """Live vector objects -> the JSON-safe dicts ``.lstroke`` stores."""
    return _vector_codec()._serialize_vector_objects(objects)  # noqa: SLF001


def deserialize_vector_objects(raw: Any) -> list[Any]:
    """The inverse: stored dicts -> live vector objects."""
    return _vector_codec()._deserialize_vector_objects(raw)  # noqa: SLF001


__all__ = [
    "DOCUMENT_FORMAT",
    "DOCUMENT_FORMAT_VERSION",
    "CanvasSpec",
    "DocumentMetadata",
    "DocumentSnapshot",
    "LayerIO",
    "LayerSnapshot",
    "PageDirectoryEntry",
    "PageSnapshot",
    "deserialize_vector_objects",
    "serialize_vector_objects",
]
