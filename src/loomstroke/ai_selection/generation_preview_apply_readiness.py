"""Read-only readiness checks for applying SAM3 preview generation records."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

SAM3_PREVIEW_APPLY_READINESS_SCHEMA_VERSION = "sam3.preview_apply_readiness.v1"

_SAM3_SERVICE_ID = "sam3_ai_select"
_PREVIEW_OPERATION = "ai_effect_preview_submit"
_PREVIEW_SOURCE = "sam3.ai_select.preview_generation_submit"


@dataclass(frozen=True, slots=True)
class Sam3PreviewApplyReadiness:
    """Secret-free summary of whether a SAM3 preview record can be applied."""

    ready: bool
    blocked_reason_codes: tuple[str, ...]
    history_job_id: str | None
    output_asset_ids: tuple[str, ...]
    image_path_available: bool
    generation_diff_qa_status: str | None
    generation_diff_qa_ok: bool | None
    recommended_actions: tuple[str, ...]
    schema_version: str = SAM3_PREVIEW_APPLY_READINESS_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        generation_diff_qa: dict[str, Any] | None = None
        if self.generation_diff_qa_status is not None or self.generation_diff_qa_ok is not None:
            generation_diff_qa = {
                "status": self.generation_diff_qa_status,
                "ok": self.generation_diff_qa_ok,
            }
        return {
            "schemaVersion": self.schema_version,
            "ready": self.ready,
            "blockedReasonCodes": list(self.blocked_reason_codes),
            "historyJobId": self.history_job_id,
            "outputAssetIds": list(self.output_asset_ids),
            "imagePathAvailable": self.image_path_available,
            "generationDiffQA": generation_diff_qa,
            "recommendedActions": list(self.recommended_actions),
        }


def analyze_preview_apply_readiness(
    record: object,
    *,
    require_generation_diff_qa_ok: bool = True,
) -> Sam3PreviewApplyReadiness:
    """Inspect a SAM3 preview generation history record without mutating it."""

    history_job_id = _optional_text(getattr(record, "job_id", None))
    sam3_snapshot = _sam3_snapshot(record)
    if not _is_sam3_preview_submit_record(record, sam3_snapshot):
        return _readiness(
            ready=False,
            blocked_reason_codes=("sam3_preview_history_record_invalid",),
            history_job_id=history_job_id,
            output_asset_ids=(),
            image_path_available=False,
            generation_diff_qa_status=None,
            generation_diff_qa_ok=None,
            recommended_actions=("use_sam3_preview_generation_history_record",),
        )

    output_asset_ids = _preview_output_asset_ids(record, sam3_snapshot)
    image_path_available = _optional_text(getattr(record, "image_path", None)) is not None
    qa_state = _generation_diff_qa_state(sam3_snapshot)

    blocked_reason_codes: list[str] = []
    if not image_path_available:
        blocked_reason_codes.append(
            "preview_output_image_path_missing" if output_asset_ids else "preview_output_missing"
        )

    if require_generation_diff_qa_ok:
        if qa_state.state == "missing":
            blocked_reason_codes.append("generation_diff_qa_missing")
        elif qa_state.state == "invalid":
            blocked_reason_codes.append("generation_diff_qa_invalid")
        elif qa_state.ok is False:
            blocked_reason_codes.append("generation_diff_qa_warning")

    blocked = tuple(dict.fromkeys(blocked_reason_codes))
    return _readiness(
        ready=not blocked,
        blocked_reason_codes=blocked,
        history_job_id=history_job_id,
        output_asset_ids=output_asset_ids,
        image_path_available=image_path_available,
        generation_diff_qa_status=qa_state.status,
        generation_diff_qa_ok=qa_state.ok,
        recommended_actions=_recommended_actions(blocked, qa_state.recommended_actions),
    )


@dataclass(frozen=True, slots=True)
class _GenerationDiffQAState:
    state: str
    status: str | None
    ok: bool | None
    recommended_actions: tuple[str, ...]


def _readiness(
    *,
    ready: bool,
    blocked_reason_codes: tuple[str, ...],
    history_job_id: str | None,
    output_asset_ids: tuple[str, ...],
    image_path_available: bool,
    generation_diff_qa_status: str | None,
    generation_diff_qa_ok: bool | None,
    recommended_actions: tuple[str, ...],
) -> Sam3PreviewApplyReadiness:
    return Sam3PreviewApplyReadiness(
        ready=ready,
        blocked_reason_codes=blocked_reason_codes,
        history_job_id=history_job_id,
        output_asset_ids=output_asset_ids,
        image_path_available=image_path_available,
        generation_diff_qa_status=generation_diff_qa_status,
        generation_diff_qa_ok=generation_diff_qa_ok,
        recommended_actions=recommended_actions,
    )


def _sam3_snapshot(record: object) -> Mapping[str, Any] | None:
    snapshot = _mapping(getattr(record, "service_snapshot", None))
    if snapshot is None:
        return None
    return _mapping(snapshot.get(_SAM3_SERVICE_ID))


def _is_sam3_preview_submit_record(
    record: object,
    sam3_snapshot: Mapping[str, Any] | None,
) -> bool:
    return (
        _optional_text(getattr(record, "service_id", None)) == _SAM3_SERVICE_ID
        and sam3_snapshot is not None
        and _optional_text(sam3_snapshot.get("operation")) == _PREVIEW_OPERATION
        and _optional_text(sam3_snapshot.get("source")) == _PREVIEW_SOURCE
    )


def _preview_output_asset_ids(
    record: object,
    sam3_snapshot: Mapping[str, Any],
) -> tuple[str, ...]:
    asset_ids: list[str] = []
    for image in _iter_mappings(getattr(record, "output_images", ())):
        _append_unique_asset_id(asset_ids, image)

    job = _mapping(sam3_snapshot.get("job")) or {}
    for asset_id in _string_iterable(job.get("resultAssetIds")):
        _append_unique(asset_ids, asset_id)
    for asset in _iter_mappings(job.get("assets")):
        _append_unique_asset_id(asset_ids, asset)
    return tuple(asset_ids)


def _append_unique_asset_id(asset_ids: list[str], value: Mapping[str, Any]) -> None:
    for key in ("asset_id", "assetId", "id"):
        asset_id = _optional_text(value.get(key))
        if asset_id is not None:
            _append_unique(asset_ids, asset_id)
            return


def _generation_diff_qa_state(
    sam3_snapshot: Mapping[str, Any],
) -> _GenerationDiffQAState:
    qa = _mapping(sam3_snapshot.get("generation_diff_qa"))
    if qa is None:
        return _GenerationDiffQAState(
            state="missing",
            status=None,
            ok=None,
            recommended_actions=(),
        )
    actions = tuple(_string_iterable(qa.get("recommended_actions") or qa.get("recommendedActions")))
    status = _optional_text(qa.get("status"))
    ok = qa.get("ok")
    if not isinstance(ok, bool):
        return _GenerationDiffQAState(
            state="invalid",
            status=status,
            ok=None,
            recommended_actions=actions,
        )
    return _GenerationDiffQAState(
        state="valid",
        status=status or ("passed" if ok else "warning"),
        ok=ok,
        recommended_actions=actions,
    )


def _recommended_actions(
    blocked_reason_codes: tuple[str, ...],
    qa_actions: tuple[str, ...],
) -> tuple[str, ...]:
    actions: list[str] = []
    for code in blocked_reason_codes:
        if code == "sam3_preview_history_record_invalid":
            _append_unique(actions, "use_sam3_preview_generation_history_record")
        elif code == "preview_output_missing":
            _append_unique(actions, "rerun_sam3_preview_generation")
        elif code == "preview_output_image_path_missing":
            _append_unique(actions, "materialize_preview_output_asset")
        elif code in {"generation_diff_qa_missing", "generation_diff_qa_invalid"}:
            _append_unique(actions, "run_sam3_generation_diff_qa")
        elif code == "generation_diff_qa_warning":
            if qa_actions:
                for action in qa_actions:
                    _append_unique(actions, action)
            else:
                _append_unique(actions, "review_generation_diff_warning")
    return tuple(actions)


def _append_unique(values: list[str], value: str) -> None:
    if value not in values:
        values.append(value)


def _iter_mappings(value: object) -> Iterable[Mapping[str, Any]]:
    if not isinstance(value, Iterable) or isinstance(value, (str, bytes, bytearray, Mapping)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _string_iterable(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return ()
    result: list[str] = []
    for item in value:
        text = _optional_text(item)
        if text is not None:
            result.append(text)
    return tuple(result)


def _mapping(value: object) -> Mapping[str, Any] | None:
    return value if isinstance(value, Mapping) else None


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "SAM3_PREVIEW_APPLY_READINESS_SCHEMA_VERSION",
    "Sam3PreviewApplyReadiness",
    "analyze_preview_apply_readiness",
]
