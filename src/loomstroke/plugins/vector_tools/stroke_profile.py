"""Adapter between ``VectorObject.meta["loomstroke:stroke_profile"]`` and ``StrokeProfile``."""

from __future__ import annotations

from typing import Any

from loomstroke.core.vector_correction import StrokeProfile

from .common import (
    VECTOR_STROKE_PROFILE_META_KEY,
    VectorObject,
    clone_vector_object_with_patch,
)

__all__ = ["StrokeProfileAdapter"]


class StrokeProfileAdapter:
    @staticmethod
    def read(obj: Any) -> StrokeProfile | None:
        meta = getattr(obj, "meta", None)
        if not isinstance(meta, dict) or VECTOR_STROKE_PROFILE_META_KEY not in meta:
            return None
        style = getattr(obj, "style", None)
        base = float(getattr(style, "stroke_width", 1.0) or 1.0)
        return StrokeProfile.from_raw(meta.get(VECTOR_STROKE_PROFILE_META_KEY), base)

    @classmethod
    def has_variable_width(cls, obj: Any) -> bool:
        profile = cls.read(obj)
        return profile is not None and profile.is_variable

    @staticmethod
    def write(obj: VectorObject, profile: StrokeProfile | None) -> VectorObject:
        """Return *obj* with the profile stored as v2 (or removed when ``None``)."""
        meta = dict(obj.meta)
        if profile is None:
            meta.pop(VECTOR_STROKE_PROFILE_META_KEY, None)
        else:
            meta[VECTOR_STROKE_PROFILE_META_KEY] = profile.to_raw()
        if meta == obj.meta:
            return obj
        return clone_vector_object_with_patch(obj, meta=meta)
