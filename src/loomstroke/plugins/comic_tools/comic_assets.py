"""Comic asset library — reusable comic parts stored per category on disk.

Phase 2 of the raster-rig integration: a confirmed balloon (rig / raster
preserve / parametric) can be registered as an ASSET and later dropped onto
any canvas from the "Comic Assets" panel. Assets are plain files under the
plugin's user_data directory::

    <plugin_data_dir("comic_tools")>/assets/<category>/<asset_id>/asset.json
                                                             /thumb.png

``asset.json`` carries the ``BalloonRecord.to_meta()`` payload (crops
included for raster records, so an asset is self-contained) plus name /
timestamps. Categories are just directory names — new kinds of assets get a
new category (and a new tab in the panel) without touching the store.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from .model import BalloonRecord, move_balloon_record

logger = logging.getLogger(__name__)

ASSET_MIME = "application/x-loomstroke-comic-asset+json"
ASSET_PAYLOAD_VERSION = 1
CATEGORY_BALLOON = "balloon"
#: categories the panel knows today (stable id -> display label); more
#: categories are added here + a tab in the panel
CATEGORY_LABELS: dict[str, str] = {CATEGORY_BALLOON: "フキダシ"}
ASSET_FILE = "asset.json"
THUMB_FILE = "thumb.png"
THUMB_PX = 96


@dataclass
class ComicAsset:
    asset_id: str
    category: str
    name: str
    created_at: float
    #: the stored record payload (``BalloonRecord.to_meta()`` for balloons)
    record_meta: dict[str, Any] = field(default_factory=dict)
    thumbnail_png: bytes | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def record(self) -> BalloonRecord | None:
        return BalloonRecord.from_meta(self.record_meta)


def default_assets_root() -> Path:
    from loomstroke.platform.user_dirs import plugin_data_dir  # noqa: PLC0415

    return plugin_data_dir("comic_tools") / "assets"


class ComicAssetStore:
    """File-backed asset library (one folder per asset, atomic writes)."""

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else None

    @property
    def root(self) -> Path:
        if self._root is None:
            self._root = default_assets_root()
        return self._root

    # -- listing ------------------------------------------------------
    def categories(self) -> list[str]:
        known = list(CATEGORY_LABELS)
        try:
            for child in sorted(self.root.iterdir()):
                if child.is_dir() and child.name not in known:
                    known.append(child.name)
        except OSError:
            pass
        return known

    def list(self, category: str) -> list[ComicAsset]:
        folder = self.root / _safe_name(category)
        assets: list[ComicAsset] = []
        try:
            children = sorted(folder.iterdir())
        except OSError:
            return assets
        for child in children:
            if not child.is_dir():
                continue
            asset = self._read(child, category)
            if asset is not None:
                assets.append(asset)
        assets.sort(key=lambda a: (a.created_at, a.asset_id))
        return assets

    def load(self, category: str, asset_id: str) -> ComicAsset | None:
        return self._read(self.root / _safe_name(category) / _safe_name(asset_id), category)

    def _read(self, folder: Path, category: str) -> ComicAsset | None:
        try:
            payload = json.loads((folder / ASSET_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(payload, Mapping):
            return None
        asset_id = str(payload.get("asset_id") or folder.name)
        thumb: bytes | None = None
        thumb_path = folder / THUMB_FILE
        if thumb_path.is_file():
            try:
                thumb = thumb_path.read_bytes()
            except OSError:
                thumb = None
        return ComicAsset(
            asset_id=asset_id,
            category=str(payload.get("category") or category),
            name=str(payload.get("name") or asset_id),
            created_at=float(payload.get("created_at") or 0.0),
            record_meta=dict(payload.get("record") or {}),
            thumbnail_png=thumb,
            extra=dict(payload.get("extra") or {}),
        )

    # -- writing ------------------------------------------------------
    def save(self, asset: ComicAsset) -> Path:
        folder = self.root / _safe_name(asset.category) / _safe_name(asset.asset_id)
        folder.mkdir(parents=True, exist_ok=True)
        payload = {
            "schema_version": 1,
            "asset_id": asset.asset_id,
            "category": asset.category,
            "name": asset.name,
            "created_at": float(asset.created_at),
            "record": dict(asset.record_meta),
            "extra": dict(asset.extra),
        }
        tmp = folder / (ASSET_FILE + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(folder / ASSET_FILE)
        if asset.thumbnail_png:
            tmp_thumb = folder / (THUMB_FILE + ".tmp")
            tmp_thumb.write_bytes(asset.thumbnail_png)
            tmp_thumb.replace(folder / THUMB_FILE)
        return folder

    def rename(self, category: str, asset_id: str, name: str) -> bool:
        asset = self.load(category, asset_id)
        if asset is None:
            return False
        asset.name = str(name)
        self.save(asset)
        return True

    def delete(self, category: str, asset_id: str) -> bool:
        folder = self.root / _safe_name(category) / _safe_name(asset_id)
        if not folder.is_dir():
            return False
        for child in folder.iterdir():
            try:
                child.unlink()
            except OSError:
                logger.debug("asset file removal failed: %s", child, exc_info=True)
        try:
            folder.rmdir()
        except OSError:
            return False
        return True


def _safe_name(value: str) -> str:
    text = "".join(ch if (ch.isalnum() or ch in "-_") else "_" for ch in str(value))
    return text or "_"


# ── balloons ───────────────────────────────────────────────────────────────
def new_asset_id() -> str:
    return f"a{uuid.uuid4().hex[:10]}"


def record_bounds(record: BalloonRecord) -> tuple[float, float, float, float]:
    """Page-space bbox of body + tail (+ the nominal tip)."""

    x0, y0, w, h = record.body_rect
    x1, y1 = x0 + w, y0 + h
    if record.tail_rect is not None:
        tx, ty, tw, th = record.tail_rect
        x0, y0, x1, y1 = min(x0, tx), min(y0, ty), max(x1, tx + tw), max(y1, ty + th)
    if record.tail.enabled:
        px, py = record.tail.target
        x0, y0, x1, y1 = min(x0, px), min(y0, py), max(x1, px), max(y1, py)
    return (x0, y0, max(1.0, x1 - x0), max(1.0, y1 - y0))


def balloon_thumbnail_png(record: BalloonRecord, *, size_px: int = THUMB_PX) -> bytes | None:
    """Thumbnail of the balloon as rendered (raster records: the composed
    part image on a virtual canvas around the balloon; others: the vector
    outline). None when nothing could be rendered."""

    from PySide6.QtCore import QBuffer, QIODevice, Qt  # noqa: PLC0415
    from PySide6.QtGui import QImage  # noqa: PLC0415

    image = _render_record_image(record)
    if image is None or image.isNull():
        return None
    thumb = image.scaled(
        size_px,
        size_px,
        Qt.AspectRatioMode.KeepAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    canvas = QImage(size_px, size_px, QImage.Format.Format_ARGB32_Premultiplied)
    canvas.fill(Qt.GlobalColor.transparent)
    from PySide6.QtGui import QPainter  # noqa: PLC0415

    painter = QPainter(canvas)
    painter.drawImage((size_px - thumb.width()) // 2, (size_px - thumb.height()) // 2, thumb)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    canvas.save(buffer, "PNG")
    return bytes(buffer.data())


def _render_record_image(record: BalloonRecord) -> Any | None:
    from .raster_parts import compose_part_layer_image, is_raster_preserve  # noqa: PLC0415

    pad = 12.0
    bx, by, bw, bh = record_bounds(record)
    local = move_balloon_record(record, pad - bx, pad - by)
    width, height = int(bw + 2 * pad), int(bh + 2 * pad)
    if is_raster_preserve(local):
        try:
            return compose_part_layer_image(local, width, height)
        except Exception:
            logger.debug("asset thumbnail compose failed", exc_info=True)
            return None
    # parametric / hand-drawn: paint the vector object outline
    try:
        from PySide6.QtCore import Qt  # noqa: PLC0415
        from PySide6.QtGui import QImage, QPainter  # noqa: PLC0415

        from .balloon_objects import build_balloon_object  # noqa: PLC0415

        obj = build_balloon_object(local)
        image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        if obj is None:
            return image
        from PySide6.QtCore import QPointF  # noqa: PLC0415
        from PySide6.QtGui import QColor, QPainterPath, QPen  # noqa: PLC0415

        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        pen = QPen(QColor(0, 0, 0, 255))
        pen.setWidthF(2.0)
        painter.setPen(pen)
        painter.setBrush(QColor(255, 255, 255, 255))
        for path in getattr(obj, "paths", None) or []:
            anchors = [getattr(a, "pos", None) for a in getattr(path, "anchors", None) or []]
            points = [QPointF(float(pt[0]), float(pt[1])) for pt in anchors if pt is not None]
            if len(points) < 2:
                continue
            qpath = QPainterPath(points[0])
            for pt in points[1:]:
                qpath.lineTo(pt)
            qpath.closeSubpath()
            painter.drawPath(qpath)
        painter.end()
        return image
    except Exception:
        logger.debug("asset thumbnail vector render failed", exc_info=True)
        return None


def build_balloon_asset(
    record: BalloonRecord, *, name: str = "", timestamp: float | None = None
) -> ComicAsset:
    """Asset payload of a balloon: the record normalised to its own bbox
    origin (so a drop places it by its centre), document links cleared."""

    bx, by, _bw, _bh = record_bounds(record)
    normalised = replace(
        move_balloon_record(record, -bx, -by),
        panel_id="",
        speaker_id="",
        speaker_name="",
        text_object_id="",
    )
    asset_id = new_asset_id()
    return ComicAsset(
        asset_id=asset_id,
        category=CATEGORY_BALLOON,
        name=name or f"balloon {asset_id[-4:]}",
        created_at=float(timestamp if timestamp is not None else time.time()),
        record_meta=normalised.to_meta(),
        thumbnail_png=balloon_thumbnail_png(record),
        extra={"source_balloon_id": record.balloon_id},
    )


def place_balloon_record(
    asset: ComicAsset, center: tuple[float, float], *, balloon_id: str
) -> BalloonRecord | None:
    """The asset's record re-identified and moved so its bbox centre sits at
    ``center`` (page px)."""

    record = asset.record()
    if record is None:
        return None
    bx, by, bw, bh = record_bounds(record)
    moved = move_balloon_record(record, center[0] - (bx + bw * 0.5), center[1] - (by + bh * 0.5))
    return replace(
        moved,
        balloon_id=balloon_id,
        text_object_id=f"comic-text-{balloon_id}",
        panel_id="",
        speaker_id="",
        speaker_name="",
    )


def drop_payload(category: str, asset_id: str) -> str:
    return json.dumps(
        {
            "v": ASSET_PAYLOAD_VERSION,
            "plugin_id": "comic_tools",
            "category": str(category),
            "asset_id": str(asset_id),
        }
    )


def parse_drop_payload(payload_json: str) -> tuple[str, str] | None:
    try:
        payload = json.loads(payload_json)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, Mapping) or payload.get("v") != ASSET_PAYLOAD_VERSION:
        return None
    category = str(payload.get("category") or "")
    asset_id = str(payload.get("asset_id") or "")
    if not category or not asset_id:
        return None
    return category, asset_id


__all__ = [
    "ASSET_MIME",
    "CATEGORY_BALLOON",
    "CATEGORY_LABELS",
    "ComicAsset",
    "ComicAssetStore",
    "balloon_thumbnail_png",
    "build_balloon_asset",
    "default_assets_root",
    "drop_payload",
    "parse_drop_payload",
    "place_balloon_record",
    "record_bounds",
]
