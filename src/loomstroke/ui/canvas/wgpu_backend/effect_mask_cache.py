"""Compatibility imports for the canonical filter module."""

from .filter_mask_cache import WgpuFilterMaskCache

WgpuEffectMaskCache = WgpuFilterMaskCache

__all__ = ["WgpuEffectMaskCache"]
