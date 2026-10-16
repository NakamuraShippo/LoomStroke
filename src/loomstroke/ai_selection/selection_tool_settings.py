"""Raw-value settings contract for the future SAM3 AI Select tool."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, cast

from .data_model import BoxPrompt, OverlayOptions, PointPrompt
from .metadata_scrub import scrub_sam3_metadata
from .selection_bridge import (
    Sam3SelectionMode,
    Sam3SelectionPromptSpec,
    Sam3SelectionScope,
    SelectionApplyOperation,
)

SAM3_AI_SELECT_TOOL_SETTINGS_SCHEMA_VERSION = "sam3.ai_select_tool_settings.v1"
SAM3_AI_SELECT_RUN_CONFIG_SCHEMA_VERSION = "sam3.ai_select_run_config.v1"

SAM3_AI_SELECT_MODE_VALUES = ("prompt", "click", "box", "exemplar", "mixed")
SAM3_AI_SELECT_SCOPE_VALUES = ("active_layer", "visible_composite", "selection_bounds", "page")
SAM3_AI_SELECT_OPERATION_VALUES = ("replace", "add", "subtract", "intersect")
SAM3_AI_SELECT_PREVIEW_COLOR_MODE_VALUES = ("single", "instance", "heat")
SAM3_AI_SELECT_EXPORT_SOURCE_VALUES = ("active_layer", "composite")
SAM3_AI_SELECT_SHEET_FORMAT_VALUES = ("png", "webp")
SAM3_AI_SELECT_COMBINE_MODE_VALUES = ("union", "subtract", "intersect", "xor")

_MAX_APPLY_EDGE_RADIUS_PX = 16
_MAX_TIGHTNESS_PX = 16
_MAX_EXPORT_PADDING_PX = 64
_MAX_SHEET_SPACING_PX = 16
_MIN_CANDIDATES = 1
_MAX_CANDIDATES = 64


class Sam3AiSelectToolSettingsError(ValueError):
    """Raised when AI Select tool settings contain non-raw values."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class Sam3AiSelectRunConfig:
    """JSON-safe run configuration derived from AI Select tool settings."""

    scope: Sam3SelectionScope
    prompt_spec: Sam3SelectionPromptSpec
    operation: SelectionApplyOperation
    overlay_options: OverlayOptions
    max_candidates: int
    min_score: float
    mask_threshold: float = 0.5
    split_disconnected_instances: bool = True
    auto_mask_cleanup: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
    schema_version: str = SAM3_AI_SELECT_RUN_CONFIG_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "scope": self.scope,
            "promptSpec": _prompt_spec_to_dict(self.prompt_spec),
            "operation": self.operation,
            "overlayOptions": self.overlay_options.to_dict(),
            "maxCandidates": self.max_candidates,
            "minScore": self.min_score,
            "maskThreshold": self.mask_threshold,
            "splitDisconnectedInstances": self.split_disconnected_instances,
            "autoMaskCleanup": self.auto_mask_cleanup,
            "metadata": dict(scrub_sam3_metadata(self.metadata)),
        }


@dataclass(frozen=True, slots=True)
class Sam3AiSelectToolSettings:
    """Runtime settings for the future SAM3 AI Select tool."""

    prompt_text: str | None = None
    mode: Sam3SelectionMode = "prompt"
    scope: Sam3SelectionScope = "visible_composite"
    # CT-23: the host source the UI RUN reads (``scope`` stays for API compatibility).
    source_scope: Sam3SelectionScope = "active_layer"
    apply_operation: SelectionApplyOperation = "replace"
    points: tuple[PointPrompt, ...] = field(default_factory=tuple)
    boxes: tuple[BoxPrompt, ...] = field(default_factory=tuple)
    exemplar_asset_ids: tuple[str, ...] = field(default_factory=tuple)
    negative_text: str | None = None
    locale: str | None = None
    normalized_text: str | None = None
    max_candidates: int = 16
    min_score: float = 0.2
    mask_threshold: float = 0.5
    split_disconnected_instances: bool = True
    show_all_instances: bool = True
    auto_mask_cleanup: bool = True
    combine_mode: str = "union"
    preview_color_mode: str = "instance"
    overlay_opacity: float = 0.45
    include_rejected: bool = False
    click_auto_run: bool = True
    click_accumulate_auto_apply: bool = True
    candidate_click_auto_apply: bool = True
    apply_edge_feather_px: int = 1
    apply_edge_smooth_px: int = 1
    apply_edge_tightness_px: int = 0
    export_padding_px: int = 0
    export_source: str = "active_layer"
    sheet_spacing_px: int = 2
    sheet_power_of_two: bool = False
    sheet_format: str = "png"
    metadata: Mapping[str, Any] | None = None
    schema_version: str = SAM3_AI_SELECT_TOOL_SETTINGS_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "mode", _mode(self.mode))
        object.__setattr__(self, "scope", _scope(self.scope))
        object.__setattr__(self, "source_scope", _scope(self.source_scope))
        object.__setattr__(self, "apply_operation", _operation(self.apply_operation))
        object.__setattr__(self, "preview_color_mode", _preview_color_mode(self.preview_color_mode))
        object.__setattr__(self, "prompt_text", _optional_clean_text(self.prompt_text))
        object.__setattr__(self, "negative_text", _optional_clean_text(self.negative_text))
        object.__setattr__(self, "locale", _optional_clean_text(self.locale))
        object.__setattr__(self, "normalized_text", _optional_clean_text(self.normalized_text))
        object.__setattr__(
            self,
            "exemplar_asset_ids",
            tuple(
                cleaned
                for cleaned in (_optional_clean_text(item) for item in self.exemplar_asset_ids)
                if cleaned is not None
            ),
        )
        object.__setattr__(self, "points", tuple(self.points))
        object.__setattr__(self, "boxes", tuple(self.boxes))
        object.__setattr__(
            self,
            "max_candidates",
            _clamp_int(self.max_candidates, _MIN_CANDIDATES, _MAX_CANDIDATES),
        )
        object.__setattr__(self, "min_score", _clamp_float(self.min_score, 0.0, 1.0))
        object.__setattr__(self, "mask_threshold", _clamp_float(self.mask_threshold, 0.05, 0.95))
        object.__setattr__(
            self, "split_disconnected_instances", bool(self.split_disconnected_instances)
        )
        object.__setattr__(self, "overlay_opacity", _clamp_float(self.overlay_opacity, 0.0, 1.0))
        object.__setattr__(self, "show_all_instances", bool(self.show_all_instances))
        object.__setattr__(self, "auto_mask_cleanup", bool(self.auto_mask_cleanup))
        object.__setattr__(self, "combine_mode", _combine_mode(self.combine_mode))
        object.__setattr__(self, "include_rejected", bool(self.include_rejected))
        object.__setattr__(self, "click_auto_run", bool(self.click_auto_run))
        object.__setattr__(
            self, "click_accumulate_auto_apply", bool(self.click_accumulate_auto_apply)
        )
        object.__setattr__(
            self, "candidate_click_auto_apply", bool(self.candidate_click_auto_apply)
        )
        object.__setattr__(
            self,
            "apply_edge_feather_px",
            _clamp_int(self.apply_edge_feather_px, 0, _MAX_APPLY_EDGE_RADIUS_PX),
        )
        object.__setattr__(
            self,
            "apply_edge_smooth_px",
            _clamp_int(self.apply_edge_smooth_px, 0, _MAX_APPLY_EDGE_RADIUS_PX),
        )
        object.__setattr__(
            self,
            "apply_edge_tightness_px",
            _clamp_signed_int(self.apply_edge_tightness_px, _MAX_TIGHTNESS_PX),
        )
        object.__setattr__(
            self,
            "export_padding_px",
            _clamp_int(self.export_padding_px, 0, _MAX_EXPORT_PADDING_PX),
        )
        object.__setattr__(self, "export_source", _export_source(self.export_source))
        object.__setattr__(
            self,
            "sheet_spacing_px",
            _clamp_int(self.sheet_spacing_px, 0, _MAX_SHEET_SPACING_PX),
        )
        object.__setattr__(self, "sheet_power_of_two", bool(self.sheet_power_of_two))
        object.__setattr__(self, "sheet_format", _sheet_format(self.sheet_format))
        object.__setattr__(self, "metadata", dict(scrub_sam3_metadata(dict(self.metadata or {}))))

    def to_prompt_spec(self) -> Sam3SelectionPromptSpec:
        return Sam3SelectionPromptSpec(
            mode=self.mode,
            prompt_text=self.prompt_text,
            points=tuple(self.points),
            boxes=tuple(self.boxes),
            exemplar_asset_ids=tuple(self.exemplar_asset_ids),
            negative_text=self.negative_text,
            locale=self.locale,
            normalized_text=self.normalized_text,
        )

    def to_overlay_options(self) -> OverlayOptions:
        return OverlayOptions(
            opacity=self.overlay_opacity,
            color_mode=self.preview_color_mode,
            include_rejected=self.include_rejected,
        )

    def to_run_config(self) -> Sam3AiSelectRunConfig:
        return build_ai_select_run_config(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schemaVersion": self.schema_version,
            "promptText": self.prompt_text,
            "mode": self.mode,
            "scope": self.scope,
            "applyOperation": self.apply_operation,
            "points": [point.to_dict() for point in self.points],
            "boxes": [box.to_dict() for box in self.boxes],
            "exemplarAssetIds": list(self.exemplar_asset_ids),
            "negativeText": self.negative_text,
            "locale": self.locale,
            "normalizedText": self.normalized_text,
            "maxCandidates": self.max_candidates,
            "minScore": self.min_score,
            "maskThreshold": self.mask_threshold,
            "splitDisconnectedInstances": self.split_disconnected_instances,
            "showAllInstances": self.show_all_instances,
            "autoMaskCleanup": self.auto_mask_cleanup,
            "combineMode": self.combine_mode,
            "previewColorMode": self.preview_color_mode,
            "overlayOpacity": self.overlay_opacity,
            "includeRejected": self.include_rejected,
            "clickAutoRun": self.click_auto_run,
            "clickAccumulateAutoApply": self.click_accumulate_auto_apply,
            "candidateClickAutoApply": self.candidate_click_auto_apply,
            "applyEdgeFeatherPx": self.apply_edge_feather_px,
            "applyEdgeSmoothPx": self.apply_edge_smooth_px,
            "applyEdgeTightnessPx": self.apply_edge_tightness_px,
            "exportPaddingPx": self.export_padding_px,
            "exportSource": self.export_source,
            "sheetSpacingPx": self.sheet_spacing_px,
            "sheetPowerOfTwo": self.sheet_power_of_two,
            "sheetFormat": self.sheet_format,
            "metadata": dict(scrub_sam3_metadata(dict(self.metadata or {}))),
        }


def build_ai_select_run_config(settings: Sam3AiSelectToolSettings) -> Sam3AiSelectRunConfig:
    metadata = dict(scrub_sam3_metadata(dict(settings.metadata or {})))
    metadata.update(
        {
            "showAllInstances": bool(settings.show_all_instances),
            "toolMode": settings.mode,
            "toolScope": settings.scope,
        }
    )
    # show_all_instances=false clamps the run to the single best candidate.
    # Click mode is exempt: tracker proposals are disambiguation alternatives,
    # not same-concept instances, and Next/Previous relies on them.
    if settings.show_all_instances or settings.mode == "click":
        max_candidates = settings.max_candidates
    else:
        max_candidates = 1
    return Sam3AiSelectRunConfig(
        scope=settings.scope,
        prompt_spec=settings.to_prompt_spec(),
        operation=settings.apply_operation,
        overlay_options=settings.to_overlay_options(),
        max_candidates=max_candidates,
        min_score=settings.min_score,
        mask_threshold=settings.mask_threshold,
        split_disconnected_instances=settings.split_disconnected_instances,
        auto_mask_cleanup=settings.auto_mask_cleanup,
        metadata=metadata,
    )


def _prompt_spec_to_dict(prompt_spec: Sam3SelectionPromptSpec) -> dict[str, Any]:
    return {
        "mode": prompt_spec.mode,
        "promptText": prompt_spec.prompt_text,
        "points": [point.to_dict() for point in prompt_spec.points],
        "boxes": [box.to_dict() for box in prompt_spec.boxes],
        "exemplarAssetIds": list(prompt_spec.exemplar_asset_ids),
        "negativeText": prompt_spec.negative_text,
        "locale": prompt_spec.locale,
        "normalizedText": prompt_spec.normalized_text,
    }


def _mode(value: str) -> Sam3SelectionMode:
    return cast(
        "Sam3SelectionMode",
        _raw_value(value, SAM3_AI_SELECT_MODE_VALUES, "invalid_ai_select_mode", "mode"),
    )


def _scope(value: str) -> Sam3SelectionScope:
    return cast(
        "Sam3SelectionScope",
        _raw_value(value, SAM3_AI_SELECT_SCOPE_VALUES, "invalid_ai_select_scope", "scope"),
    )


def _operation(value: str) -> SelectionApplyOperation:
    return cast(
        "SelectionApplyOperation",
        _raw_value(
            value,
            SAM3_AI_SELECT_OPERATION_VALUES,
            "invalid_ai_select_apply_operation",
            "apply_operation",
        ),
    )


def _preview_color_mode(value: str) -> str:
    return _raw_value(
        value,
        SAM3_AI_SELECT_PREVIEW_COLOR_MODE_VALUES,
        "invalid_ai_select_preview_color_mode",
        "preview_color_mode",
    )


def _combine_mode(value: str) -> str:
    return _raw_value(
        value,
        SAM3_AI_SELECT_COMBINE_MODE_VALUES,
        "invalid_ai_select_combine_mode",
        "combine_mode",
    )


def _export_source(value: str) -> str:
    return _raw_value(
        value,
        SAM3_AI_SELECT_EXPORT_SOURCE_VALUES,
        "invalid_ai_select_export_source",
        "export_source",
    )


def _sheet_format(value: str) -> str:
    return _raw_value(
        value,
        SAM3_AI_SELECT_SHEET_FORMAT_VALUES,
        "invalid_ai_select_sheet_format",
        "sheet_format",
    )


def _raw_value(value: str, allowed: tuple[str, ...], code: str, field_name: str) -> str:
    raw = str(value).strip()
    if raw in allowed:
        return raw
    raise Sam3AiSelectToolSettingsError(
        code,
        f"SAM3 AI Select {field_name} must be a raw value: {raw!r}.",
    )


def _optional_clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip()
    return cleaned or None


def _clamp_int(value: Any, minimum: int, maximum: int) -> int:
    try:
        numeric = int(value)
    except (TypeError, ValueError):
        numeric = minimum
    return max(minimum, min(maximum, numeric))


def _clamp_signed_int(value: Any, limit: int, *, default: int = 0) -> int:
    """符号付き設定を ``-limit..+limit`` に clamp する。

    ``_clamp_int`` は無効入力を ``minimum`` に落とすため、負の下限を持つ
    符号付き設定 -- 例: エッジタイトさ -- では無効入力が最小値に張り付いて
    しまう。本ヘルパーは無効入力を中立の ``default`` [既定 0] へ落とす。
    """

    try:
        numeric = int(value)
    except (TypeError, ValueError):
        numeric = default
    return max(-limit, min(limit, numeric))


def _clamp_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        numeric = minimum
    return max(minimum, min(maximum, numeric))


__all__ = [
    "SAM3_AI_SELECT_COMBINE_MODE_VALUES",
    "SAM3_AI_SELECT_EXPORT_SOURCE_VALUES",
    "SAM3_AI_SELECT_MODE_VALUES",
    "SAM3_AI_SELECT_OPERATION_VALUES",
    "SAM3_AI_SELECT_PREVIEW_COLOR_MODE_VALUES",
    "SAM3_AI_SELECT_RUN_CONFIG_SCHEMA_VERSION",
    "SAM3_AI_SELECT_SCOPE_VALUES",
    "SAM3_AI_SELECT_SHEET_FORMAT_VALUES",
    "SAM3_AI_SELECT_TOOL_SETTINGS_SCHEMA_VERSION",
    "Sam3AiSelectRunConfig",
    "Sam3AiSelectToolSettings",
    "Sam3AiSelectToolSettingsError",
    "build_ai_select_run_config",
]
