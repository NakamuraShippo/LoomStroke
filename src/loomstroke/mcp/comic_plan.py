"""Comic page planning and validation helpers for LoomStroke MCP."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .comic_style import normalize_comic_style
from .safety import scrub_secrets, validate_output_path
from .types import McpStructuredError, ReasonCode

SCHEMA_VERSION = "mcp.comic_page_plan.v1"
MAX_PAGE_DIMENSION = 16_384
MAX_TEXT_LENGTH = 2_000
MAX_DESCRIPTION_LENGTH = 120
MAX_BALLOONS_PER_PANEL = 4
DEFAULT_PAGE = {
    "width": 832,
    "height": 1216,
    "dpi": 72,
    "background": "#F7F1E6",
}

# Line-initial panel markers: `コマ1:` / `1コマ目:` / `Panel 1:` (fullwidth
# digits and colons accepted). Bare numbers are NOT markers — `10:10ごろ` is prose.
_PANEL_MARKER = re.compile(
    r"^\s*(?:コマ\s*[0-9０-９]+|[0-9０-９]+\s*コマ目|panel\s*[0-9０-９]+)\s*[:：]",  # noqa: RUF001 - fullwidth digits/colon are intentional
    re.IGNORECASE,
)
_QUOTED_TEXT = re.compile(r"「([^「」]*)」|『([^『』]*)』|“([^“”]*)”")
_NARRATION_LABEL = re.compile(
    r"(?:セリフ|ナレーション|地の文|説明|dialogue|narration|caption)\s*[:：]?",  # noqa: RUF001 - fullwidth colon is intentional
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class _PanelBlock:
    """One planned panel: balloon texts plus a scene description for the artist."""

    balloons: tuple[str, ...]
    description: str


@dataclass(frozen=True, slots=True)
class ComicRect:
    x: float
    y: float
    width: float
    height: float

    def to_schema(self) -> dict[str, float]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }


@dataclass(frozen=True, slots=True)
class ComicTextRef:
    text_id: str
    role: str
    panel_id: str
    rect: ComicRect
    text: str
    text_length: int
    requested_font_size: int | None
    fit_mode: str
    text_style: Mapping[str, Any] = field(default_factory=dict)

    def to_fit_report(self) -> dict[str, Any]:
        from .comic_native_text import build_comic_native_text  # noqa: PLC0415

        result = build_comic_native_text(
            {
                "text": self.text,
                "rect": self.rect.to_schema(),
                "textStyle": {
                    **self.text_style,
                    "fontSize": self.requested_font_size,
                    "fit": self.fit_mode,
                },
            }
        )
        return result.fit.to_fit_report(
            text_id=self.text_id,
            role=self.role,
            panel_id=self.panel_id,
        )


@dataclass(frozen=True, slots=True)
class ComicValidationIssue:
    code: str
    message: str
    path: str | None = None

    def to_schema(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.path:
            payload["path"] = self.path
        return payload


@dataclass(frozen=True, slots=True)
class ComicPagePlanValidation:
    ok: bool
    blocked: bool
    summary: dict[str, Any]
    text_refs: tuple[ComicTextRef, ...]
    blocked_reasons: tuple[ComicValidationIssue, ...] = ()
    layout_warnings: tuple[ComicValidationIssue, ...] = ()
    root_status: dict[str, Any] = field(default_factory=dict)

    def to_schema(self) -> dict[str, Any]:
        return {
            "schemaVersion": "mcp.comic_plan_validation.v1",
            "planSchemaVersion": SCHEMA_VERSION,
            "ok": self.ok,
            "blocked": self.blocked,
            "blockedReasons": [issue.to_schema() for issue in self.blocked_reasons],
            "layoutWarnings": [issue.to_schema() for issue in self.layout_warnings],
            "textFitReports": [text.to_fit_report() for text in self.text_refs],
            "rootStatus": dict(self.root_status),
            "summary": scrub_secrets(self.summary),
        }


def validate_comic_page_plan(
    plan: Mapping[str, Any],
    *,
    known_asset_ids: Sequence[str] = (),
    roots: Sequence[str | Path] = (),
) -> ComicPagePlanValidation:
    """Validate a ComicPagePlan without writing files or mutating the document."""

    validator = _ComicPlanValidator(plan, known_asset_ids=known_asset_ids, roots=roots)
    return validator.validate()


def plan_comic_page(
    arguments: Mapping[str, Any],
    *,
    default_page: Mapping[str, Any] | None = None,
    known_asset_ids: Sequence[str] = (),
    roots: Sequence[str | Path] = (),
) -> dict[str, Any]:
    """Create a deterministic ComicPagePlan from a short script."""

    script = str(arguments.get("script") or "").strip()
    if not script:
        raise McpStructuredError(
            ReasonCode.INVALID_ARGUMENT,
            "'script' is required.",
            details={"argument": "script"},
            code=-32602,
        )
    constraints = _mapping(arguments.get("constraints"))
    max_panels = _clamped_int(constraints.get("maxPanels"), default=4, minimum=1, maximum=8)
    safe_margin = _clamped_int(
        constraints.get("safeMargin"),
        default=40,
        minimum=8,
        maximum=256,
    )
    min_font_size = _clamped_int(
        constraints.get("minimumFontSize"),
        default=12,
        minimum=6,
        maximum=64,
    )
    page = _planned_page(arguments.get("page"), default_page=default_page)
    style = {
        "readingDirection": "ltr",
        "fontFamily": "default",
        "textColor": "#111111",
        "panelStrokeColor": "#111111",
        "panelStrokeWidth": 5,
        **normalize_comic_style(arguments.get("style")),
    }
    asset_ids, warnings = _planned_asset_ids(arguments.get("assetIds"), known_asset_ids)
    if style.get("description"):
        warnings.append("style_description_is_metadata_not_an_image_generation_prompt")
    blocks, parse_warnings = _script_panel_blocks(script, max_panels=max_panels)
    warnings.extend(parse_warnings)
    panels = _planned_panels(
        page,
        blocks,
        asset_ids=asset_ids,
        safe_margin=safe_margin,
        min_font_size=min_font_size,
    )
    export = {
        "fileName": "comic_page.png",
        "registerAsset": True,
        **_mapping(arguments.get("export")),
    }
    plan = {
        "schemaVersion": SCHEMA_VERSION,
        "page": page,
        "style": style,
        "panels": panels,
        "export": export,
    }
    validation = validate_comic_page_plan(plan, known_asset_ids=known_asset_ids, roots=roots)
    if validation.blocked:
        warnings.append("planned_comic_validation_blocked")
    return {
        "schemaVersion": "mcp.comic_page_plan_result.v1",
        "plan": plan,
        "warnings": sorted(set(warnings)),
        "requiresConfirmation": False,
        "validation": validation.to_schema(),
    }


class _ComicPlanValidator:
    def __init__(
        self,
        plan: Mapping[str, Any],
        *,
        known_asset_ids: Sequence[str],
        roots: Sequence[str | Path],
    ) -> None:
        self._plan = dict(plan)
        self._known_asset_ids = set(known_asset_ids)
        self._roots = tuple(roots)
        self._blocked: list[ComicValidationIssue] = []
        self._warnings: list[ComicValidationIssue] = []
        self._text_refs: list[ComicTextRef] = []
        self._ids: set[str] = set()

    def validate(self) -> ComicPagePlanValidation:
        if self._plan.get("schemaVersion") != SCHEMA_VERSION:
            self._blocked.append(
                ComicValidationIssue(
                    "unsupported_schema_version",
                    "ComicPagePlan schemaVersion is unsupported.",
                    "schemaVersion",
                )
            )

        page = _mapping(self._plan.get("page"))
        width = _bounded_int(page.get("width"), path="page.width", blocked=self._blocked)
        height = _bounded_int(page.get("height"), path="page.height", blocked=self._blocked)
        if width is not None and height is not None:
            self._validate_page_bounds(width, height)

        panels = self._plan.get("panels")
        if not isinstance(panels, list) or not panels:
            self._blocked.append(
                ComicValidationIssue(
                    "panels_required",
                    "ComicPagePlan requires at least one panel.",
                    "panels",
                )
            )
        else:
            for index, panel in enumerate(panels):
                self._validate_panel(panel, index, width, height)

        root_status = self._validate_roots()
        blocked = bool(self._blocked)
        return ComicPagePlanValidation(
            ok=not blocked,
            blocked=blocked,
            summary=self._summary(width=width, height=height),
            text_refs=tuple(self._text_refs),
            blocked_reasons=tuple(self._blocked),
            layout_warnings=tuple(self._warnings),
            root_status=root_status,
        )

    def _validate_page_bounds(self, width: int, height: int) -> None:
        if width > MAX_PAGE_DIMENSION or height > MAX_PAGE_DIMENSION:
            self._blocked.append(
                ComicValidationIssue(
                    "page_too_large",
                    "ComicPagePlan page dimensions exceed the maximum supported size.",
                    "page",
                )
            )

    def _validate_panel(
        self,
        panel: Any,
        index: int,
        page_width: int | None,
        page_height: int | None,
    ) -> None:
        path = f"panels[{index}]"
        panel_map = _mapping(panel)
        panel_id = _required_id(panel_map.get("id"), path=f"{path}.id", blocked=self._blocked)
        if panel_id:
            self._register_id(panel_id, f"{path}.id")
        rect = _rect(panel_map.get("rect"), path=f"{path}.rect", blocked=self._blocked)
        if rect and page_width is not None and page_height is not None:
            self._warn_if_outside_page(rect, page_width, page_height, f"{path}.rect")

        for slot_index, art_slot in enumerate(_list(panel_map.get("artSlots"))):
            self._validate_art_slot(
                art_slot,
                panel_id or f"panel_{index}",
                f"{path}.artSlots[{slot_index}]",
            )
        for balloon_index, balloon in enumerate(_list(panel_map.get("balloons"))):
            self._validate_text_box(
                balloon,
                panel_id or f"panel_{index}",
                f"{path}.balloons[{balloon_index}]",
                "balloon",
            )
        for caption_index, caption in enumerate(_list(panel_map.get("captions"))):
            self._validate_text_box(
                caption,
                panel_id or f"panel_{index}",
                f"{path}.captions[{caption_index}]",
                "caption",
            )

    def _validate_art_slot(self, value: Any, panel_id: str, path: str) -> None:
        del panel_id
        slot = _mapping(value)
        slot_id = _required_id(slot.get("id"), path=f"{path}.id", blocked=self._blocked)
        if slot_id:
            self._register_id(slot_id, f"{path}.id")
        _rect(slot.get("rect"), path=f"{path}.rect", blocked=self._blocked)
        asset_id = slot.get("assetId")
        if (
            asset_id is not None
            and str(asset_id) not in {"", "None"}
            and str(asset_id) not in self._known_asset_ids
        ):
            self._blocked.append(
                ComicValidationIssue(
                    "unknown_asset",
                    "ComicPagePlan references an assetId that is not registered.",
                    f"{path}.assetId",
                )
            )

    def _validate_text_box(self, value: Any, panel_id: str, path: str, role: str) -> None:
        box = _mapping(value)
        text_id = _required_id(box.get("id"), path=f"{path}.id", blocked=self._blocked)
        if text_id:
            self._register_id(text_id, f"{path}.id")
        rect = _rect(box.get("rect"), path=f"{path}.rect", blocked=self._blocked)
        text = box.get("text")
        if not isinstance(text, str):
            self._blocked.append(
                ComicValidationIssue(
                    "text_required",
                    "Comic text boxes require a string text value.",
                    f"{path}.text",
                )
            )
            text_length = 0
        else:
            text_length = len(text)
            if text_length > MAX_TEXT_LENGTH:
                self._blocked.append(
                    ComicValidationIssue(
                        "text_too_long",
                        "Comic text exceeds the maximum supported length.",
                        f"{path}.text",
                    )
                )

        text_style = _mapping(box.get("textStyle"))
        requested_font_size = _optional_int(text_style.get("fontSize"))
        fit_mode = str(text_style.get("fit") or "shrink_to_fit")
        if fit_mode not in {"none", "wrap", "shrink_to_fit", "truncate"}:
            self._warnings.append(
                ComicValidationIssue(
                    "unknown_fit_mode",
                    "Unknown text fit mode; renderer will fall back to none.",
                    f"{path}.textStyle.fit",
                )
            )
            fit_mode = "none"

        if text_id and rect is not None:
            self._text_refs.append(
                ComicTextRef(
                    text_id=text_id,
                    role=role,
                    panel_id=panel_id,
                    rect=rect,
                    text=text if isinstance(text, str) else "",
                    text_length=text_length,
                    requested_font_size=requested_font_size,
                    fit_mode=fit_mode,
                    text_style=dict(text_style),
                )
            )

    def _summary(self, *, width: int | None, height: int | None) -> dict[str, Any]:
        panels = _list(self._plan.get("panels"))
        return {
            "page": {"width": width, "height": height},
            "panelCount": len(panels),
            "textBoxCount": len(self._text_refs),
            "artSlotCount": sum(len(_list(_mapping(panel).get("artSlots"))) for panel in panels),
        }

    def _validate_roots(self) -> dict[str, Any]:
        export = _mapping(self._plan.get("export"))
        output_path = export.get("outputPath")
        root_status: dict[str, Any] = {
            "roots": [str(root) for root in self._roots],
            "fileWriteRequested": output_path is not None,
            "allowed": True,
            "resolvedOutputPath": None,
            "blockedReasons": [],
        }
        if output_path is None:
            return root_status
        try:
            resolved = validate_output_path(
                str(output_path),
                roots=self._roots,
                overwrite=True,
                allowed_suffixes=(".png",),
            )
        except McpStructuredError as exc:
            code = _root_error_code(exc)
            root_status["allowed"] = False
            root_status["blockedReasons"] = [code]
            self._blocked.append(
                ComicValidationIssue(
                    code,
                    exc.message,
                    "export.outputPath",
                )
            )
            return root_status
        root_status["resolvedOutputPath"] = str(resolved)
        return root_status

    def _register_id(self, item_id: str, path: str) -> None:
        if item_id in self._ids:
            self._blocked.append(
                ComicValidationIssue(
                    "duplicate_id",
                    "ComicPagePlan ids must be unique within a page.",
                    path,
                )
            )
            return
        self._ids.add(item_id)

    def _warn_if_outside_page(
        self,
        rect: ComicRect,
        page_width: int,
        page_height: int,
        path: str,
    ) -> None:
        if (
            rect.x < 0
            or rect.y < 0
            or rect.x + rect.width > page_width
            or rect.y + rect.height > page_height
        ):
            self._warnings.append(
                ComicValidationIssue(
                    "rect_outside_page",
                    "Comic element extends outside the page bounds.",
                    path,
                )
            )


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _required_id(value: Any, *, path: str, blocked: list[ComicValidationIssue]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        blocked.append(
            ComicValidationIssue(
                "id_required",
                "ComicPagePlan elements require a non-empty id.",
                path,
            )
        )
        return None
    return value.strip()


def _bounded_int(
    value: Any,
    *,
    path: str,
    blocked: list[ComicValidationIssue],
) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        blocked.append(
            ComicValidationIssue(
                "positive_int_required",
                "ComicPagePlan page dimensions must be positive integers.",
                path,
            )
        )
        return None
    return value


def _optional_int(value: Any) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


def _rect(
    value: Any,
    *,
    path: str,
    blocked: list[ComicValidationIssue],
) -> ComicRect | None:
    rect = _mapping(value)
    values: dict[str, float] = {}
    for key in ("x", "y", "width", "height"):
        raw = rect.get(key)
        if not isinstance(raw, int | float) or isinstance(raw, bool) or not math.isfinite(raw):
            blocked.append(
                ComicValidationIssue(
                    "finite_number_required",
                    "Comic rect values must be finite numbers.",
                    f"{path}.{key}",
                )
            )
            return None
        values[key] = float(raw)
    if values["width"] <= 0 or values["height"] <= 0:
        blocked.append(
            ComicValidationIssue(
                "positive_rect_size_required",
                "Comic rect width and height must be positive.",
                path,
            )
        )
        return None
    return ComicRect(**values)


def _planned_page(
    value: Any,
    *,
    default_page: Mapping[str, Any] | None,
) -> dict[str, Any]:
    page = {**DEFAULT_PAGE, **_mapping(default_page), **_mapping(value)}
    return {
        "width": _clamped_int(
            page.get("width"),
            default=832,
            minimum=1,
            maximum=MAX_PAGE_DIMENSION,
        ),
        "height": _clamped_int(
            page.get("height"),
            default=1216,
            minimum=1,
            maximum=MAX_PAGE_DIMENSION,
        ),
        "dpi": _clamped_int(page.get("dpi"), default=72, minimum=1, maximum=2400),
        "background": str(page.get("background") or "#F7F1E6"),
    }


def _planned_asset_ids(value: Any, known_asset_ids: Sequence[str]) -> tuple[list[str], list[str]]:
    known = {str(asset_id) for asset_id in known_asset_ids}
    warnings: list[str] = []
    asset_ids: list[str] = []
    for item in value if isinstance(value, list) else []:
        asset_id = str(item or "").strip()
        if not asset_id:
            continue
        if known and asset_id not in known:
            warnings.append("unknown_asset_ids_ignored")
            continue
        asset_ids.append(asset_id)
    return asset_ids, warnings


def _script_segments(script: str, *, max_panels: int) -> list[str]:
    chunks: list[str] = []
    for line in script.splitlines():
        normalized = line.strip()
        if not normalized:
            continue
        sentence_pattern = r"[^\u3002\uff01\uff1f!?\n]+[\u3002\uff01\uff1f!?]?"
        chunks.extend(
            part.strip() for part in re.findall(sentence_pattern, normalized) if part.strip()
        )
    if not chunks:
        chunks = [script[:MAX_TEXT_LENGTH]]
    if len(chunks) <= max_panels:
        return chunks
    return [*chunks[: max_panels - 1], " ".join(chunks[max_panels - 1 :])]


def _script_panel_blocks(
    script: str,
    *,
    max_panels: int,
) -> tuple[list[_PanelBlock], list[str]]:
    """Split a script into per-panel blocks.

    Lines starting with an explicit panel marker (`\u30b3\u30de1:` / `1\u30b3\u30de\u76ee:` /
    `Panel 1:`) begin a new panel. Inside a panel, quoted spans (`\u300c\u2026\u300d` etc.)
    become balloons and the remaining prose becomes the scene description.
    Scripts without markers fall back to sentence segmentation.
    """

    lines = script.splitlines()
    marker_indices = [index for index, line in enumerate(lines) if _PANEL_MARKER.match(line)]
    if not marker_indices:
        segments = _script_segments(script, max_panels=max_panels)
        return [_PanelBlock(balloons=(segment,), description="") for segment in segments], []

    warnings: list[str] = []
    if any(line.strip() for line in lines[: marker_indices[0]]):
        warnings.append("script_preamble_ignored")

    raw_blocks: list[str] = []
    for position, start in enumerate(marker_indices):
        end = marker_indices[position + 1] if position + 1 < len(marker_indices) else len(lines)
        first_line = _PANEL_MARKER.sub("", lines[start], count=1)
        raw_blocks.append("\n".join([first_line, *lines[start + 1 : end]]).strip())

    if len(raw_blocks) > max_panels:
        warnings.append("script_panels_merged_into_max_panels")
        raw_blocks = [*raw_blocks[: max_panels - 1], "\n".join(raw_blocks[max_panels - 1 :])]

    blocks = [_parse_panel_block(raw) for raw in raw_blocks]
    return blocks, warnings


def _parse_panel_block(raw: str) -> _PanelBlock:
    balloons: list[str] = []
    for match in _QUOTED_TEXT.finditer(raw):
        quoted = next((group for group in match.groups() if group), "").strip()
        if quoted:
            balloons.append(quoted)
    if len(balloons) > MAX_BALLOONS_PER_PANEL:
        merged = " ".join(balloons[MAX_BALLOONS_PER_PANEL - 1 :])
        balloons = [*balloons[: MAX_BALLOONS_PER_PANEL - 1], merged]
    description = _QUOTED_TEXT.sub("", raw)
    description = _NARRATION_LABEL.sub("", description)
    description = " ".join(
        segment.strip(" \t\u3001\u3002,.:\uff1a;\u30fb-\u2014")
        for segment in description.splitlines()
        if segment.strip(" \t\u3001\u3002,.:\uff1a;\u30fb-\u2014")
    ).strip()
    if not balloons:
        text = description or raw.strip()
        return _PanelBlock(balloons=(text[:MAX_TEXT_LENGTH],), description="")
    return _PanelBlock(
        balloons=tuple(balloon[:MAX_TEXT_LENGTH] for balloon in balloons),
        description=description[:MAX_DESCRIPTION_LENGTH],
    )


def _planned_panels(
    page: Mapping[str, Any],
    blocks: Sequence[_PanelBlock],
    *,
    asset_ids: Sequence[str],
    safe_margin: int,
    min_font_size: int,
) -> list[dict[str, Any]]:
    width = int(page["width"])
    height = int(page["height"])
    panel_count = max(1, len(blocks))
    margin = min(safe_margin, max(8, width // 5), max(8, height // 5))
    gutter = max(8, min(24, height // 80))
    panel_width = max(1, width - (margin * 2))
    available_height = max(1, height - (margin * 2) - (gutter * (panel_count - 1)))
    panel_height = max(1, available_height / panel_count)
    panels: list[dict[str, Any]] = []
    for index, block in enumerate(blocks):
        x = float(margin)
        y = float(margin + index * (panel_height + gutter))
        rect = {"x": x, "y": y, "width": float(panel_width), "height": float(panel_height)}
        inset = max(8.0, min(24.0, panel_width * 0.04))
        art_width = max(1.0, min(panel_width * 0.36, panel_height * 0.7))
        art_rect = {
            "x": x + inset,
            "y": y + inset,
            "width": max(1.0, art_width),
            "height": max(1.0, panel_height - inset * 2),
        }
        column_x = art_rect["x"] + art_rect["width"] + inset
        column_width = max(1.0, x + panel_width - (art_rect["x"] + art_rect["width"]) - inset * 2)
        column_height = max(1.0, panel_height - inset * 2)
        balloon_count = max(1, len(block.balloons))
        balloon_gap = min(12.0, inset)
        balloon_height = max(
            1.0,
            (column_height - balloon_gap * (balloon_count - 1)) / balloon_count,
        )
        balloons = []
        for balloon_index, balloon_text in enumerate(block.balloons):
            balloon_id = (
                f"balloon_{index + 1}"
                if balloon_count == 1
                else f"balloon_{index + 1}_{balloon_index + 1}"
            )
            balloons.append(
                {
                    "id": balloon_id,
                    "kind": "speech_round",
                    "rect": {
                        "x": column_x,
                        "y": y + inset + balloon_index * (balloon_height + balloon_gap),
                        "width": column_width,
                        "height": balloon_height,
                    },
                    "text": balloon_text[:MAX_TEXT_LENGTH],
                    "textStyle": {
                        "fontSize": max(min_font_size, 24),
                        "minFontSize": min_font_size,
                        "fit": "shrink_to_fit",
                    },
                }
            )
        asset_id = asset_ids[index] if index < len(asset_ids) else None
        panels.append(
            {
                "id": f"panel_{index + 1}",
                "rect": rect,
                "background": "#FFFDF8",
                "borderRadius": 4,
                "artSlots": [
                    {
                        "id": f"art_{index + 1}",
                        "assetId": asset_id,
                        "rect": art_rect,
                        "placeholder": {
                            "label": block.description or f"Scene {index + 1}",
                            "fillColor": "#DDEBFF",
                        },
                    }
                ],
                "balloons": balloons,
                "captions": [],
            }
        )
    return panels


def _clamped_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return max(minimum, min(maximum, value))


def _root_error_code(exc: McpStructuredError) -> str:
    details = exc.details
    if details.get("policy") == "roots_required":
        return "roots_required"
    if exc.reason_code == ReasonCode.INVALID_ARGUMENT.value:
        return "invalid_output_path"
    return "output_path_outside_roots"


__all__ = [
    "SCHEMA_VERSION",
    "ComicPagePlanValidation",
    "ComicRect",
    "ComicTextRef",
    "ComicValidationIssue",
    "plan_comic_page",
    "validate_comic_page_plan",
]
