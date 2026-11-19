"""Use Rust for the two per-pixel passes of the reference oil brush."""

import numpy as np

from .oil import OilBrush


class RustOilBrush(OilBrush):
    def __init__(self, stroke, native):
        super().__init__(stroke)
        self.native = native

    def _contact_sums(self, pieces, across, along):
        sums = np.zeros((self._BUNDLES, 5), dtype=np.float64)
        for tile, sector, mask in pieces:
            # Keep the sample lattice anchored to brush space across tile boundaries.
            origin = (-mask[0].start % self.step, -mask[1].start % self.step)
            self.native.oil_pickup(
                tile.pixels[sector],
                self.footprint[mask],
                across[mask],
                along[mask],
                self.radius,
                self.forward,
                self.step,
                origin,
                sums,
            )
        return sums

    def _lay_down(self, pieces, index, palette):
        load_factor = 0.6 + 0.4 * self.stroke.load
        # The dry-brush terms are prepared as in the reference; the kernel multiplies
        # them in the same order.
        bristles = self._dry_bristles()
        for tile, sector, mask in pieces:
            self.native.oil_deposit(
                tile.pixels[sector],
                tile.grain[sector],
                self._catching(self.footprint[mask], tile, sector),
                index[mask],
                bristles,
                palette,
                load_factor,
            )
