"""Streamed slice export: compose -> CT-09 encode, one slice at a time (CT-12 §10).

Each slice is composed by the :class:`StreamStripeComposer` (bounded
memory), encoded through the CT-09 raster export contract (atomic
write, alpha policy, encoder options) and described by a
:class:`WebtoonSliceArtifact`.  Item results reuse the CT-10
:class:`BatchExportResult` types: a failed slice is recorded and the
export continues, cancel stops before the next slice and keeps the
finished files (spec 12 §15).  A slice above the profile's byte limit is
reported as ``slice_over_max_bytes`` — quality is never lowered silently
(spec 12 §10; the permitted re-encode range is Phase W4).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from loomstroke.io.export_contract import ExportIssue
from loomstroke.io.raster_export import export_raster

from ..batch_export_item_result import BatchExportItemResult
from ..batch_export_result import BatchExportResult
from .slice_plan import SlicePlan
from .stream_stripe_composer import StreamStripeComposer
from .webtoon_slice_artifact import WebtoonSliceArtifact

__all__ = ["WebtoonExportCoordinator"]

ShouldContinue = Callable[[int, int], bool]


class WebtoonExportCoordinator:
    """Write the planned slices of one stream."""

    OVER_MAX_BYTES: str = "slice_over_max_bytes"

    def __init__(
        self,
        composer: StreamStripeComposer,
        *,
        encoder: Callable[..., Any] = export_raster,
    ) -> None:
        self._composer = composer
        self._encoder = encoder

    def export(
        self,
        slices: Sequence[SlicePlan],
        *,
        destinations: Sequence[Path | None],
        format_id: str,
        planned: Sequence[Path] | None = None,
        dpi: tuple[float, float] = (72.0, 72.0),
        background_rgba: tuple[int, int, int, int] = (255, 255, 255, 255),
        alpha_mode: str = "preserve",
        matte_rgba: tuple[int, int, int, int] | None = None,
        encoder_options: Mapping[str, Any] | None = None,
        max_file_bytes: int | None = None,
        should_continue: ShouldContinue | None = None,
    ) -> tuple[BatchExportResult, tuple[WebtoonSliceArtifact, ...]]:
        """``destinations[i] is None`` marks a slice skipped by collision policy;
        ``planned`` (optional, same length) supplies the name such a skipped
        item is reported under."""
        if len(slices) != len(destinations):
            raise ValueError("slices and destinations must have the same length")
        if planned is not None and len(planned) != len(slices):
            raise ValueError("planned and slices must have the same length")
        items: list[BatchExportItemResult] = []
        artifacts: list[WebtoonSliceArtifact] = []
        total = len(slices)
        cancelled = False
        for index, (plan, destination) in enumerate(zip(slices, destinations, strict=True)):
            item_id = f"slice_{plan.index + 1}"
            if destination is None:
                planned_path = planned[index] if planned is not None else None
                items.append(BatchExportItemResult(item_id, planned_path, "skipped"))
                continue
            if cancelled or (should_continue is not None and not should_continue(index, total)):
                cancelled = True
                items.append(BatchExportItemResult(item_id, destination, "cancelled"))
                continue
            item, artifact = self._export_slice(
                plan,
                destination,
                item_id,
                format_id=format_id,
                dpi=dpi,
                background_rgba=background_rgba,
                alpha_mode=alpha_mode,
                matte_rgba=matte_rgba,
                encoder_options=encoder_options,
                max_file_bytes=max_file_bytes,
            )
            items.append(item)
            if artifact is not None:
                artifacts.append(artifact)
        return BatchExportResult(tuple(items)), tuple(artifacts)

    def _export_slice(
        self,
        plan: SlicePlan,
        destination: Path,
        item_id: str,
        *,
        format_id: str,
        dpi: tuple[float, float],
        background_rgba: tuple[int, int, int, int],
        alpha_mode: str,
        matte_rgba: tuple[int, int, int, int] | None,
        encoder_options: Mapping[str, Any] | None,
        max_file_bytes: int | None,
    ) -> tuple[BatchExportItemResult, WebtoonSliceArtifact | None]:
        try:
            rgba = self._composer.compose(plan.stream_y0, plan.stream_y1)
            result = self._encoder(
                rgba,
                Path(destination),
                format_id,
                canvas_dpi=dpi,
                alpha_mode=alpha_mode,
                matte_rgba=matte_rgba,
                background_rgba=background_rgba,
                encoder_options=dict(encoder_options or {}),
            )
        except Exception as error:  # recorded per item (spec 12 §15)
            return (
                BatchExportItemResult(
                    item_id,
                    destination,
                    "failed",
                    (ExportIssue(code="export_failed", message=str(error), severity="error"),),
                ),
                None,
            )
        issues = list(getattr(result, "issues", ()) or ())
        if getattr(result, "status", "success") == "failed":
            return BatchExportItemResult(item_id, destination, "failed", tuple(issues)), None
        payload = Path(destination).read_bytes()
        warnings = list(plan.warnings)
        if max_file_bytes is not None and len(payload) > max_file_bytes:
            warnings.append(self.OVER_MAX_BYTES)
            issues.append(
                ExportIssue(
                    code=self.OVER_MAX_BYTES,
                    message=f"{len(payload)} bytes > {max_file_bytes}",
                )
            )
        artifact = WebtoonSliceArtifact(
            index=plan.index,
            file_name=Path(destination).name,
            width=int(rgba.shape[1]),
            height=int(rgba.shape[0]),
            byte_count=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
            source_page_ids=tuple(
                span.page_id
                for span in self._composer.stream.spans_between(plan.stream_y0, plan.stream_y1)
            ),
            stream_y0=plan.stream_y0,
            stream_y1=plan.stream_y1,
            warnings=tuple(warnings),
        )
        status = "warning" if issues else "success"
        return BatchExportItemResult(item_id, destination, status, tuple(issues)), artifact
