"""Persist local mask combine/refine results through existing AI Selection adapters."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

from loomstroke.ai_selection.mask_combine_adapter import (
    Sam3MaskCombineAdapterError,
    combine_mask_assets,
)
from loomstroke.ai_selection.mask_refine_adapter import (
    Sam3MaskRefineAdapterError,
    refine_mask_asset,
)

from .editing_common import edit_operation, finite_number, invalid
from .sam_asset_common import (
    MAX_AGGREGATE_MASK_PIXELS,
    asset_error,
    dimensions,
    identifier,
    integer,
    load,
    mask_result,
    output_id,
    provider_store,
)

OPTION_PROPERTIES = {
    "feather_px": {"type": "number", "minimum": 0, "maximum": 128},
    "smooth_radius_px": {"type": "number", "minimum": 0, "maximum": 128},
    **{
        key: {"type": "integer", "minimum": 0, "maximum": 128}
        for key in ("expand_px", "contract_px")
    },
    **{
        key: {"type": "integer", "minimum": 0, "maximum": 16_777_216}
        for key in ("remove_islands_below_px", "fill_holes_below_px")
    },
    **{key: {"type": "boolean"} for key in ("fill_holes", "invert")},
    "harden_threshold": {"type": "number", "minimum": 0, "maximum": 1},
}


def options(value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) - OPTION_PROPERTIES.keys():
        raise invalid("'options' must contain documented mask refinement fields only.", "options")
    result = {}
    for key, item in value.items():
        spec = OPTION_PROPERTIES[key]
        if spec["type"] == "boolean":
            if not isinstance(item, bool):
                raise invalid(f"'options.{key}' must be boolean.", "options." + key)
            result[key] = item
        elif spec["type"] == "integer":
            result[key] = integer(item, "options." + key, spec["minimum"], spec["maximum"])
        else:
            result[key] = finite_number(
                item, "options." + key, minimum=spec["minimum"], maximum=spec["maximum"]
            )
    return result


async def run_mask_operation(
    service: Any, name: str, arguments: Mapping[str, Any]
) -> dict[str, Any]:
    _provider, store = provider_store(service)
    width, height = dimensions(service, arguments)
    combine = name.endswith("combine_masks")
    values = arguments.get("maskAssetIds") if combine else [arguments.get("maskAssetId")]
    if not isinstance(values, list) or not (2 if combine else 1) <= len(values) <= 32:
        raise invalid(
            "Provide 2-32 mask asset IDs for combine, or one source mask for refine.",
            "maskAssetIds",
        )
    ids = [identifier(item, "maskAssetId") for item in values]
    if width * height * len(ids) > MAX_AGGREGATE_MASK_PIXELS:
        raise invalid("The aggregate mask input exceeds the 64 MP resource limit.", "maskAssetIds")
    for asset_id in ids:
        asset = load(store, "mask_asset", asset_id, "maskAssetId")
        if asset.width * asset.height > MAX_AGGREGATE_MASK_PIXELS:
            raise invalid("A stored input mask exceeds the resource limit.", "maskAssetIds")
    mode = arguments.get("mode", "union")
    if combine and mode not in {"union", "subtract", "intersect", "xor"}:
        raise invalid("'mode' must be union, subtract, intersect or xor.", "mode")
    refinement = {} if combine else options(arguments.get("options", {}))
    async with edit_operation(service, name, arguments, require_canvas=False):
        asset_id = output_id(store, arguments.get("outputAssetId"))
        try:
            if combine:
                asset = await asyncio.to_thread(
                    combine_mask_assets,
                    store,
                    ids,
                    output_asset_id=asset_id,
                    canvas_width=width,
                    canvas_height=height,
                    mode=mode,
                )
            else:
                asset = await asyncio.to_thread(
                    refine_mask_asset,
                    store,
                    ids[0],
                    output_asset_id=asset_id,
                    canvas_width=width,
                    canvas_height=height,
                    options=refinement,
                )
        except (Sam3MaskCombineAdapterError, Sam3MaskRefineAdapterError) as exc:
            raise asset_error(exc) from exc
    return mask_result(asset)
