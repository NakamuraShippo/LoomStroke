"""Tile-local watercolor and oil strokes on premultiplied raster layers."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np
from PySide6.QtCore import QRect

from loomstroke.core.channels.channel_pixel_policy import ChannelPixelPolicy
from loomstroke.core.paper_texture_mask import PaperTextureMask
from loomstroke.core.raster_stroke_command import RasterStrokeCommand

from .alpha import restore_coverage
from .kneaded import KneadedEraserBrush
from .oil import OilBrush
from .paper import WATER_MOBILITY, sample_grain
from .pen_contact import PenContact
from .watercolor import WatercolorBrush, clear_wet_state

TILE = 128
INK = np.array([46, 70, 197], dtype=np.float32)  # BGR
# How a brush changes along a stroke, each value in 0..1. Watercolor: paint reach, paint
# left at the end, lifting of wet paint. Oil: paint reach, pick-up of the colour below,
# return to the brush's own colour.
# A kneaded eraser uses the first value only: how much paint the lump holds.
DEFAULT_WEAR = {"water": (0.5, 0.2, 0.5), "oil": (0.5, 0.5, 0.5), "knead": (0.5, 0.5, 0.5)}


@dataclass
class WorkTile:
    rect: QRect
    pixels: np.ndarray
    grain: np.ndarray
    water: np.ndarray | None
    # Editable share of each pixel in 0..255; None when the whole tile is editable.
    coverage: np.ndarray | None = None
    texture: np.ndarray | None = None
    blocked: bool = False
    # Oil: the relief of the surface, sampled when a drying brush first needs it.
    tooth: np.ndarray | None = None
    # Watercolor: the pixels before this stroke, which lifted paint returns to.
    source: np.ndarray | None = None


def _limit_to_coverage(tile, sector, before, water_before):
    """Keep the dab only where the tile is editable; soft edges mix in proportion."""
    amount = tile.coverage[sector].astype(np.uint32)[:, :, None]
    pixels = tile.pixels[sector]
    pixels[:] = (before * (255 - amount) + pixels * amount + 127) // 255
    if water_before is not None:
        water = tile.water[sector]
        water[:] = water_before + (water - water_before) * (
            tile.coverage[sector].astype(np.float32) / 255.0
        )


def _pooled_edges(shape, radius):
    """Let a wash gather just inside the edges of a tip image, as it does on a round brush."""
    inside = (shape > 0.25).astype(np.uint8)
    depth = cv2.distanceTransform(inside, cv2.DIST_L2, 3)
    rim = np.exp(-(((depth - radius * 0.17) / max(1.5, radius * 0.18)) ** 2))
    pool = np.exp(-(((depth - radius * 0.05) / max(0.7, radius * 0.053)) ** 2))
    # Same proportions as the round wash: body 0.122, rim 0.047, pool 0.16.
    return (shape * (1.0 + (0.047 / 0.122) * rim + (0.16 / 0.122) * pool * inside)).astype(
        np.float32
    )


class MediaStroke:
    """Keep dab state and candidate pixels separate from the unmodified source layer."""

    def __init__(
        self,
        layer,
        medium,
        diameter,
        *,
        color_bgr=None,
        opacity=1.0,
        paper="Standard",
        water_amount=0.5,
        paint_load=0.5,
        tip=None,
        wear=None,
    ):
        if medium not in DEFAULT_WEAR or diameter < 1:
            raise ValueError("Expected a known medium and a positive brush diameter")
        if not 0.0 <= opacity <= 1.0:
            raise ValueError("Paint opacity must be in 0..1")
        if paper not in WATER_MOBILITY:
            raise ValueError("Unknown paint paper")
        if not (0 <= water_amount <= 1 and 0 <= paint_load <= 1):
            raise ValueError("Water amount and paint load must be in 0..1")
        self.layer = layer
        self.paper = paper
        self.water_amount = water_amount
        # Watercolor: how far the paint reaches, what is left of it at the end, and how
        # strongly the thin brush lifts wet paint; each in 0..1.
        self.wear = DEFAULT_WEAR[medium] if wear is None else tuple(map(float, wear))
        if len(self.wear) != 3 or not all(0 <= value <= 1 for value in self.wear):
            raise ValueError("Brush wear needs three values in 0..1")
        self.paint_load = paint_load
        self.opacity = opacity
        self.medium = medium
        self._source_version = layer.texture_version
        self._clip_rect = layer.image.rect()
        self.radius = max(1, diameter // 2)
        pad = self.radius + (5 if medium == "water" else 0)
        y, x = np.mgrid[-pad:pad, -pad:pad].astype(np.float32) + 0.5
        self.x, self.y = x, y
        self.contact = PenContact(x, y, pad)
        # With a tip image the brush touches the paper in that image's shape, not in
        # a disc: the brushes start from a full square and the tip carves it.
        self.tip_shaped = bool(tip and tip[0])
        self.tiles = {}
        self.ink = np.array(INK if color_bgr is None else color_bgr, dtype=np.float32)
        if self.ink.shape != (3,) or not np.all((self.ink >= 0) & (self.ink <= 255)):
            raise ValueError("Brush colour must contain three BGR components in 0..255")
        # Colour of the current dab; the stroke's ink unless the dab brings its own.
        self.dab_ink = self.ink
        self.load = 1.0
        self.previous = None
        self.pad = pad
        self.brush = self._create_brush(medium)
        if tip is not None:
            self.set_tip(*tip)
        self._coverage = None
        # Optional pen settings: canvas-anchored grain and pick-up of the paint below.
        self._texture = None
        self._mixing = 0.0
        self._pickup = None
        self._grain = None
        # Symmetry copies paint into the same tiles, each with its own brush motion.
        self._lanes = {0: self.brush}
        self._lane = 0
        self._lane_motion = {}

    def _create_brush(self, medium):
        if medium == "knead":
            return KneadedEraserBrush(self)
        return WatercolorBrush(self) if medium == "water" else OilBrush(self)

    def set_clip_rect(self, rect: QRect) -> None:
        """Limit paint and mobile pigment to a layer-local rectangle before sampling."""
        self._clip_rect = self.layer.image.rect().intersected(rect)

    def set_tip(self, tip_path: str, hardness: float, hardness_mode: str) -> None:
        """Shape every dab with the pen's edge hardness and optional tip image."""
        if not tip_path and hardness >= 1.0:
            return
        from loomstroke.core.commands_stroke import _load_tip_mask  # noqa: PLC0415

        size = 2 * self.radius
        shape = np.zeros((2 * self.pad, 2 * self.pad), dtype=np.float32)
        edge = self.pad - self.radius
        shape[edge : edge + size, edge : edge + size] = _load_tip_mask(
            tip_path, size, hardness, hardness_mode
        )
        if self.tip_shaped and self.medium == "water":
            shape = _pooled_edges(shape, self.radius)
        self.contact.set_shape(shape, rotates=bool(tip_path))

    def set_surface(self, strength: float, scale: float, seed: int, mixing: float) -> None:
        """Apply the pen's paper texture and its mixing with the colour already on the layer."""
        self._texture = (strength, scale, seed) if strength > 0 else None
        self._mixing = min(1.0, max(0.0, mixing))

    def _mixed_ink(self, pieces):
        # Same rule as the raster pen: the dab takes up the covered colour under it.
        # A sparse lattice is enough for an average and keeps large brushes fast.
        if self._pickup is None:
            self._pickup = np.clip(self.radius + 0.5 - np.hypot(self.x, self.y), 0, 1)
        step = max(1, self.radius // 8)
        colour, covered = np.zeros(3, dtype=np.float64), 0.0
        for tile, sector, mask in pieces:
            lattice = np.s_[-mask[0].start % step :: step, -mask[1].start % step :: step]
            weight = self._pickup[mask][lattice]
            pixels = tile.pixels[sector][lattice]
            covered += float((pixels[..., 3] * weight).sum())
            colour += (pixels[..., :3] * weight[..., None]).sum(axis=(0, 1))
        if covered <= 1e-3:
            return self.dab_ink
        below = np.clip(colour * (255.0 / covered), 0, 255)
        return (self.dab_ink * (1 - self._mixing) + below * self._mixing).astype(np.float32)

    def _apply_texture(self, pieces):
        strength, scale, seed = self._texture
        if self._grain is None:
            self._grain = np.ones((2 * self.pad, 2 * self.pad), dtype=np.float32)
        grain = self._grain
        grain.fill(1.0)
        for tile, sector, mask in pieces:
            if tile.texture is None:
                # The grain is fixed to the sheet, so each tile needs it only once.
                rect = tile.rect
                tile.texture = PaperTextureMask.sample(
                    rect.x() + int(self.layer.offset_x),
                    rect.y() + int(self.layer.offset_y),
                    rect.width(),
                    rect.height(),
                    scale=scale,
                    strength=strength,
                    seed=seed,
                )
            grain[mask] = tile.texture[sector]
        mask = self.contact.mask
        self.contact.mask = grain if mask is None else mask * grain

    def set_coverage(self, sample) -> None:
        """Limit edits by sample(layer_rect) -> uint8 coverage, e.g. a selection or a mask."""
        self._coverage = sample

    def _tile(self, tx, ty):
        key = (tx, ty)
        if key not in self.tiles:
            rect = QRect(tx * TILE, ty * TILE, TILE, TILE).intersected(self.layer.image.rect())
            pixels = ChannelPixelPolicy.pixels(self.layer.image.copy(rect))
            sheet_rect = rect.translated(int(self.layer.offset_x), int(self.layer.offset_y))
            grain = sample_grain(sheet_rect, self.medium, self.paper)
            water = np.zeros_like(grain) if self.medium == "water" else None
            tile = self.tiles[key] = WorkTile(rect, pixels, grain, water)
            if water is not None:
                tile.source = pixels.copy()
            if self._coverage is not None and not rect.isEmpty():
                coverage = self._coverage(rect)
                if coverage.min() < 255:
                    tile.coverage, tile.blocked = coverage, not coverage.any()
        return self.tiles[key]

    def _pieces(self, x, y):
        # Plain integers: this runs for every dab and every tile under it.
        x0, y0 = x - self.pad, y - self.pad
        cx, cy, cw, ch = self._clip_rect.getRect()
        left, top = max(x0, cx), max(y0, cy)
        right, bottom = min(x0 + 2 * self.pad, cx + cw), min(y0 + 2 * self.pad, cy + ch)
        if left >= right or top >= bottom:
            return
        tiles = self.tiles
        for ty in range(top // TILE, (bottom - 1) // TILE + 1):
            y1, y2 = max(top, ty * TILE), min(bottom, (ty + 1) * TILE)
            for tx in range(left // TILE, (right - 1) // TILE + 1):
                tile = tiles.get((tx, ty)) or self._tile(tx, ty)
                if tile.blocked:
                    continue
                x1, x2 = max(left, tx * TILE), min(right, (tx + 1) * TILE)
                yield (
                    tile,
                    (slice(y1 - ty * TILE, y2 - ty * TILE), slice(x1 - tx * TILE, x2 - tx * TILE)),
                    (slice(y1 - y0, y2 - y0), slice(x1 - x0, x2 - x0)),
                )

    def _enter_lane(self, lane):
        if lane != self._lane:
            self._lane_motion[self._lane] = self.previous, self.load
            self.previous, self.load = self._lane_motion.get(lane, (None, 1.0))
            self._lane = lane
        brush = self._lanes.get(lane)
        if brush is None:
            brush = self._lanes[lane] = self._create_brush(self.medium)
            if self.medium == "water":
                # One wet sheet: every copy feeds and reads the same water and pigment.
                brush.working = self.brush.working
        return brush

    def dab(  # noqa: PLR0913 - one dab is one flat input record
        self,
        x,
        y,
        pressure=1.0,
        tilt_x=0.0,
        tilt_y=0.0,
        lane=0,
        density=None,
        angle=0.0,
        color_bgr=None,
    ):
        if pressure <= 0 or self.paint_load == 0 or (density is not None and density <= 0):
            return set()
        if self.medium == "knead" and self.layer.alpha_lock:
            return set()  # Locked transparency leaves an eraser nothing to take.
        brush = self._enter_lane(lane)
        self.dab_ink = self.ink if color_bgr is None else np.array(color_bgr, dtype=np.float32)
        self.contact.update(min(pressure, 1.0), tilt_x, tilt_y, density, angle)
        pieces = list(self._pieces(x, y))
        if self._mixing:
            self.dab_ink = self._mixed_ink(pieces)
        contact = self.contact.mask
        if self._texture is not None:
            self._apply_texture(pieces)
        partial = [
            (
                tile,
                sector,
                tile.pixels[sector].copy(),
                None if tile.water is None else tile.water[sector].copy(),
            )
            for tile, sector, _ in pieces
            if tile.coverage is not None
        ]
        if self.layer.alpha_lock:
            saved = [tile.pixels[sector][..., 3].copy() for tile, sector, _ in pieces]
            brush.dab(x, y, pieces)
            for (tile, sector, _), alpha in zip(pieces, saved, strict=True):
                restore_coverage(tile.pixels[sector], alpha)
        else:
            brush.dab(x, y, pieces)
        self.contact.mask = contact  # The cached mask stays free of this dab's grain.
        for tile, sector, pixels, water in partial:
            _limit_to_coverage(tile, sector, pixels, water)
        return {(tile.rect.x(), tile.rect.y()) for tile, _, _ in pieces}

    def work_bytes(self):
        tile_bytes = sum(
            t.pixels.nbytes + t.grain.nbytes + (0 if t.water is None else t.water.nbytes)
            for t in self.tiles.values()
        )
        if self.medium == "water":
            tile_bytes += self.brush.working_pigment_bytes()
        return tile_bytes

    def prepare_command(self) -> RasterStrokeCommand | None:
        """Collect modified tiles while leaving the layer and its history untouched."""
        if self.layer.texture_version != self._source_version:
            raise RuntimeError("Target layer changed during the paint stroke")
        command = RasterStrokeCommand(self.layer)
        for tile in self.tiles.values():
            changes = command.prepare(self.layer, tile.rect, ChannelPixelPolicy.image(tile.pixels))
            if changes:
                command.tiles.update(changes)
                command.local_dirty = command.local_dirty.united(tile.rect)
        return command if command.tiles else None

    def commit_medium(self) -> None:
        """Publish the medium after the caller successfully commits the pixel command."""
        if self.medium == "water" and not self.layer.alpha_lock:
            self.brush.commit_wet_state()
        else:
            clear_wet_state(self.layer)
