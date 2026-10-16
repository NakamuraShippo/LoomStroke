"""Selection apply plan adapter for SAM3 Manga Mask Assistant rows."""

from __future__ import annotations

from .data_model import BlockedReason, Sam3Warning, SourceSnapshotRef
from .manga_mask_assistant_model import (
    Sam3MangaMaskAssistantRow,
    build_manga_mask_assistant_rows,
)
from .manga_mask_package import Sam3ComicMaskPackage
from .selection_bridge import (
    Sam3SelectionApplyPlan,
    Sam3SelectionBridgeError,
    SelectionApplyOperation,
    normalize_selection_apply_operation,
)

_SNAPSHOT_ID_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz" "ABCDEFGHIJKLMNOPQRSTUVWXYZ" "0123456789" "._-"
)


class Sam3MangaMaskSelectionAdapterError(ValueError):
    """Raised when a Manga Mask Assistant row cannot be adapted."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def build_manga_mask_selection_apply_plan(
    package: Sam3ComicMaskPackage,
    row_id: str,
    *,
    source_width: int,
    source_height: int,
    operation: str = "replace",
    source_snapshot_id: str | None = None,
) -> Sam3SelectionApplyPlan:
    """Build a read-only selection apply plan for one Manga Mask Assistant row."""

    if not isinstance(package, Sam3ComicMaskPackage):
        raise Sam3MangaMaskSelectionAdapterError(
            "manga_mask_selection_package_invalid",
            "Manga mask selection plans require a comic mask package.",
        )

    width = _positive_int(source_width)
    height = _positive_int(source_height)
    source_ref = _source_ref(package, width or 0, height or 0, source_snapshot_id)
    blocked_reasons: list[BlockedReason] = []
    warnings: list[Sam3Warning] = []
    normalized_operation: SelectionApplyOperation = "replace"

    if width is None or height is None:
        blocked_reasons.append(
            _blocked_reason(
                "manga_mask_selection_source_size_invalid",
                "Manga mask selection apply requires a positive source size.",
                "Refresh the current canvas size before applying the mask.",
            )
        )

    try:
        normalized_operation = normalize_selection_apply_operation(operation)
    except Sam3SelectionBridgeError:
        blocked_reasons.append(
            _blocked_reason(
                "invalid_apply_operation",
                "Manga mask selection apply operation is invalid.",
                "Use replace, add, subtract, or intersect.",
            )
        )

    row = _find_row(package, row_id)
    if row is None:
        blocked_reasons.append(
            _blocked_reason(
                "manga_mask_assistant_action_target_missing",
                "Manga Mask Assistant row is not available in the current package.",
                "Refresh the Manga Mask Assistant package before applying the row.",
            )
        )
        return _plan(
            package=package,
            source_ref=source_ref,
            candidate_id="",
            mask_asset_id=None,
            operation=normalized_operation,
            selection_bounds=None,
            blocked_reasons=blocked_reasons,
            warnings=warnings,
        )

    mask_asset_id = str(row.mask_asset_id or "").strip() or None
    if mask_asset_id is None:
        blocked_reasons.append(
            _blocked_reason(
                "candidate_mask_missing",
                "Manga Mask Assistant row does not reference a mask asset.",
                "Regenerate the manga mask package before applying the row.",
            )
        )

    selection_bounds: tuple[int, int, int, int] | None = None
    if width is not None and height is not None:
        selection_bounds, clipped = _clip_bbox(row.bbox, width, height)
        if selection_bounds is None:
            blocked_reasons.append(
                _blocked_reason(
                    "candidate_bounds_invalid",
                    "Manga Mask Assistant row bounds do not intersect the source canvas.",
                    "Inspect the mask package before applying the row.",
                )
            )
        elif clipped:
            warnings.append(
                Sam3Warning(
                    code="candidate_bounds_clipped",
                    message="Manga Mask Assistant row bounds were clipped to the source canvas.",
                    severity="warning",
                    action="Inspect the candidate preview before applying.",
                )
            )

    return _plan(
        package=package,
        source_ref=source_ref,
        candidate_id=row.candidate_id,
        mask_asset_id=mask_asset_id,
        operation=normalized_operation,
        selection_bounds=selection_bounds,
        blocked_reasons=blocked_reasons,
        warnings=warnings,
    )


def build_protect_mask_selection_apply_plan(
    package: Sam3ComicMaskPackage,
    protected_asset: object,
    *,
    source_width: int,
    source_height: int,
) -> Sam3SelectionApplyPlan:
    """Build a selection apply plan for a saved speech-bubble protect mask.

    The operation is fixed to ``replace`` (protect visualization), and the
    plan's candidate id mirrors the asset's ``source_candidate_id`` so the
    command adapter's asset/candidate consistency check passes.
    """

    if not isinstance(package, Sam3ComicMaskPackage):
        raise Sam3MangaMaskSelectionAdapterError(
            "manga_mask_selection_package_invalid",
            "Manga mask selection plans require a comic mask package.",
        )

    width = _positive_int(source_width)
    height = _positive_int(source_height)
    source_ref = _source_ref(package, width or 0, height or 0, None)
    blocked_reasons: list[BlockedReason] = []
    warnings: list[Sam3Warning] = []

    if width is None or height is None:
        blocked_reasons.append(
            _blocked_reason(
                "manga_mask_selection_source_size_invalid",
                "Manga mask selection apply requires a positive source size.",
                "Refresh the current canvas size before applying the mask.",
            )
        )

    mask_asset_id = str(getattr(protected_asset, "asset_id", "") or "").strip() or None
    if mask_asset_id is None:
        blocked_reasons.append(
            _blocked_reason(
                "candidate_mask_missing",
                "Protect mask asset does not have an asset id.",
                "Regenerate the protect mask before applying it.",
            )
        )
    candidate_id = str(getattr(protected_asset, "source_candidate_id", "") or "").strip()
    if not candidate_id:
        blocked_reasons.append(
            _blocked_reason(
                "manga_mask_protect_candidate_missing",
                "Protect mask asset does not reference a source candidate.",
                "Regenerate the protect mask before applying it.",
            )
        )

    selection_bounds: tuple[int, int, int, int] | None = None
    if width is not None and height is not None:
        bbox = getattr(protected_asset, "bbox", None)
        if bbox is not None:
            selection_bounds, clipped = _clip_bbox(tuple(bbox), width, height)
        else:
            selection_bounds, clipped = (None, False)
        if selection_bounds is None:
            blocked_reasons.append(
                _blocked_reason(
                    "candidate_bounds_invalid",
                    "Protect mask bounds do not intersect the source canvas.",
                    "Regenerate the protect mask before applying it.",
                )
            )
        elif clipped:
            warnings.append(
                Sam3Warning(
                    code="candidate_bounds_clipped",
                    message="Protect mask bounds were clipped to the source canvas.",
                    severity="warning",
                    action="Inspect the protect mask before applying.",
                )
            )

    return _plan(
        package=package,
        source_ref=source_ref,
        candidate_id=candidate_id,
        mask_asset_id=mask_asset_id,
        operation="replace",
        selection_bounds=selection_bounds,
        blocked_reasons=blocked_reasons,
        warnings=warnings,
    )


def _find_row(
    package: Sam3ComicMaskPackage,
    row_id: object,
) -> Sam3MangaMaskAssistantRow | None:
    normalized_row_id = str(row_id or "").strip()
    if not normalized_row_id:
        return None
    for row in build_manga_mask_assistant_rows(package):
        if row.row_id == normalized_row_id:
            return row
    return None


def _plan(
    *,
    package: Sam3ComicMaskPackage,
    source_ref: SourceSnapshotRef,
    candidate_id: str,
    mask_asset_id: str | None,
    operation: SelectionApplyOperation,
    selection_bounds: tuple[int, int, int, int] | None,
    blocked_reasons: list[BlockedReason],
    warnings: list[Sam3Warning],
) -> Sam3SelectionApplyPlan:
    return Sam3SelectionApplyPlan(
        candidate_set_id=package.package_id,
        candidate_id=candidate_id,
        mask_asset_id=mask_asset_id,
        operation=operation,
        source_ref=source_ref,
        selection_bounds=selection_bounds,
        status="blocked" if blocked_reasons else "ready",
        blocked_reasons=tuple(blocked_reasons),
        warnings=tuple(warnings),
    )


def _source_ref(
    package: Sam3ComicMaskPackage,
    width: int,
    height: int,
    source_snapshot_id: str | None,
) -> SourceSnapshotRef:
    return SourceSnapshotRef(
        snapshot_id=_snapshot_id(package, source_snapshot_id),
        kind="manga_mask_package",
        width=width,
        height=height,
        document_id=package.source_document_id,
        page_id=package.source_page_id,
    )


def _snapshot_id(package: Sam3ComicMaskPackage, source_snapshot_id: str | None) -> str:
    explicit_id = str(source_snapshot_id or "").strip()
    if explicit_id:
        return _safe_snapshot_id(explicit_id)
    package_id = _safe_snapshot_id(package.package_id)
    page_id = _safe_snapshot_id(package.source_page_id)
    return f"manga-mask-{package_id}-{page_id}"


def _safe_snapshot_id(value: object) -> str:
    text = str(value or "").strip()
    safe = "".join(char if char in _SNAPSHOT_ID_CHARS else "_" for char in text)
    return safe.strip("._-") or "unknown"


def _positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        number = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def _clip_bbox(
    bbox: tuple[int, int, int, int],
    source_width: int,
    source_height: int,
) -> tuple[tuple[int, int, int, int] | None, bool]:
    x, y, width, height = (int(item) for item in bbox)
    if width <= 0 or height <= 0:
        return (None, False)

    left = max(0, x)
    top = max(0, y)
    right = min(source_width, x + width)
    bottom = min(source_height, y + height)
    if right <= left or bottom <= top:
        return (None, False)

    clipped_bbox = (left, top, right - left, bottom - top)
    return (clipped_bbox, clipped_bbox != (x, y, width, height))


def _blocked_reason(code: str, message: str, action: str) -> BlockedReason:
    return BlockedReason(code=code, message=message, action=action)


__all__ = [
    "Sam3MangaMaskSelectionAdapterError",
    "build_manga_mask_selection_apply_plan",
    "build_protect_mask_selection_apply_plan",
]
