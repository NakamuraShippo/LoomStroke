"""Webtoon vertical stream, slicing and package export (CT-12, spec 12).

Pure, Qt-free building blocks — one class per module:

- :mod:`webtoon_profile` — :class:`WebtoonProfile` (generic defaults, §9)
- :mod:`webtoon_segment` — :class:`WebtoonSegment` (page + crop + gaps, §5)
- :mod:`stream_span` / :mod:`vertical_stream_plan` — stream coordinates
- :mod:`vertical_stream_resolver` — :class:`VerticalStreamResolver` (§9)
- :mod:`slice_plan` / :mod:`fixed_height_slicer` — W2 boundaries (§16)
- :mod:`stream_stripe_composer` — bounded-memory slice raster (§10/§13)
- :mod:`webtoon_slice_artifact` / :mod:`webtoon_export_coordinator` (§10)
- :mod:`webtoon_manifest_writer` — ``webtoon_manifest.json`` (§10)
"""

from .fixed_height_slicer import FixedHeightSlicer
from .slice_plan import SlicePlan
from .stream_span import StreamSpan
from .stream_stripe_composer import StreamStripeComposer
from .vertical_stream_plan import VerticalStreamPlan
from .vertical_stream_resolver import VerticalStreamResolver
from .webtoon_export_coordinator import WebtoonExportCoordinator
from .webtoon_manifest_writer import WebtoonManifestWriter
from .webtoon_profile import WebtoonProfile
from .webtoon_segment import WebtoonSegment
from .webtoon_slice_artifact import WebtoonSliceArtifact

__all__ = [
    "FixedHeightSlicer",
    "SlicePlan",
    "StreamSpan",
    "StreamStripeComposer",
    "VerticalStreamPlan",
    "VerticalStreamResolver",
    "WebtoonExportCoordinator",
    "WebtoonManifestWriter",
    "WebtoonProfile",
    "WebtoonSegment",
    "WebtoonSliceArtifact",
]
