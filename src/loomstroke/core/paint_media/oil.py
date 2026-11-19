"""Directional bristle bundles with separate pigment loads for oil paint."""

from __future__ import annotations

import math
import weakref

import numpy as np

from .alpha import deposit
from .paper import sample_tooth


def _density(bgr):
    # Three-channel absorption approximation in stored colour space, with finite black density.
    return -np.log((bgr + 16.0) / 271.0)


# As the brush runs out (1 - load): how far up the surface tooth paint stops reaching, how
# soft that limit is, and how much the gaps between tufts deepen.
_DRY_BRUSH = 1.1
_DRY_EDGE = 0.3
_STREAK = 0.6


def _reflectance(density):
    return np.clip(np.exp(-density) * 271.0 - 16.0, 0.0, 255.0)


class OilBrush:
    """Carry local underpaint in several tufts and let the bristle pattern bend along a stroke."""

    _BUNDLES = 5
    _SAMPLES_PER_PIXEL = 8

    def __init__(self, stroke):
        self.stroke = weakref.proxy(stroke)
        self.radius = max(1, stroke.radius)
        radial = np.hypot(stroke.x, stroke.y) / self.radius
        self.footprint = np.clip((1.0 - radial) * 8, 0, 1)
        if stroke.tip_shaped:
            # The tip image, not a disc, bounds the bristles (see the contact mask).
            self.footprint = (np.maximum(np.abs(stroke.x), np.abs(stroke.y)) <= self.radius).astype(
                self.footprint.dtype
            )
        self.upright_footprint = self.footprint
        self.step = max(1, self.radius // 12)
        self.length = 0.0
        self.direction = (1.0, 0.0)
        self.forward = 1.0
        self.tip_positions = np.linspace(
            -self.radius - 3,
            self.radius + 3,
            (2 * self.radius + 6) * self._SAMPLES_PER_PIXEL + 1,
            dtype=np.float32,
        )
        self.bristles = self._bristle_profile()
        pigment = 2 * stroke.paint_load
        self.bristles *= math.sqrt(pigment)
        # A well-loaded brush travels farther and its original paint replenishes the tip;
        # a nearly empty one takes on the colour of the underpainting sooner.
        # The stroke's wear settings scale these; half way leaves each as it is, and the
        # top of the reach never runs out.
        reach, pickup, recovery = stroke.wear
        self.depletion_distance = (
            math.inf
            if reach >= 1
            else (75 * self.radius) * (0.3 + 0.7 * pigment) * 4.0 ** (2 * reach - 1)
        )
        self.replenishment = 0.32 * pigment * (2 * recovery)
        if stroke.opacity != 1.0:
            self.bristles *= stroke.opacity
        self.ink = _density(stroke.ink)
        self._ink_source = stroke.ink
        self.loads = np.tile(self.ink, (self._BUNDLES, 1))
        self.uptake = np.array([0.88, 1.2, 0.72, 1.08, 0.80], dtype=np.float32)
        self.uptake *= (1.55 - 0.55 * pigment) * (2 * pickup)
        # Smooth colour transitions between neighbouring tufts of the brush.
        bundle = np.clip(
            (self.tip_positions / self.radius + 1) * 0.5 * self._BUNDLES - 0.5,
            0,
            self._BUNDLES - 1,
        )
        self.left = np.minimum(bundle.astype(np.intp), self._BUNDLES - 2)
        fraction = (bundle - self.left).astype(np.float32)[:, None]
        self.fade = fraction * fraction * (3 - 2 * fraction)

    def _bristle_profile(self):
        # Irregular gaps between tufts stay attached to the brush throughout the stroke.
        rng = np.random.default_rng(3701)
        pos = self.tip_positions
        profile = np.full_like(pos, 0.76)
        center = float(pos[0])
        while center < pos[-1]:
            width = rng.uniform(0.36, 1.05)
            depth = rng.uniform(0.30, 0.72)
            profile -= depth * np.exp(-0.5 * ((pos - center) / width) ** 2)
            center += rng.uniform(1.9, 6.1)
        return np.clip(profile, 0.08, 0.86)

    def _orientation(self, x, y):
        previous = self.stroke.previous
        self.stroke.previous = (x, y)
        if previous is None:
            return 0.0
        dx, dy = x - previous[0], y - previous[1]
        distance = math.hypot(dx, dy)
        if distance:
            dx, dy = dx / distance, dy / distance
            ux, uy = self.direction
            # The long axis of the tip is unchanged by reversing the stroke.
            if ux * dx + uy * dy < 0:
                self.forward = -1.0
                dx, dy = -dx, -dy
            else:
                self.forward = 1.0
            follow = 1 - math.exp(-distance / max(1.0, self.radius * 0.28))
            ux, uy = ux + follow * (dx - ux), uy + follow * (dy - uy)
            norm = math.hypot(ux, uy)
            self.direction = (ux / norm, uy / norm)
        return distance

    def _contact_sums(self, pieces, across, along):
        # Sample the same brush-local lattice on either side of a tile boundary. Accumulating
        # premultiplied BGRA before division excludes unpainted pixels from the picked colour.
        sums = np.zeros((self._BUNDLES, 5), dtype=np.float64)
        for tile, sector, mask in pieces:
            oy, ox = -mask[0].start % self.step, -mask[1].start % self.step
            sample = np.s_[oy :: self.step, ox :: self.step]
            # The leading edge encounters fresh underpaint before that tuft lays its new coat.
            leading = self.forward * along[mask][sample] / self.radius
            contact = (self.footprint[mask][sample] * np.clip(0.6 + 1.6 * leading, 0.12, 2)).ravel()
            pixels = tile.pixels[sector][sample].reshape(-1, 4)
            position = across[mask][sample].ravel() / self.radius
            bundle = np.clip(
                ((position + 1) * (0.5 * self._BUNDLES)).astype(int), 0, self._BUNDLES - 1
            )
            sums[:, 4] += np.bincount(bundle, weights=contact, minlength=self._BUNDLES)
            for channel in range(4):
                sums[:, channel] += np.bincount(
                    bundle, weights=contact * pixels[:, channel], minlength=self._BUNDLES
                )
        return sums

    def _pick_up(self, pieces, across, along, distance):
        sums = self._contact_sums(pieces, across, along)
        alpha = sums[:, 3:4]
        covered = alpha / np.maximum(1, sums[:, 4:5] * 255)
        picked = (255 * sums[:, :3] / np.maximum(alpha, 1e-9)).astype(np.float32)
        contact_rate = min(0.34, 0.06 + 0.52 * distance / self.radius)
        # A tuft can at most take on the colour below completely.
        uptake = np.minimum(1.0, contact_rate * self.uptake[:, None] * covered)
        self.loads += uptake * (_density(picked) - self.loads)
        self.loads += min(1.0, self.replenishment * contact_rate) * (self.ink - self.loads)

    def dab(self, x, y, pieces):
        ink = self.stroke.dab_ink
        if ink is not self._ink_source:
            # A dab with its own colour reloads every tuft by the same shift, keeping
            # whatever underpaint each tuft has already picked up.
            target = _density(ink)
            self.loads += target - self.ink
            self.ink, self._ink_source = target, ink
        self.footprint = self.stroke.contact.apply(self.upright_footprint)
        distance = self._orientation(x, y)
        self.length += distance
        self.stroke.load = max(0.18, math.exp(-self.length / self.depletion_distance))
        ux, uy = self.direction
        across = self.stroke.y * ux - self.stroke.x * uy
        along = self.stroke.x * ux + self.stroke.y * uy
        self._pick_up(pieces, across, along, distance)
        density = self.loads[self.left] + self.fade * (
            self.loads[self.left + 1] - self.loads[self.left]
        )
        palette = _reflectance(density)
        along = self.length + self.forward * along
        # Continuous bending gives every tuft a slightly different path on a curved stroke.
        bent = across + 0.74 * np.sin(0.057 * along + 0.22 * across)
        index = np.clip(
            ((bent - self.tip_positions[0]) * self._SAMPLES_PER_PIXEL + 0.5).astype(np.intp),
            0,
            len(self.bristles) - 1,
        )
        self._lay_down(pieces, index, palette)

    def _dry_bristles(self):
        """The bristle profile for the current load: a drying brush opens its gaps."""
        dry = 1.0 - self.stroke.load
        if dry <= 0.0:
            return self.bristles
        full = self.bristles.max()
        return np.clip(self.bristles - (_STREAK * dry) * (full - self.bristles), 0, None).astype(
            np.float32
        )

    def _catching(self, footprint, tile, sector):
        """Where a drying brush still leaves paint: on the tooth of the surface first."""
        dry = 1.0 - self.stroke.load
        if dry <= 0.0:
            return footprint
        if tile.tooth is None:
            layer = self.stroke.layer
            sheet = tile.rect.translated(int(layer.offset_x), int(layer.offset_y))
            # Kept in units of the soft edge, so each dab only adds its dryness and clips.
            tile.tooth = sample_tooth(sheet, self.stroke.paper, tile.grain) / np.float32(_DRY_EDGE)
        reach = np.float32(1.0 - _DRY_BRUSH * dry / _DRY_EDGE)
        caught = tile.tooth[sector] + reach
        np.clip(caught, 0.0, 1.0, out=caught)
        caught *= footprint
        return caught

    def _lay_down(self, pieces, index, palette):
        bristles = self._dry_bristles()
        load = self.stroke.load
        for tile, sector, mask in pieces:
            grain = tile.grain[sector]
            coverage = self._catching(self.footprint[mask], tile, sector) * bristles[index[mask]]
            amount = coverage * (0.6 + 0.4 * load) * (0.72 + 0.28 * grain)
            deposit(tile.pixels[sector], palette[index[mask]], amount)
