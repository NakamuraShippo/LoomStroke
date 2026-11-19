"""Feed the same sampled water and floating pigment to the Rust tile kernel."""

import numpy as np

from .watercolor import WatercolorBrush


def _region(sector, mask):
    ty, tx = sector
    my, mx = mask
    return tx.start, ty.start, mx.start, my.start, tx.stop - tx.start, ty.stop - ty.start


class RustWatercolorBrush(WatercolorBrush):
    def __init__(self, stroke, native):
        super().__init__(stroke)
        self._kernel = native.apply_water_dab
        self._sample = native.fill_reservoir
        self._flank = native.flank_footprint
        self._ink_bgr = tuple(map(float, stroke.ink))

    def _fill_reservoir(self, field, pieces, tiles):
        for (tile, sector, mask), wet in zip(pieces, tiles, strict=True):
            self._sample(
                field,
                tile.water,
                wet.pigment if wet.pigment.ndim == 3 else None,
                self._ink_bgr,
                _region(sector, mask),
                self.pigment_mobility,
            )

    def _flank_footprint(self, dx, dy):
        footprint = np.empty_like(self.coverage)  # The caller keeps one per pen step.
        self._flank(footprint, self.coverage, self.pool, self.normal_x, self.normal_y, dx, dy)
        return footprint

    def _apply_pieces(self, pieces, tiles, field):
        regions = []
        for (tile, sector, mask), wet in zip(pieces, tiles, strict=True):
            if field is not None and wet.pigment.ndim == 1:
                wet.pigment = np.broadcast_to(self.stroke.ink, (*tile.water.shape, 3)).copy()
            pigment = wet.pigment if field is not None else None
            regions.append(
                (tile.pixels, tile.water, tile.grain, _region(sector, mask), pigment, tile.source)
            )
        stroke = self.stroke
        ink = self._ink_bgr if stroke.dab_ink is stroke.ink else tuple(map(float, stroke.dab_ink))
        # One call per dab lets the rows of all touched tiles share the worker threads.
        self._kernel(
            regions,
            self.footprint,
            self.moisture,
            self.fringe,
            ink,
            field,
            self.stroke.contact.mask,
            # The kernel scales the laid paint and lifts wet paint, as the reference does.
            (self.lift, self.load, 1.0 - self.load),
        )
