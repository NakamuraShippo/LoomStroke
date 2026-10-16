"""First-touch preimage capture for deferred transaction strokes (RJS P0-05).

During a stroke transaction, per-segment history recording is deferred and a
single ``_HistoryPatch`` is built when the transaction flushes.  The legacy
implementation snapshotted the whole layer image on the first deferred
segment (``layer.image.copy()`` — ~256 MB for an 8K layer).  This service
replaces that snapshot with an explicit capture policy chosen when a capture
begins:

- ``RASTER_REGION`` (normal raster strokes): before each segment mutates
  pixels, only the not-yet-covered part of the segment's image-local rect is
  copied.  Fragments are non-overlapping, so the first-touch value of every
  pixel is kept and never re-captured or overwritten.  Captured bytes scale
  with the edited region, not the layer.
- ``MASK_DENSE`` (mask edit strokes): the legacy full-image snapshot is kept
  deliberately, because ``mask_edit_swap`` normalizes the *whole* mask after
  every segment (13/256 grayscale values are not stable under the float32
  luminance round-trip), so first-touch fragments cannot reproduce the
  pre-transaction mask exactly.  This is an explicit policy decision, not an
  error fallback: the mode is fixed when the capture begins and never
  switches implicitly (see ``docs/dev/CONTINUATION.md`` 2026-07-18).

``CanvasHistoryTransactionService`` remains the transaction lifecycle owner;
this service only owns preimage capture, materialization and reset.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QImage, QPainter


class PreimageCaptureMode(Enum):
    """Explicit capture policy fixed at capture begin."""

    RASTER_REGION = "raster_region"
    MASK_DENSE = "mask_dense"


@dataclass(slots=True)
class _PreimageFragment:
    """First-touch pixels for one non-overlapping image-local rect."""

    rect: QRect
    image: QImage


@dataclass(slots=True)
class PreimageCaptureResult:
    """Finished capture consumed by the deferred-paint history flush."""

    mode: PreimageCaptureMode
    layer_id: str
    dirty: QRect
    offset: tuple[int, int]
    img_key: tuple[int, int]
    mask_edit: bool
    source: QImage
    dense_before: QImage | None
    fragments: list[_PreimageFragment]

    def materialize_before(self, local_dirty: QRect) -> QImage:
        """Build the rectangular before image for ``local_dirty``.

        ``MASK_DENSE`` crops the dense first-touch snapshot (legacy path).
        ``RASTER_REGION`` starts from the current mutated source — pixels
        inside the dirty bounding rect that no segment declared are unchanged
        by contract, so their current value *is* the before value — and
        overwrites every captured fragment at its position, restoring the
        first-touch value of all mutated pixels.
        """

        if self.mode is PreimageCaptureMode.MASK_DENSE:
            assert self.dense_before is not None
            return self.dense_before.copy(local_dirty)
        base = self.source.copy(local_dirty)
        painter = QPainter(base)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        for fragment in self.fragments:
            overlap = fragment.rect.intersected(local_dirty)
            if overlap.isEmpty():
                continue
            src = QRect(overlap)
            src.translate(-fragment.rect.x(), -fragment.rect.y())
            painter.drawImage(
                QPoint(overlap.x() - local_dirty.x(), overlap.y() - local_dirty.y()),
                fragment.image,
                src,
            )
        painter.end()
        return base


class CanvasRasterPreimageCaptureService:
    """Own first-touch preimage capture state for deferred paint history."""

    def __init__(self) -> None:
        self._mode: PreimageCaptureMode | None = None
        self._layer_id: str | None = None
        self._img_key: tuple[int, int] = (0, 0)
        self._offset: tuple[int, int] = (0, 0)
        self._mask_edit = False
        self._dirty: QRect | None = None
        self._source: QImage | None = None
        self._dense_before: QImage | None = None
        self._fragments: list[_PreimageFragment] = []
        self._captured_logical_bytes = 0
        self._dense_snapshot_count = 0

    # -- state inspection -------------------------------------------------
    @property
    def is_active(self) -> bool:
        return self._mode is not None

    @property
    def mode(self) -> PreimageCaptureMode | None:
        return self._mode

    @property
    def pending_layer_id(self) -> str | None:
        return self._layer_id

    def pending_dirty_rect(self) -> QRect | None:
        return QRect(self._dirty) if self._dirty is not None else None

    @property
    def fragment_count(self) -> int:
        return len(self._fragments)

    @property
    def captured_logical_bytes(self) -> int:
        """Logical bytes captured so far (rect area x 4, both modes)."""

        return self._captured_logical_bytes

    @property
    def dense_snapshot_count(self) -> int:
        """Full-image snapshots taken (0 unless ``MASK_DENSE`` is active)."""

        return self._dense_snapshot_count

    # -- capture lifecycle ------------------------------------------------
    def requires_flush(
        self,
        *,
        layer_id: str,
        image: QImage,
        offset: tuple[int, int],
        mask_edit: bool,
    ) -> bool:
        """True when the pending capture must flush before this segment.

        The legacy triggers (layer id / image size change) are kept and
        extended with offset, mutated-image identity and capture-policy
        mismatches so a stale capture can never absorb segments from a
        different source.
        """

        if self._mode is None:
            return False
        return (
            self._layer_id != layer_id
            or self._img_key != (image.width(), image.height())
            or self._offset != offset
            or self._source is not image
            or self._mask_edit != mask_edit
        )

    def capture_segment(
        self,
        *,
        layer_id: str,
        image: QImage,
        bounds: QRect,
        offset: tuple[int, int],
        mask_edit: bool,
    ) -> None:
        """Capture before-pixels for one segment (call before mutation)."""

        if self._mode is None:
            self._begin(
                layer_id=layer_id,
                image=image,
                bounds=bounds,
                offset=offset,
                mask_edit=mask_edit,
            )
            return
        assert self._dirty is not None
        self._dirty = self._dirty.united(bounds)
        if self._mode is PreimageCaptureMode.RASTER_REGION:
            self._capture_uncovered(image, bounds)

    def take(self) -> PreimageCaptureResult | None:
        """Move the pending capture out of the service and reset state."""

        if self._mode is None or self._layer_id is None or self._dirty is None:
            self.reset()
            return None
        assert self._source is not None
        result = PreimageCaptureResult(
            mode=self._mode,
            layer_id=self._layer_id,
            dirty=QRect(self._dirty),
            offset=self._offset,
            img_key=self._img_key,
            mask_edit=self._mask_edit,
            source=self._source,
            dense_before=self._dense_before,
            fragments=self._fragments,
        )
        self.reset()
        return result

    def reset(self) -> None:
        """Release all capture state (end / mid-flush / abort / new doc)."""

        self._mode = None
        self._layer_id = None
        self._img_key = (0, 0)
        self._offset = (0, 0)
        self._mask_edit = False
        self._dirty = None
        self._source = None
        self._dense_before = None
        self._fragments = []
        self._captured_logical_bytes = 0
        self._dense_snapshot_count = 0

    # -- internals --------------------------------------------------------
    def _begin(
        self,
        *,
        layer_id: str,
        image: QImage,
        bounds: QRect,
        offset: tuple[int, int],
        mask_edit: bool,
    ) -> None:
        self._layer_id = layer_id
        self._img_key = (image.width(), image.height())
        self._offset = offset
        self._mask_edit = mask_edit
        self._dirty = QRect(bounds)
        self._source = image
        if mask_edit:
            # MASK_DENSE: deliberate legacy full snapshot (see module doc).
            self._mode = PreimageCaptureMode.MASK_DENSE
            self._dense_before = image.copy()
            self._dense_snapshot_count += 1
            self._captured_logical_bytes += self._img_key[0] * self._img_key[1] * 4
            return
        self._mode = PreimageCaptureMode.RASTER_REGION
        self._capture_uncovered(image, bounds)

    def _capture_uncovered(self, image: QImage, bounds: QRect) -> None:
        local = QRect(bounds)
        local.translate(-self._offset[0], -self._offset[1])
        local = local.intersected(QRect(0, 0, self._img_key[0], self._img_key[1]))
        if local.isEmpty():
            return
        for sub in self._subtract_covered(local):
            self._fragments.append(_PreimageFragment(rect=QRect(sub), image=image.copy(sub)))
            self._captured_logical_bytes += sub.width() * sub.height() * 4

    def _subtract_covered(self, rect: QRect) -> list[QRect]:
        """Split ``rect`` into sub-rects not covered by existing fragments."""

        pending = [QRect(rect)]
        for fragment in self._fragments:
            covered = fragment.rect
            next_pending: list[QRect] = []
            for cand in pending:
                if not cand.intersects(covered):
                    next_pending.append(cand)
                    continue
                inter = cand.intersected(covered)
                if inter.top() > cand.top():
                    next_pending.append(
                        QRect(cand.left(), cand.top(), cand.width(), inter.top() - cand.top())
                    )
                if inter.bottom() < cand.bottom():
                    next_pending.append(
                        QRect(
                            cand.left(),
                            inter.bottom() + 1,
                            cand.width(),
                            cand.bottom() - inter.bottom(),
                        )
                    )
                if inter.left() > cand.left():
                    next_pending.append(
                        QRect(cand.left(), inter.top(), inter.left() - cand.left(), inter.height())
                    )
                if inter.right() < cand.right():
                    next_pending.append(
                        QRect(
                            inter.right() + 1,
                            inter.top(),
                            cand.right() - inter.right(),
                            inter.height(),
                        )
                    )
            pending = next_pending
            if not pending:
                break
        return pending


__all__ = (
    "CanvasRasterPreimageCaptureService",
    "PreimageCaptureMode",
    "PreimageCaptureResult",
)
