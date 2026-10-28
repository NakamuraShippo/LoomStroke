"""Resolve the export colour policy against the document profile (CT-09 §6/§9).

``embed`` tags RGB pixels with the document's compatible RGB ICC bytes, ``untagged``
writes no profile, ``convert_srgb`` transforms the pixels with the
authoritative CPU transform (CT-11 P1) and tags sRGB.  Nothing here ever
fabricates a profile the document does not have: a document without a
profile is "treated as sRGB", and the issue codes say so instead of
silently tagging it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

from .color_profile_registry import ColorProfileRegistry, transform_rgba
from .export_contract import ExportIssue

RgbaTransform = Callable[[NDArray[np.uint8]], NDArray[np.uint8]]


@dataclass(frozen=True, slots=True)
class ResolvedColorPolicy:
    """``transform`` (if any) runs on straight RGBA before encoding."""

    icc_bytes: bytes | None
    issues: tuple[ExportIssue, ...] = ()
    transform: RgbaTransform | None = None


class ExportColorPolicy:
    """Turn a policy id + document profile bytes into encoder inputs."""

    def __init__(self, registry: ColorProfileRegistry | None = None) -> None:
        self._registry = registry or ColorProfileRegistry()

    @property
    def registry(self) -> ColorProfileRegistry:
        return self._registry

    def resolve(self, policy: str, *, document_profile: bytes | None) -> ResolvedColorPolicy:
        if policy == "untagged":
            return ResolvedColorPolicy(icc_bytes=None)
        if policy == "embed":
            return self._resolve_embed(document_profile)
        if policy == "convert_srgb":
            return self._resolve_convert_srgb(document_profile)
        raise ValueError(f"unknown export color policy: {policy!r}")

    # -- policies ---------------------------------------------------------------

    def _resolve_embed(self, document_profile: bytes | None) -> ResolvedColorPolicy:
        if not document_profile:
            return ResolvedColorPolicy(
                icc_bytes=None,
                issues=(
                    ExportIssue(
                        code="document_profile_missing",
                        message="the document has no ICC profile; exported untagged "
                        "(treated as sRGB)",
                    ),
                ),
            )
        info = self._register(document_profile)
        if info is None:
            return ResolvedColorPolicy(icc_bytes=None, issues=(self._invalid_issue(),))
        if info.colorspace.strip().upper() != "RGB":
            return ResolvedColorPolicy(
                icc_bytes=None,
                issues=(
                    ExportIssue(
                        code="document_profile_invalid",
                        message="the document ICC profile is not RGB; "
                        "RGB pixels were exported without it",
                    ),
                ),
            )
        return ResolvedColorPolicy(icc_bytes=self._registry.profile_bytes(info.profile_id))

    def _resolve_convert_srgb(self, document_profile: bytes | None) -> ResolvedColorPolicy:
        srgb = self._registry.srgb()
        srgb_bytes = self._registry.profile_bytes(srgb.profile_id)
        if not document_profile:
            return ResolvedColorPolicy(
                icc_bytes=srgb_bytes,
                issues=(
                    ExportIssue(
                        code="source_assumed_srgb",
                        message="the document has no ICC profile; pixels were assumed "
                        "to be sRGB and tagged as such",
                    ),
                ),
            )
        info = self._register(document_profile)
        if info is None:
            return ResolvedColorPolicy(icc_bytes=srgb_bytes, issues=(self._invalid_issue(),))
        if info.profile_id == srgb.profile_id:
            return ResolvedColorPolicy(icc_bytes=srgb_bytes)
        registry = self._registry
        source_id = info.profile_id
        destination_id = srgb.profile_id

        def _transform(rgba: NDArray[np.uint8]) -> NDArray[np.uint8]:
            return transform_rgba(
                rgba,
                registry,
                source_profile_id=source_id,
                destination_profile_id=destination_id,
                intent="relative",
                black_point_compensation=True,
            )

        return ResolvedColorPolicy(icc_bytes=srgb_bytes, transform=_transform)

    # -- helpers ----------------------------------------------------------------

    def _register(self, data: bytes):  # type: ignore[no-untyped-def]
        try:
            return self._registry.register(data)
        except ValueError:
            return None

    @staticmethod
    def _invalid_issue() -> ExportIssue:
        return ExportIssue(
            code="document_profile_invalid",
            message="the document ICC profile could not be parsed; exported without it",
        )
