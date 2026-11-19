"""Transient water and mobile pigment on a versioned raster layer."""

from __future__ import annotations

import math
from dataclasses import dataclass
from weakref import proxy

import cv2
import numpy as np

from .alpha import deposit
from .paper import WATER_MOBILITY, sample_paper

__all__ = ["WatercolorBrush", "WetLayer", "WetTile", "clear_wet_state", "sample_paper"]

# Flank footprints kept per pen step; one is a brush-sized float32 grid.
_FLANK_CACHE = 64
# With the stroke's wear settings half way: a full brush paints this many radii before
# its paint has thinned to 1/e, and a thin brush takes back up to LIFT of the wet paint
# under each dab.
_REACH = 80.0
_LIFT = 0.1


@dataclass
class WetTile:
    water: np.ndarray
    pigment: np.ndarray  # BGR triplet for a single pigment, otherwise a tile of BGR triplets
    turn: int


@dataclass
class WetLayer:
    version: int
    image_key: int
    turn: int
    tiles: dict


def clear_wet_state(layer):
    """Discard the transient water reservoir without touching deposited pixels."""
    layer._paint_medium_wet_state = None


class WatercolorBrush:
    def __init__(self, stroke):
        self.stroke = proxy(stroke)
        layer = stroke.layer
        previous = getattr(layer, "_paint_medium_wet_state", None)
        if previous and (
            previous.version != layer.texture_version
            or previous.image_key != layer.image.cacheKey()
        ):
            previous = None
        self.previous = previous
        self.turn = previous.turn + 1 if previous else 0
        self.working = {}
        self.committed = False
        distance = np.hypot(stroke.x, stroke.y)
        radius = stroke.radius
        feather = max(1.5, radius * 0.22)
        body = np.clip((radius + 0.55 - distance) / feather, 0.0, 1.0)
        rim = np.exp(-(((distance - radius * 0.83) / max(1.5, radius * 0.18)) ** 2))
        self.coverage = body * (0.122 + 0.047 * rim)
        self.pool = 0.16 * np.exp(-(((distance - radius * 0.95) / max(0.7, radius * 0.053)) ** 2))
        if stroke.tip_shaped:
            # The tip image supplies the outline and its pooled edges through the
            # contact mask; start from an even square instead of a disc.
            body = (np.maximum(np.abs(stroke.x), np.abs(stroke.y)) <= radius).astype(np.float32)
            self.coverage = body * np.float32(0.122)
            self.pool = np.zeros_like(body)
        self.normal_x, self.normal_y = stroke.x / distance, stroke.y / distance
        self.last_position = None
        self.direction = None
        self._flanks = {}
        self.moisture = body * 0.43
        self.fringe = np.clip((radius + 4.5 - distance) / 4.5, 0, 1) * (1.0 - body)
        if stroke.tip_shaped:
            self.fringe = np.zeros_like(body)
        mobility = WATER_MOBILITY[stroke.paper]
        # The deposited pigment and the water left for the next wash have separate controls.
        # More solvent thins the central colour and carries wet pigment further at an edge.
        pigment = 2 * stroke.paint_load
        water = 2 * stroke.water_amount
        concentration = pigment / (0.65 + 0.35 * water)
        self.coverage *= concentration
        self.pool *= concentration
        # The reservoir tracks mobile pigment carried by water. A barely loaded
        # blue wash must not release as much blue into the next colour as a full one.
        self.moisture *= water * mobility * min(1, pigment)
        self.fringe *= min(1, water) * mobility * min(1, pigment)
        self.pigment_mobility = water
        self.diffusion_sigma = 3.1 * (0.45 + 0.55 * water) * mobility
        kernel = max(1, round(2.25 * self.diffusion_sigma)) * 2 + 1
        self.diffusion_kernel = (kernel, kernel)
        if stroke.opacity != 1.0:
            self.coverage *= stroke.opacity
            self.pool *= stroke.opacity
            self.moisture *= stroke.opacity
            self.fringe *= stroke.opacity
        self.footprint = self.coverage + self.pool
        self.reservoir = np.empty((*body.shape, 4), dtype=np.float32)
        self._load_brush(body, radius, pigment, water)

    def _load_brush(self, body, radius, pigment, water):
        # The brush gives up its paint along the stroke. Once it is thin it also takes
        # this stroke's wet paint back off the paper, so going over a wash again
        # lightens it; only a freshly loaded brush (the next stroke) darkens it.
        reach, self.floor, lifting = self.stroke.wear
        self.length = 0.0
        # Half way is the reference distance; the top of the range never runs out.
        self.reach = (
            math.inf
            if reach >= 1
            else _REACH * radius * (0.3 + 0.7 * pigment) * 4.0 ** (2 * reach - 1)
        )
        self.load = 1.0
        self.lift = np.ascontiguousarray(
            body * (_LIFT * 2 * lifting * min(1, water)), dtype=np.float32
        )

    def _prepare(self, tile):
        key = tile.rect.x(), tile.rect.y()
        if key not in self.working:
            previous = self.previous.tiles.get(key) if self.previous else None
            pigment = self.stroke.ink
            if previous and self.pigment_mobility and self.turn - previous.turn <= 6:
                tile.water[:] = previous.water * 0.76 ** (self.turn - previous.turn)
                if not np.array_equal(previous.pigment, pigment):
                    pigment = np.broadcast_to(previous.pigment, (*tile.pixels.shape[:2], 3)).copy()
            self.working[key] = WetTile(tile.water, pigment, self.turn)
        return self.working[key]

    def _diffuse(self, pieces, tiles):
        """Sample the wet reservoir across all touched tiles before writing any of them."""
        field = self.reservoir
        field.fill(0)
        self._fill_reservoir(field, pieces, tiles)
        # An isotropic capillary step is limited to the brush footprint plus its wet fringe.
        return cv2.GaussianBlur(
            field, self.diffusion_kernel, self.diffusion_sigma, borderType=cv2.BORDER_CONSTANT
        )

    def _fill_reservoir(self, field, pieces, tiles):
        for (tile, sector, mask), wet in zip(pieces, tiles, strict=True):
            water = tile.water[sector]
            pigment = wet.pigment[sector] if wet.pigment.ndim == 3 else wet.pigment
            field[mask][:, :, :3] = pigment * water[:, :, None]
            field[mask][:, :, 3] = water
        # Solvent in the brush releases more existing pigment into this capillary step.
        # Scale mass and transported colour together so the sampled hue stays valid.
        if self.pigment_mobility != 1:
            field *= self.pigment_mobility

    def dab(self, x, y, pieces):
        if self.committed:
            raise RuntimeError("Wet wash has already been committed")
        if self.last_position is not None:
            self.length += math.hypot(x - self.last_position[0], y - self.last_position[1])
            self.load = max(self.floor, math.exp(-self.length / self.reach))
        self._set_footprint(x, y)
        tiles = [self._prepare(tile) for tile, _, _ in pieces]
        mixed = any(wet.pigment.ndim == 3 for wet in tiles)
        field = self._diffuse(pieces, tiles) if mixed else None
        self._apply_pieces(pieces, tiles, field)

    def _apply_pieces(self, pieces, tiles, field):
        # A dab may carry its own colour. Unmixed wet tiles still record the stroke's ink,
        # so a varied stroke costs no more than a plain one.
        ink = self.stroke.dab_ink
        footprint, moisture, wet_fringe = self._contact_masks()
        load, thirst = np.float32(self.load), np.float32(1.0 - self.load)
        lift = self.stroke.contact.apply(self.lift)
        for (tile, sector, mask), wet in zip(pieces, tiles, strict=True):
            water = tile.water[sector]
            grain = tile.grain[sector]
            amount = footprint[mask] * (0.38 + 0.62 * grain) * load
            fresh = moisture[mask]
            color = ink
            if field is not None:
                local = field[mask]
                mobile = local[:, :, 3]
                # Wet pigment colours the newly fed area. A dry ground never enters this field.
                old_fraction = 0.92 * mobile / (0.12 + mobile)
                carried = local[:, :, :3] - ink * mobile[:, :, None]
                color = ink + carried * (0.92 / (0.12 + mobile))[:, :, None]
                foreign = np.max(np.abs(carried), axis=2) / 255.0
                fringe = wet_fringe[mask] * foreign * (0.6 + 0.4 * grain)
                amount += fringe * 0.30
                fresh = fresh + fringe * 0.28
                if wet.pigment.ndim == 1:
                    wet.pigment = np.broadcast_to(
                        self.stroke.ink, (*tile.pixels.shape[:2], 3)
                    ).copy()
                existing = wet.pigment[sector]
                share = fresh / np.maximum(1e-5, water * (1.0 - fresh) + fresh)
                # Retain local pigment while exchanging some colour with its wetter neighbours.
                exchange = old_fraction * fresh
                existing[:] += (color - existing) * (share + (1.0 - share) * exchange)[:, :, None]
            if thirst > 0:
                # Wet paint of this stroke returns towards the pixels it was laid on.
                taken = np.clip(lift[mask] * thirst * water, 0, 1)[:, :, None]
                pixels = tile.pixels[sector]
                laid = pixels.astype(np.float32)
                pixels[:] = np.rint(laid + (tile.source[sector] - laid) * taken)
            deposit(tile.pixels[sector], color, amount)
            water[:] += (1.0 - water) * fresh

    def _contact_masks(self):
        contact = self.stroke.contact
        if contact.mask is None:
            return self.footprint, self.moisture, self.fringe
        return (
            contact.apply(self.footprint),
            contact.apply(self.moisture),
            contact.apply(self.fringe),
        )

    def _set_footprint(self, x, y):
        if self.last_position is not None:
            dx, dy = x - self.last_position[0], y - self.last_position[1]
            if (dx or dy) and (dx, dy) != self.direction:
                # Dabs sit on whole pixels, so a stroke keeps returning to a few steps.
                footprint = self._flanks.get((dx, dy))
                if footprint is None:
                    if len(self._flanks) >= _FLANK_CACHE:
                        self._flanks.clear()
                    footprint = self._flanks[dx, dy] = self._flank_footprint(dx, dy)
                self.footprint = footprint
                self.direction = dx, dy
        self.last_position = x, y

    def _flank_footprint(self, dx, dy):
        # On a moving wash, pigment pools on the two flanks instead of ringing each dab.
        across = (dy * self.normal_x - dx * self.normal_y) / np.hypot(dx, dy)
        return self.coverage + self.pool * across.astype(np.float32) ** 4

    def working_pigment_bytes(self):
        return sum(tile.pigment.nbytes for tile in self.working.values() if tile.pigment.ndim == 3)

    def commit_wet_state(self):
        """Publish only after the pixel command and its history entry have committed."""
        layer = self.stroke.layer
        retained = (
            {k: v for k, v in self.previous.tiles.items() if self.turn - v.turn < 6}
            if self.previous
            else {}
        )
        if self.pigment_mobility:
            retained.update(self.working)
        layer._paint_medium_wet_state = (
            WetLayer(layer.texture_version, layer.image.cacheKey(), self.turn, retained)
            if retained
            else None
        )
        self.committed = True
