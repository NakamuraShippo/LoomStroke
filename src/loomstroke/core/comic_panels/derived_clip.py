"""Distinguish saved derived coverage from an independently edited clip."""

from loomstroke.core.mask_coverage import mask_coverage_alpha8

from .membership import PART_KEY


def authored_coverage(*, metadata, enabled, mask, frame, expected):
    if metadata.get(PART_KEY, {}).get("clip_auto_update") is False:
        return True
    if not enabled or mask is None:
        return True
    if frame.region_version != frame.clip_mask_region_version:
        return False
    return mask_coverage_alpha8(mask) != expected
