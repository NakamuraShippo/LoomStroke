"""Production Control Tower helpers for LoomStroke MCP.

The Production Control Tower (PCT) intentionally lives outside the ``.lstroke``
save format.  Its store is JSON-only metadata under ``user_data`` and all
canvas-affecting work must enter through MCP confirmation plus a later
Command-backed service boundary.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from PySide6.QtGui import QImage

from loomstroke.platform.user_dirs import default_user_root

from .generation_capabilities import generation_submitter_capabilities
from .production_record_io import read_checked_records

PCT_TOOL_SCHEMA_VERSION = "pct.tool.v1"
PCT_STORE_SCHEMA_VERSION = "pct.store.v1"

_STORE_DIR_NAME = "production_control"
_SECRET_KEY_PARTS = (
    "api_key",
    "apikey",
    "authorization",
    "bearer",
    "cookie",
    "credential",
    "endpoint",
    "header",
    "password",
    "private",
    "secret",
    "session",
    "token",
    "url",
)
_SAFE_SECURITY_METADATA_KEYS = frozenset(
    {
        "credentialpolicy",
        "credentialreadperformed",
        "credentialrequired",
        "credentialstatus",
        "credentialvaluesincluded",
    }
)
_EXTERNAL_GATEWAYS = frozenset({"comfyui", "nanobanana", "gptimage2"})
_FIXED_PARAMETER_GATEWAYS = frozenset({"nanobanana", "gptimage2"})
_WORKFLOW_BACKED_GATEWAYS = frozenset({"comfyui"})
_BIBLE_SCOPE_FILES = {
    "project": "project.json",
    "style": "styles.jsonl",
    "character": "characters.jsonl",
    "asset": "assets.jsonl",
    "export_target": "export_targets.jsonl",
}


def pct_response(
    data: Mapping[str, Any] | None = None,
    *,
    warnings: Sequence[str] | None = None,
    blocked: bool = False,
    blocked_reasons: Sequence[str] | None = None,
    requires_confirmation: bool = False,
    confirmation_summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the common PCT tool envelope."""

    return {
        "schemaVersion": PCT_TOOL_SCHEMA_VERSION,
        "ok": not blocked,
        "data": scrub_for_pct(dict(data or {})),
        "warnings": list(warnings or ()),
        "blocked": bool(blocked),
        "blockedReasons": list(blocked_reasons or ()),
        "requiresConfirmation": bool(requires_confirmation),
        "confirmationSummary": (
            scrub_for_pct(dict(confirmation_summary)) if confirmation_summary else None
        ),
    }


def scrub_for_pct(value: Any) -> Any:
    """Scrub credentials, private endpoints, request headers, and bulky bytes."""

    return _scrub_pct_value(value)


def contains_sensitive_fields(value: Any) -> bool:
    """Return True when a value contains fields disallowed in PCT persistence."""

    if isinstance(value, Mapping):
        for key, item in value.items():
            if _is_sensitive_key(str(key)):
                return True
            if contains_sensitive_fields(item):
                return True
    elif isinstance(value, list | tuple):
        return any(contains_sensitive_fields(item) for item in value)
    return False


def _replace_with_retry(tmp: Path, path: Path, *, attempts: int = 5) -> None:
    """os.replace with short retries for Windows transient locks.

    Antivirus / indexer scans can hold a fresh file handle for a moment,
    turning the atomic replace into PermissionError (WinError 5) or a
    sharing violation (WinError 32). Retrying briefly is the standard
    mitigation; the final attempt propagates the error unchanged.
    """

    for attempt in range(attempts):
        try:
            os.replace(str(tmp), str(path))
            return
        except (PermissionError, OSError):
            if attempt == attempts - 1:
                raise
            time.sleep(0.05 * (attempt + 1))


class ProductionControlStore:
    """Small JSON store rooted at ``user_data/production_control``."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = Path(root) if root is not None else default_user_root() / _STORE_DIR_NAME

    def ensure(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "bible").mkdir(exist_ok=True)
        (self.root / "qa").mkdir(exist_ok=True)
        (self.root / "recipes").mkdir(exist_ok=True)
        self._write_json_if_missing(
            self.root / "store_meta.json",
            {
                "schemaVersion": PCT_STORE_SCHEMA_VERSION,
                "createdAt": _now_iso(),
                "storage": "user_data/production_control",
            },
        )

    def describe(self) -> dict[str, Any]:
        self.ensure()
        return {
            "schemaVersion": PCT_STORE_SCHEMA_VERSION,
            "root": str(self.root),
            "storage": "user_data/production_control",
            "artifacts": len(self.read_records("artifacts")),
            "edges": len(self.read_records("edges")),
            "jobs": len(self.read_records("jobs")),
            "changesets": len(self.read_records("changesets")),
            "recipes": len(self.list_recipes()),
        }

    def read_records(self, name: str) -> list[dict[str, Any]]:
        self.ensure()
        return self._read_json_list(self.root / f"{name}.json")

    def write_records(self, name: str, records: Iterable[Mapping[str, Any]]) -> None:
        self.ensure()
        self._write_json_list(self.root / f"{name}.json", [dict(item) for item in records])

    def _read_records_checked(self, name: str) -> list[dict[str, Any]]:
        self.ensure()
        return read_checked_records(self.root / f"{name}.json")

    def _read_bible_entries_checked(self, scope: str) -> list[dict[str, Any]]:
        normalized = _normalize_bible_scope(scope)
        return read_checked_records(
            self._bible_path(normalized),
            container_key="entries" if normalized == "project" else None,
            jsonl=normalized != "project",
        )

    def upsert_records(
        self,
        name: str,
        records: Iterable[Mapping[str, Any]],
        *,
        key: str,
    ) -> list[dict[str, Any]]:
        existing = {str(item.get(key)): dict(item) for item in self.read_records(name)}
        for record in records:
            record_dict = dict(record)
            record_id = str(record_dict.get(key) or "")
            if record_id:
                existing[record_id] = record_dict
        merged = sorted(existing.values(), key=lambda item: str(item.get(key) or ""))
        self.write_records(name, merged)
        return merged

    def get_record(self, name: str, key: str, value: str) -> dict[str, Any] | None:
        for record in self.read_records(name):
            if str(record.get(key) or "") == value:
                return dict(record)
        return None

    def add_job(self, job: Mapping[str, Any]) -> dict[str, Any]:
        self.upsert_records("jobs", [job], key="jobId")
        return dict(job)

    def add_decision(self, decision: Mapping[str, Any]) -> dict[str, Any]:
        self.upsert_records("decisions", [decision], key="decisionId")
        return dict(decision)

    def add_changeset(self, changeset: Mapping[str, Any]) -> dict[str, Any]:
        self.upsert_records("changesets", [changeset], key="changesetId")
        return dict(changeset)

    def update_changeset(self, changeset: Mapping[str, Any]) -> dict[str, Any]:
        return self.add_changeset(changeset)

    def list_bible_entries(self, scope: str, scope_id: str | None = None) -> list[dict[str, Any]]:
        normalized = _normalize_bible_scope(scope)
        path = self._bible_path(normalized)
        entries = (
            self._read_json_list(path, container_key="entries")
            if normalized == "project"
            else self._read_jsonl(path)
        )
        if scope_id is not None:
            entries = [
                entry
                for entry in entries
                if str(entry.get("scopeId") or entry.get("id") or "") == scope_id
            ]
        return entries

    def add_bible_entry(self, entry: Mapping[str, Any]) -> dict[str, Any]:
        record = dict(entry)
        scope = _normalize_bible_scope(str(record.get("scope") or "project"))
        entries = self.list_bible_entries(scope)
        entries = [item for item in entries if item.get("entryId") != record.get("entryId")]
        entries.append(record)
        entries.sort(key=lambda item: str(item.get("entryId") or ""))
        self._write_bible_entries(scope, entries)
        return record

    def remove_bible_entry(self, scope: str, entry_id: str) -> bool:
        normalized = _normalize_bible_scope(scope)
        entries = self.list_bible_entries(normalized)
        remaining = [entry for entry in entries if str(entry.get("entryId") or "") != entry_id]
        if len(remaining) == len(entries):
            return False
        self._write_bible_entries(normalized, remaining)
        return True

    def _write_bible_entries(self, scope: str, entries: Sequence[Mapping[str, Any]]) -> None:
        normalized = _normalize_bible_scope(scope)
        path = self._bible_path(normalized)
        if normalized == "project":
            self._write_json(
                path,
                {"schemaVersion": "pct.bible.v1", "scope": normalized, "entries": entries},
            )
        else:
            self._write_jsonl(path, entries)

    def write_qa_report(self, artifact_id: str, report: Mapping[str, Any]) -> None:
        self.ensure()
        safe_id = _safe_filename(artifact_id)
        self._write_json(self.root / "qa" / f"{safe_id}.json", dict(report))

    def read_qa_report(self, artifact_id: str) -> dict[str, Any] | None:
        self.ensure()
        path = self.root / "qa" / f"{_safe_filename(artifact_id)}.json"
        if not path.exists():
            return None
        value = self._read_json(path, default=None)
        return value if isinstance(value, dict) else None

    def save_recipe(self, recipe: Mapping[str, Any]) -> dict[str, Any]:
        self.ensure()
        record = dict(recipe)
        recipe_id = str(record.get("recipeId") or _stable_id("pct_recipe", record))
        record["recipeId"] = recipe_id
        target = self.root / "recipes" / f"{_safe_filename(recipe_id)}.lsrecipe.json"
        self._write_json(target, record)
        self.upsert_records(
            "recipes",
            [
                {
                    "recipeId": recipe_id,
                    "name": record.get("name") or recipe_id,
                    "path": str(target),
                    "updatedAt": _now_iso(),
                }
            ],
            key="recipeId",
        )
        return record

    def get_recipe(self, recipe_id: str) -> dict[str, Any] | None:
        self.ensure()
        path = self.root / "recipes" / f"{_safe_filename(recipe_id)}.lsrecipe.json"
        if not path.exists():
            return None
        value = self._read_json(path, default=None)
        return value if isinstance(value, dict) else None

    def list_recipes(self) -> list[dict[str, Any]]:
        return self.read_records("recipes")

    def _bible_path(self, scope: str) -> Path:
        self.ensure()
        return self.root / "bible" / _BIBLE_SCOPE_FILES[scope]

    @staticmethod
    def _read_json(path: Path, *, default: Any) -> Any:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    def _read_json_list(
        self,
        path: Path,
        *,
        container_key: str | None = None,
    ) -> list[dict[str, Any]]:
        value = self._read_json(path, default=[] if container_key is None else {})
        if container_key is not None and isinstance(value, Mapping):
            value = value.get(container_key, [])
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, Mapping)]

    @staticmethod
    def _read_jsonl(path: Path) -> list[dict[str, Any]]:
        if not path.exists():
            return []
        records: list[dict[str, Any]] = []
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                item = json.loads(line)
                if isinstance(item, Mapping):
                    records.append(dict(item))
        except Exception:
            return []
        return records

    def _write_json_list(self, path: Path, records: Sequence[Mapping[str, Any]]) -> None:
        self._write_json(path, [scrub_for_pct(dict(record)) for record in records])

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        safe_payload = scrub_for_pct(payload)
        tmp.write_text(json.dumps(safe_payload, ensure_ascii=False, indent=2), encoding="utf-8")
        _replace_with_retry(tmp, path)

    def _write_jsonl(self, path: Path, records: Sequence[Mapping[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        lines = [
            json.dumps(scrub_for_pct(dict(record)), ensure_ascii=False, sort_keys=True)
            for record in records
        ]
        tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
        _replace_with_retry(tmp, path)

    def _write_json_if_missing(self, path: Path, payload: Mapping[str, Any]) -> None:
        if not path.exists():
            self._write_json(path, dict(payload))


def build_feature_catalog(service: Any) -> dict[str, Any]:
    """Return a static-plus-runtime feature catalog for MCP clients."""

    external_allowed = bool(getattr(service.safety, "external_services_allowed", False))
    roots = [str(root) for root in getattr(service.safety, "roots", ())]
    has_live_canvas = getattr(service, "canvas_service", None) is not None
    generation_capabilities = generation_submitter_capabilities(service)
    generation_ready = generation_capabilities["available"] and external_allowed
    store = getattr(service, "_production_store", None)
    store_summary = store.describe() if store is not None else {"root": None}
    features = [
        _feature(
            "feature_catalog",
            "Feature Catalog",
            "Machine-readable MCP capabilities, tools, resources, prompts, and safety notes.",
            tools=["loomstroke_get_feature_catalog", "loomstroke_explain_feature"],
            resources=["loomstroke://production/capabilities"],
            prompts=["loomstroke_graphic_plan"],
            status="available",
            safety=["read_only"],
        ),
        _feature(
            "ai_effect_catalog",
            "AI Effect Catalog",
            "Installed AI Effect preset catalog across ComfyUI, NanoBanana, and GPT Image 2.",
            tools=[
                "loomstroke_get_ai_effect_catalog",
                "loomstroke_get_ai_effect_preset_detail",
            ],
            resources=["loomstroke://ai-effects/catalog", "loomstroke://ai-effects/preset/{id}"],
            status="available",
            safety=["credential_free", "no_external_call"],
        ),
        _feature(
            "production_graph",
            "Production Graph Projection",
            "Idempotent projection from GenerationHistory to ProductionArtifact and edge records.",
            tools=["loomstroke_get_production_lineage", "loomstroke_mark_artifact_decision"],
            resources=[
                "loomstroke://production/artifacts",
                "loomstroke://production/artifact/{id}",
                "loomstroke://production/lineage/{id}",
            ],
            status="available",
            safety=["credential_free", "store_outside_lstroke"],
        ),
        _feature(
            "visual_qa_preview",
            "Visual QA Preview",
            "Read-only visual artifact checks for alpha garbage, halo, and size mismatch.",
            tools=["loomstroke_run_visual_qa_preview"],
            resources=["loomstroke://production/qa/{artifact_id}"],
            status="available",
            safety=["read_only", "no_canvas_mutation"],
        ),
        _feature(
            "asset_import",
            "Asset Import",
            "Register rooted PNG/JPEG/WebP files as MCP raster assets for preview and QA tools.",
            tools=["loomstroke_import_asset", "loomstroke_list_assets"],
            resources=["loomstroke://asset/{id}"],
            status="available" if roots else "blocked_until_roots_configured",
            safety=[
                "roots_required_for_file_read",
                "no_canvas_mutation",
                "normalizes_to_png_payload",
                "credential_free",
            ],
        ),
        _feature(
            "asset_overlay_preview",
            "Asset Overlay Preview",
            (
                "Create a rooted PNG preview with text, rectangle, or manga speech bubble "
                "overlays on an MCP asset, including assets imported from roots."
            ),
            tools=[
                "loomstroke_import_asset",
                "loomstroke_create_asset_overlay_preview",
                "loomstroke_create_speech_bubble_overlay",
            ],
            resources=["loomstroke://asset/{id}"],
            status="available" if roots else "blocked_until_roots_configured",
            safety=[
                "roots_required_for_file_write",
                "confirmation_required_for_write",
                "no_canvas_mutation",
                "response_metadata_omits_overlay_text",
                "text_fit_report_omits_text_body",
                "speech_bubble_overlay_high_level",
            ],
        ),
        _feature(
            "comic_workflow",
            "Comic Workflow",
            (
                "Plan, validate, preview, package, and optionally apply editable comic "
                "page changesets."
            ),
            tools=[
                "loomstroke_plan_comic_page",
                "loomstroke_validate_comic_plan",
                "loomstroke_render_comic_preview",
                "loomstroke_export_comic_package",
                "loomstroke_create_comic_changeset",
                "loomstroke_apply_changeset",
            ],
            resources=["loomstroke://diagnostics/readiness"],
            status=(
                "available"
                if roots and has_live_canvas
                else "preview_available_package_or_apply_blocked"
            ),
            safety=[
                "roots_required_for_package",
                "confirmation_required_for_write",
                "changeset_required_for_canvas_mutation",
                "command_backed_apply_only",
                "response_metadata_omits_comic_text",
            ],
        ),
        _feature(
            "bible_store",
            "Bible Store",
            "Project, character, style, asset, and export-target rules for prompt previews.",
            tools=["loomstroke_add_bible_rule", "loomstroke_preview_bible_injection"],
            resources=[
                "loomstroke://production/bible/{scope}",
                "loomstroke://production/bible/{scope}/{scope_id}",
            ],
            status="available",
            safety=["confirmation_required_for_write", "no_external_call"],
        ),
        _feature(
            "agent_job_console",
            "Agent Job Console",
            "Unified view over existing MCP jobs and persisted ProductionJob metadata.",
            tools=["loomstroke_create_production_job", "loomstroke_wait_job"],
            resources=["loomstroke://production/jobs", "loomstroke://production/job/{job_id}"],
            status="available" if generation_ready else "local_only",
            safety=[
                "privacy_gate_for_external_jobs",
                "confirmation_required_for_job_create",
            ],
        ),
        _feature(
            "changeset",
            "Changeset Draft and Apply",
            "Draft, confirm, apply, and rollback production changes without direct agent mutation.",
            tools=[
                "loomstroke_create_changeset",
                "loomstroke_apply_changeset",
                "loomstroke_rollback_changeset",
            ],
            resources=["loomstroke://production/changeset/{changeset_id}"],
            status="available",
            safety=["confirmation_required", "command_backed_apply_only"],
        ),
        _feature(
            "export_profile",
            "Export Profile",
            "Validate and preview export profiles under MCP roots before file output.",
            tools=["loomstroke_validate_export_profile", "loomstroke_create_export_preview"],
            resources=["loomstroke://production/export-profiles"],
            status="available" if roots else "blocked_until_roots_configured",
            safety=["roots_required_for_file_write", "confirmation_required_for_write"],
        ),
        _feature(
            "recipe_compiler",
            "Recipe Compiler",
            "Credential-free .lsrecipe.json validation and persistence.",
            tools=["loomstroke_save_recipe"],
            resources=["loomstroke://production/recipe/{recipe_id}"],
            status="available",
            safety=["credential_free", "confirmation_required_for_write"],
        ),
    ]
    return {
        "schemaVersion": "pct.feature_catalog.v1",
        "features": features,
        "safety": {
            "externalServicesAllowed": external_allowed,
            "productionRecipeGenerationSupported": generation_capabilities["productionCandidates"],
            "roots": roots,
            "credentialReadOnDemandOnly": True,
            "directCanvasMutationAllowed": False,
            "productionStore": scrub_for_pct(store_summary),
        },
    }


def explain_feature(service: Any, feature_id: str) -> dict[str, Any]:
    catalog = build_feature_catalog(service)
    for feature in catalog["features"]:
        if feature["featureId"] == feature_id:
            return {"schemaVersion": "pct.feature_explanation.v1", "feature": feature}
    return {
        "schemaVersion": "pct.feature_explanation.v1",
        "feature": None,
        "knownFeatureIds": [feature["featureId"] for feature in catalog["features"]],
    }


async def collect_ai_effect_presets(service: Any) -> list[Any]:
    lister = getattr(service, "_ai_effect_preset_lister", None)
    if lister is None:
        return []

    presets = lister()
    if inspect.isawaitable(presets):
        presets = await presets
    return list(presets or ())


def build_ai_effect_catalog(
    presets: Iterable[Any],
    *,
    external_services_allowed: bool | None = None,
) -> dict[str, Any]:
    entries = [
        _preset_catalog_entry(
            preset,
            external_services_allowed=external_services_allowed,
        )
        for preset in presets
    ]
    entries.sort(
        key=lambda item: (
            str(item.get("gatewayId") or ""),
            str(item.get("presetId") or ""),
        )
    )
    gateways: dict[str, dict[str, Any]] = {}
    for entry in entries:
        gateway_id = str(entry.get("gatewayId") or "unknown")
        gateway = gateways.setdefault(
            gateway_id,
            {
                "gatewayId": gateway_id,
                "presetCount": 0,
                "gatewayKind": entry["gateway"]["gatewayKind"],
                "externalServiceRequired": entry["gateway"]["externalServiceRequired"],
                "credentialStatus": "not_checked",
                "runtimeReadiness": _gateway_runtime_readiness(
                    gateway_id,
                    external_services_allowed=external_services_allowed,
                ),
            },
        )
        gateway["presetCount"] += 1
    return {
        "schemaVersion": "pct.ai_effect_catalog.v1",
        "presetCount": len(entries),
        "presets": entries,
        "gateways": sorted(gateways.values(), key=lambda item: item["gatewayId"]),
        "credentialPolicy": {
            "credentialValuesIncluded": False,
            "credentialReadPerformed": False,
        },
    }


def build_ai_effect_preset_detail(
    presets: Iterable[Any],
    preset_id: str,
    *,
    external_services_allowed: bool | None = None,
) -> dict[str, Any] | None:
    for preset in presets:
        if _preset_id(preset) == preset_id:
            return _preset_detail(
                preset,
                external_services_allowed=external_services_allowed,
            )
    return None


def project_generation_history(store: ProductionControlStore, history_store: Any) -> dict[str, Any]:
    records = _history_records(history_store)
    artifacts = [
        _artifact_from_generation(record, output)
        for record in records
        for output in _record_outputs(record)
    ]
    edges = [
        _edge_from_generation(record, artifact)
        for record in records
        for artifact in _artifacts_for_record(artifacts, record)
    ]

    previous_artifacts = [
        item
        for item in store.read_records("artifacts")
        if _source_type(item) != "generation_history"
    ]
    previous_edges = [
        item for item in store.read_records("edges") if _source_type(item) != "generation_history"
    ]
    merged_artifacts = sorted(
        [*previous_artifacts, *artifacts],
        key=lambda item: str(item.get("artifactId") or ""),
    )
    merged_edges = sorted(
        [*previous_edges, *edges],
        key=lambda item: str(item.get("edgeId") or ""),
    )
    store.write_records("artifacts", merged_artifacts)
    store.write_records("edges", merged_edges)
    return {
        "schemaVersion": "pct.production_graph.v1",
        "artifactCount": len(merged_artifacts),
        "edgeCount": len(merged_edges),
        "projectedHistoryCount": len(records),
        "artifacts": merged_artifacts,
        "edges": merged_edges,
    }


def build_lineage(store: ProductionControlStore, artifact_id: str) -> dict[str, Any] | None:
    artifact = store.get_record("artifacts", "artifactId", artifact_id)
    if artifact is None:
        return None
    edges = store.read_records("edges")
    incoming = [edge for edge in edges if str(edge.get("toArtifactId") or "") == artifact_id]
    outgoing = [edge for edge in edges if str(edge.get("fromArtifactId") or "") == artifact_id]
    return {
        "schemaVersion": "pct.lineage.v1",
        "artifact": artifact,
        "incomingEdges": incoming,
        "outgoingEdges": outgoing,
    }


def run_visual_qa_preview(
    *,
    artifact: Mapping[str, Any] | None,
    asset_id: str | None,
    asset_bytes: Callable[[str], bytes],
    export_profile: Mapping[str, Any] | None = None,
    bible_terms: Sequence[str] | None = None,
) -> dict[str, Any]:
    artifact_id = (
        str(artifact.get("artifactId") or "") if artifact is not None else str(asset_id or "")
    )
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []
    png_bytes: bytes | None = None
    if asset_id:
        try:
            png_bytes = asset_bytes(asset_id)
        except Exception:
            png_bytes = None
            warnings.append("asset_bytes_unavailable")
    if png_bytes is None and artifact is not None:
        png_bytes = _artifact_file_bytes(artifact)
    if png_bytes is not None:
        checks.extend(_image_checks(png_bytes))
    else:
        checks.append(_qa_check("image_available", "warn", "No image payload was available."))

    if export_profile:
        checks.extend(_export_profile_checks(artifact, export_profile))
    checks.extend(_bible_term_checks(artifact, bible_terms or ()))
    status = "fail" if any(check["severity"] == "error" for check in checks) else "pass"
    if any(check["severity"] == "warn" for check in checks):
        status = "warn" if status == "pass" else status
    return {
        "schemaVersion": "pct.visual_qa.v1",
        "artifactId": artifact_id,
        "status": status,
        "readOnly": True,
        "checks": checks,
        "createdAt": _now_iso(),
        "warnings": warnings,
    }


def preview_bible_injection(
    store: ProductionControlStore,
    *,
    prompt: str,
    scopes: Sequence[str] | None = None,
    scope_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    requested_scopes = [_normalize_bible_scope(scope) for scope in (scopes or ("project",))]
    requested_ids = {str(item) for item in (scope_ids or ()) if str(item)}
    entries: list[dict[str, Any]] = []
    for scope in requested_scopes:
        for entry in store.list_bible_entries(scope):
            if requested_ids and str(entry.get("scopeId") or "") not in requested_ids:
                continue
            entries.append(entry)
    fragments = [
        {
            "entryId": entry.get("entryId"),
            "scope": entry.get("scope"),
            "scopeId": entry.get("scopeId"),
            "text": str(entry.get("text") or entry.get("rule") or ""),
        }
        for entry in entries
    ]
    suffix = "\n".join(fragment["text"] for fragment in fragments if fragment["text"])
    preview_prompt = prompt if not suffix else f"{prompt}\n\n{suffix}"
    return {
        "schemaVersion": "pct.bible_injection_preview.v1",
        "prompt": prompt,
        "previewPrompt": preview_prompt,
        "fragments": fragments,
        "externalCallPerformed": False,
    }


def validate_export_profile(
    profile: Mapping[str, Any],
    *,
    validate_path: Callable[[str, bool, Sequence[str]], Path],
    roots: Sequence[Path],
) -> dict[str, Any]:
    warnings: list[str] = []
    blocked_reasons: list[str] = []
    normalized = scrub_for_pct(dict(profile))
    name = str(normalized.get("name") or normalized.get("profileId") or "export")
    width = _optional_positive_int(normalized.get("width"))
    height = _optional_positive_int(normalized.get("height"))
    if "width" in normalized and width is None:
        blocked_reasons.append("invalid_width")
    if "height" in normalized and height is None:
        blocked_reasons.append("invalid_height")
    output_path = normalized.get("outputPath") or normalized.get("output_path")
    if output_path:
        if not roots:
            blocked_reasons.append("roots_required")
        else:
            try:
                validate_path(str(output_path), bool(normalized.get("overwrite", False)), (".png",))
            except Exception:
                blocked_reasons.append("output_path_outside_roots")
    else:
        warnings.append("output_path_not_set")
    if not _safe_name_template(str(normalized.get("fileNamePattern") or "{artifact_id}.png")):
        blocked_reasons.append("unsafe_file_name_pattern")
    profile_id = str(normalized.get("profileId") or _stable_id("pct_export_profile", normalized))
    return {
        "schemaVersion": "pct.export_profile_validation.v1",
        "profile": {
            "profileId": profile_id,
            "name": name,
            "width": width,
            "height": height,
            "fileNamePattern": str(normalized.get("fileNamePattern") or "{artifact_id}.png"),
            "outputPath": str(output_path) if output_path else None,
        },
        "valid": not blocked_reasons,
        "warnings": warnings,
        "blockedReasons": blocked_reasons,
    }


def build_export_preview(
    profile: Mapping[str, Any],
    *,
    artifact: Mapping[str, Any] | None = None,
    asset: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = scrub_for_pct(dict(profile))
    artifact_id = (
        str(artifact.get("artifactId") or "")
        if artifact is not None
        else str(asset.get("assetId") or "") if asset is not None else ""
    )
    file_pattern = str(normalized.get("fileNamePattern") or "{artifact_id}.png")
    file_name = file_pattern.format(artifact_id=artifact_id or "preview")
    return {
        "schemaVersion": "pct.export_preview.v1",
        "artifactId": artifact_id,
        "fileName": file_name,
        "profile": normalized,
        "writePerformed": False,
    }


def validate_recipe_payload(recipe: Mapping[str, Any]) -> dict[str, Any]:
    normalized = scrub_for_pct(dict(recipe))
    blocked_reasons: list[str] = []
    warnings: list[str] = []
    if contains_sensitive_fields(recipe):
        blocked_reasons.append("credential_or_private_endpoint_field")
    if not isinstance(normalized.get("steps", []), list):
        blocked_reasons.append("steps_must_be_list")
    if not normalized.get("name"):
        warnings.append("recipe_name_not_set")
    recipe_id = str(normalized.get("recipeId") or _stable_id("pct_recipe", normalized))
    normalized["recipeId"] = recipe_id
    normalized.setdefault("schemaVersion", "pct.recipe.v1")
    return {
        "schemaVersion": "pct.recipe_validation.v1",
        "recipe": normalized,
        "valid": not blocked_reasons,
        "warnings": warnings,
        "blockedReasons": blocked_reasons,
    }


def create_bible_entry(arguments: Mapping[str, Any]) -> dict[str, Any]:
    scope = _normalize_bible_scope(str(arguments.get("scope") or "project"))
    scope_id = str(arguments.get("scopeId") or arguments.get("scope_id") or "default")
    text = str(arguments.get("text") or arguments.get("rule") or "").strip()
    tags = arguments.get("tags")
    payload = {
        "scope": scope,
        "scopeId": scope_id,
        "text": text,
        "priority": int(arguments.get("priority") or 0),
        "tags": [str(tag) for tag in tags] if isinstance(tags, list) else [],
    }
    return {
        "schemaVersion": "pct.bible_entry.v1",
        "entryId": str(arguments.get("entryId") or _stable_id("pct_bible", payload)),
        **payload,
        "createdAt": _now_iso(),
        "updatedAt": _now_iso(),
    }


def create_production_job_record(arguments: Mapping[str, Any]) -> dict[str, Any]:
    kind = str(arguments.get("kind") or arguments.get("jobClass") or "local_task")
    payload = scrub_for_pct(dict(arguments))
    job_id = str(arguments.get("jobId") or _stable_id("pct_job", payload))
    return {
        "schemaVersion": "pct.production_job.v1",
        "jobId": job_id,
        "jobClass": kind,
        "status": "queued",
        "progress": 0.0,
        "source": "production_store",
        "metadata": payload,
        "createdAt": _now_iso(),
        "updatedAt": _now_iso(),
    }


def create_decision_record(arguments: Mapping[str, Any]) -> dict[str, Any]:
    artifact_id = str(arguments.get("artifactId") or arguments.get("artifact_id") or "")
    decision = str(arguments.get("decision") or "undecided")
    payload = {
        "artifactId": artifact_id,
        "decision": decision,
        "reason": str(arguments.get("reason") or ""),
    }
    return {
        "schemaVersion": "pct.artifact_decision.v1",
        "decisionId": str(arguments.get("decisionId") or _stable_id("pct_decision", payload)),
        **payload,
        "createdAt": _now_iso(),
    }


def create_changeset_record(arguments: Mapping[str, Any]) -> dict[str, Any]:
    operations = arguments.get("operations")
    op_list = [dict(item) for item in operations] if isinstance(operations, list) else []
    payload = {
        "name": str(arguments.get("name") or ""),
        "operations": scrub_for_pct(op_list),
        "target": scrub_for_pct(arguments.get("target") or {}),
    }
    return {
        "schemaVersion": "pct.changeset.v1",
        "changesetId": str(arguments.get("changesetId") or _stable_id("pct_changeset", payload)),
        "name": payload["name"],
        "status": "draft",
        "operations": payload["operations"],
        "target": payload["target"],
        "createdAt": _now_iso(),
        "updatedAt": _now_iso(),
        "appliedAt": None,
        "rolledBackAt": None,
    }


def merge_mcp_and_production_jobs(
    *,
    mcp_jobs: Sequence[Mapping[str, Any]],
    production_jobs: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for job in mcp_jobs:
        item = scrub_for_pct(dict(job))
        item.setdefault("schemaVersion", "pct.production_job.v1")
        item["source"] = "mcp"
        jobs.append(item)
    for job in production_jobs:
        item = scrub_for_pct(dict(job))
        item.setdefault("source", "production_store")
        jobs.append(item)
    jobs.sort(key=lambda item: str(item.get("createdAt") or item.get("updatedAt") or ""))
    return jobs


def _feature(
    feature_id: str,
    name: str,
    description: str,
    *,
    tools: Sequence[str],
    resources: Sequence[str],
    prompts: Sequence[str] = (),
    status: str,
    safety: Sequence[str],
) -> dict[str, Any]:
    return {
        "featureId": feature_id,
        "name": name,
        "description": description,
        "status": status,
        "tools": list(tools),
        "resources": list(resources),
        "prompts": list(prompts),
        "safetyConditions": list(safety),
    }


def _preset_catalog_entry(
    preset: Any,
    *,
    external_services_allowed: bool | None = None,
) -> dict[str, Any]:
    detail = _preset_detail(preset, external_services_allowed=external_services_allowed)
    return {
        "presetId": detail["presetId"],
        "name": detail["name"],
        "category": detail["category"],
        "gatewayId": detail["gatewayId"],
        "source": detail["source"],
        "parameters": detail["parameterSummary"],
        "inputSlots": detail["inputSlotSummary"],
        "outputs": detail["outputSummary"],
        "gateway": detail["gateway"],
        "runtimeReadiness": detail["runtimeReadiness"],
    }


def _preset_detail(
    preset: Any,
    *,
    external_services_allowed: bool | None = None,
) -> dict[str, Any]:
    manifest = _manifest(preset)
    metadata = _mapping(manifest.get("metadata"))
    gateway_id = _gateway_id(preset, manifest)
    gateway_file = _gateway_file(preset, gateway_id)
    parameters = list(_manifest_list(manifest, "parameters"))
    if gateway_id == "gptimage2" and not parameters:
        parameters = _gptimage2_parameters()
    input_slots = list(_manifest_list(manifest, "input_slots"))
    outputs = list(_manifest_list(manifest, "outputs"))
    name = str(_value(preset, "name") or metadata.get("name") or _preset_id(preset))
    category = str(_value(preset, "category") or metadata.get("category") or "")
    gateway_summary = _gateway_summary(gateway_id, gateway_file)
    return {
        "schemaVersion": "pct.ai_effect_preset_detail.v1",
        "presetId": _preset_id(preset),
        "name": name,
        "category": category,
        "gatewayId": gateway_id,
        "source": str(_value(preset, "source") or ""),
        "parameterSummary": _parameter_summary(parameters),
        "inputSlotSummary": _slot_summary(input_slots),
        "outputSummary": _output_summary(outputs),
        "parameters": scrub_for_pct(parameters),
        "inputSlots": scrub_for_pct(input_slots),
        "outputs": scrub_for_pct(outputs),
        "gateway": gateway_summary,
        "paramValueSchema": _param_value_schema(parameters),
        "safeDefaults": _safe_param_defaults(parameters),
        "recommendedOverrides": _recommended_submit_overrides(parameters),
        "submitExample": _submit_example(parameters),
        "runtimeReadiness": _preset_runtime_readiness(
            gateway_id,
            gateway_summary,
            external_services_allowed=external_services_allowed,
        ),
        "credentialPolicy": {
            "credentialValuesIncluded": False,
            "credentialReadPerformed": False,
        },
    }


def _gateway_summary(gateway_id: str, gateway_file: Mapping[str, Any] | None) -> dict[str, Any]:
    workflow = _mapping(gateway_file.get("workflow")) if isinstance(gateway_file, Mapping) else {}
    has_workflow = bool(workflow)
    if gateway_id in _WORKFLOW_BACKED_GATEWAYS:
        kind = "workflow_backed"
    elif gateway_id in _FIXED_PARAMETER_GATEWAYS:
        kind = "fixed_parameter"
    else:
        kind = "unknown"
    workflow_file_required = gateway_id in _WORKFLOW_BACKED_GATEWAYS
    supports_gateway_file = gateway_id not in {"gptimage2"}
    workflow_status = (
        "present" if has_workflow else ("missing" if workflow_file_required else "not_applicable")
    )
    summary = {
        "gatewayId": gateway_id,
        "gatewayKind": kind,
        "externalServiceRequired": gateway_id in _EXTERNAL_GATEWAYS,
        "credentialRequired": gateway_id in {"nanobanana", "gptimage2"},
        "credentialStatus": "not_checked",
        "supportsGatewayFile": supports_gateway_file,
        "supportsWorkflowFile": workflow_file_required,
        "workflowFileRequired": workflow_file_required,
        "hasWorkflow": has_workflow,
        "workflowStatus": workflow_status,
        "workflowSummary": None,
        "gatewayFileSummary": _gateway_file_summary(gateway_file),
    }
    if has_workflow:
        classes = sorted(
            {
                str(node.get("class_type") or node.get("classType") or "")
                for node in workflow.values()
                if isinstance(node, Mapping)
            }
        )
        summary["workflowSummary"] = {
            "nodeCount": len(workflow),
            "nodeClasses": [item for item in classes if item],
        }
    return summary


def _gateway_runtime_readiness(
    gateway_id: str,
    *,
    external_services_allowed: bool | None,
) -> dict[str, Any]:
    blocked: list[dict[str, Any]] = []
    if gateway_id in _EXTERNAL_GATEWAYS and external_services_allowed is False:
        blocked.append(
            {
                "code": "privacy_gate_disabled",
                "message": "External services are disabled.",
            }
        )
    return {
        "credentialReadPerformed": False,
        "credentialStatus": "not_checked",
        "externalServicesAllowed": external_services_allowed,
        "knownBlocked": bool(blocked),
        "blockedReasons": blocked,
    }


def _preset_runtime_readiness(
    gateway_id: str,
    gateway_summary: Mapping[str, Any],
    *,
    external_services_allowed: bool | None,
) -> dict[str, Any]:
    blocked: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    if bool(gateway_summary.get("externalServiceRequired")) and external_services_allowed is False:
        blocked.append(
            {
                "code": "privacy_gate_disabled",
                "message": "External services are disabled.",
                "action": "Enable privacy/allow_external_services before generation.",
            }
        )
    if bool(gateway_summary.get("workflowFileRequired")) and not bool(
        gateway_summary.get("hasWorkflow")
    ):
        blocked.append(
            {
                "code": "workflow_missing",
                "message": "This gateway requires a workflow file but the preset has none.",
            }
        )
    preflight_status = "not_required"
    if gateway_id in _WORKFLOW_BACKED_GATEWAYS:
        preflight_status = "blocked" if blocked else "requires_backend_object_info"
        if not blocked:
            warnings.append(
                {
                    "code": "backend_dependency_preflight_not_run",
                    "message": "Backend node/model dependency preflight has not run.",
                    "action": "Run ComfyUI object_info validation before promising execution.",
                }
            )
    submit_state = "blocked" if blocked else "candidate"
    if preflight_status == "requires_backend_object_info":
        submit_state = "preflight_required"
    return {
        "schemaVersion": "pct.ai_effect_runtime_readiness.v1",
        "submitState": submit_state,
        "knownBlocked": bool(blocked),
        "blockedReasons": blocked,
        "warnings": warnings,
        "externalServiceRequired": gateway_id in _EXTERNAL_GATEWAYS,
        "externalServicesAllowed": external_services_allowed,
        "credentialRequired": gateway_id in {"nanobanana", "gptimage2"},
        "credentialStatus": "not_checked",
        "credentialReadPerformed": False,
        "workflowFileRequired": bool(gateway_summary.get("workflowFileRequired")),
        "workflowStatus": gateway_summary.get("workflowStatus"),
        "preflightStatus": preflight_status,
    }


def _gateway_file_summary(gateway_file: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(gateway_file, Mapping):
        return {"present": False}
    return {
        "present": True,
        "schemaVersion": gateway_file.get("schema_version") or gateway_file.get("schemaVersion"),
        "mappingCount": len(_mapping(gateway_file.get("param_mappings"))),
        "slotMappingCount": len(_mapping(gateway_file.get("slot_mappings"))),
        "outputMappingCount": len(_mapping(gateway_file.get("output_mappings"))),
        "workflowNodeCount": len(_mapping(gateway_file.get("workflow"))),
    }


def _parameter_summary(parameters: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(parameters),
        "ids": [
            str(item.get("internal_id") or item.get("internalId") or "") for item in parameters
        ],
        "requiredTextIds": [
            str(item.get("internal_id") or item.get("internalId") or "")
            for item in parameters
            if bool(item.get("required_text") or item.get("requiredText"))
        ],
    }


def _param_value_schema(parameters: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    properties: dict[str, dict[str, Any]] = {}
    required: list[str] = []
    for item in parameters:
        param_id = _parameter_id(item)
        if not param_id or _is_sensitive_key(param_id):
            continue
        properties[param_id] = _schema_for_parameter(item)
        if bool(item.get("required_text") or item.get("requiredText")):
            required.append(param_id)
    return {
        "type": "object",
        "additionalProperties": True,
        "properties": scrub_for_pct(properties),
        "required": required,
    }


def _safe_param_defaults(parameters: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    defaults: dict[str, Any] = {}
    for item in parameters:
        param_id = _parameter_id(item)
        if not param_id or _is_sensitive_key(param_id):
            continue
        default = _parameter_default(item)
        if default is not None:
            defaults[param_id] = default
    return scrub_for_pct(defaults)


def _recommended_submit_overrides(parameters: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "forceRegenerate": True,
        "persistOverrides": False,
        "applyToCanvas": False,
        "paramValues": _example_param_values(parameters),
    }


def _submit_example(parameters: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    overrides = _recommended_submit_overrides(parameters)
    return {
        "tool": "loomstroke_submit_generation",
        "schemaVersion": "pct.ai_effect_submit_example.v1",
        "requires": [
            {
                "tool": "loomstroke_apply_ai_effect_preset",
                "output": "layerId",
            }
        ],
        "arguments": {
            "layerId": "<layerId from loomstroke_apply_ai_effect_preset>",
            **overrides,
        },
        "notes": [
            "applyToCanvas=false keeps generation output asset-only for preview/export workflows.",
            "privacy/allow_external_services must be enabled before submit.",
        ],
    }


def _schema_for_parameter(item: Mapping[str, Any]) -> dict[str, Any]:
    kind = _parameter_kind(item)
    schema: dict[str, Any] = {"type": _json_schema_type(kind)}
    display_name = item.get("display_name") or item.get("displayName")
    if display_name:
        schema["title"] = str(display_name)
    choices = _parameter_choices(item)
    if choices:
        schema["type"] = "string"
        schema["enum"] = choices
    range_ = _mapping(item.get("range"))
    if "min" in range_:
        schema["minimum"] = range_["min"]
    if "max" in range_:
        schema["maximum"] = range_["max"]
    default = _parameter_default(item)
    if default is not None:
        schema["default"] = default
    return schema


def _example_param_values(parameters: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for item in parameters:
        param_id = _parameter_id(item)
        if not param_id or _is_sensitive_key(param_id):
            continue
        if _is_prompt_like_param(param_id, item):
            values[param_id] = "<describe the desired image>"
            continue
        default = _parameter_default(item)
        if default not in (None, ""):
            values[param_id] = default
            continue
        choices = _parameter_choices(item)
        if choices:
            values[param_id] = choices[0]
    return scrub_for_pct(values)


def _parameter_id(item: Mapping[str, Any]) -> str:
    return str(item.get("internal_id") or item.get("internalId") or "").strip()


def _parameter_kind(item: Mapping[str, Any]) -> str:
    return str(item.get("type") or item.get("kind") or "string").strip().lower()


def _json_schema_type(kind: str) -> str:
    if kind in {"bool", "boolean"}:
        return "boolean"
    if kind in {"int", "integer"}:
        return "integer"
    if kind in {"float", "double", "number"}:
        return "number"
    return "string"


def _parameter_choices(item: Mapping[str, Any]) -> list[str]:
    choices = item.get("choices") or item.get("enum")
    if not isinstance(choices, Sequence) or isinstance(choices, str | bytes):
        return []
    return [str(choice) for choice in choices]


def _parameter_default(item: Mapping[str, Any]) -> Any:
    if "default" in item:
        return scrub_for_pct(item.get("default"))
    if "defaultValue" in item:
        return scrub_for_pct(item.get("defaultValue"))
    return None


def _is_prompt_like_param(param_id: str, item: Mapping[str, Any]) -> bool:
    lowered = param_id.lower()
    if "prompt" in lowered or "description" in lowered:
        return True
    display_name = str(item.get("display_name") or item.get("displayName") or "").lower()
    return "prompt" in display_name or "description" in display_name


def _slot_summary(slots: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(slots),
        "requiredCount": sum(1 for item in slots if bool(item.get("required"))),
        "ids": [str(item.get("internal_id") or item.get("internalId") or "") for item in slots],
    }


def _output_summary(outputs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "count": len(outputs),
        "ids": [str(item.get("internal_id") or item.get("internalId") or "") for item in outputs],
    }


def _manifest(preset: Any) -> dict[str, Any]:
    return _mapping(_value(preset, "manifest"))


def _preset_id(preset: Any) -> str:
    manifest = _manifest(preset)
    return str(
        _value(preset, "preset_id")
        or _value(preset, "presetId")
        or manifest.get("preset_id")
        or manifest.get("presetId")
        or ""
    )


def _gateway_id(preset: Any, manifest: Mapping[str, Any]) -> str:
    return str(
        _value(preset, "gateway_id")
        or _value(preset, "gatewayId")
        or manifest.get("gateway")
        or manifest.get("gateway_id")
        or ""
    )


def _gateway_file(preset: Any, gateway_id: str) -> dict[str, Any] | None:
    primary = getattr(preset, "primary_gateway_file", None)
    if callable(primary):
        try:
            value = primary()
            if isinstance(value, Mapping):
                return dict(value)
        except Exception:
            return None
    direct = _value(preset, "gateway_file")
    if isinstance(direct, Mapping):
        return dict(direct)
    files = _value(preset, "gateway_files") or _value(preset, "gatewayFiles")
    if isinstance(files, Mapping):
        value = files.get(gateway_id)
        if isinstance(value, Mapping):
            return dict(value)
    return None


def _manifest_list(manifest: Mapping[str, Any], key: str) -> list[dict[str, Any]]:
    value = manifest.get(key)
    if value is None and "_" in key:
        camel = key.split("_", 1)[0] + key.split("_", 1)[1].title().replace("_", "")
        value = manifest.get(camel)
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, Mapping)]


def _gptimage2_parameters() -> list[dict[str, Any]]:
    try:
        from loomstroke.plugins.gptimage2.types import (  # noqa: PLC0415
            ALL_BACKGROUNDS,
            ALL_MODELS,
            ALL_MODERATION,
            ALL_OUTPUT_FORMATS,
            ALL_QUALITIES,
            ALL_SIZES,
            DEFAULT_MODEL,
        )
    except Exception:
        ALL_BACKGROUNDS = ["auto", "opaque"]
        ALL_MODELS = ["gpt-image-2"]
        ALL_MODERATION = ["auto", "low"]
        ALL_OUTPUT_FORMATS = ["png", "jpeg", "webp"]
        ALL_QUALITIES = ["auto", "low", "medium", "high"]
        ALL_SIZES = ["auto", "1024x1024", "1024x1536", "1536x1024"]
        DEFAULT_MODEL = "gpt-image-2"
    return [
        _param("prompt", "Prompt", "string", "", multiline=True),
        _param("model", "Model", "enum", DEFAULT_MODEL, choices=list(ALL_MODELS)),
        _param("size", "Size", "enum", "1024x1024", choices=list(ALL_SIZES)),
        _param("quality", "Quality", "enum", "auto", choices=list(ALL_QUALITIES)),
        _param(
            "output_format",
            "Output format",
            "enum",
            "png",
            choices=list(ALL_OUTPUT_FORMATS),
        ),
        _param(
            "output_compression",
            "Output compression",
            "int",
            100,
            range_={"min": 0, "max": 100, "step": 1},
        ),
        _param("background", "Background", "enum", "auto", choices=list(ALL_BACKGROUNDS)),
        _param("moderation", "Moderation", "enum", "auto", choices=list(ALL_MODERATION)),
    ]


def _param(
    internal_id: str,
    display_name: str,
    kind: str,
    default: Any,
    *,
    choices: Sequence[str] | None = None,
    range_: Mapping[str, Any] | None = None,
    multiline: bool = False,
) -> dict[str, Any]:
    return {
        "internal_id": internal_id,
        "display_name": display_name,
        "type": kind,
        "default": default,
        "choices": list(choices or ()),
        "range": dict(range_ or {}),
        "multiline": multiline,
        "gateway_ref": {"param": internal_id},
    }


def _history_records(history_store: Any) -> list[Any]:
    if history_store is None:
        return []
    lister = getattr(history_store, "list_records", None)
    if callable(lister):
        try:
            return list(lister())
        except Exception:
            return []
    if isinstance(history_store, Sequence) and not isinstance(history_store, str | bytes):
        return list(history_store)
    return []


def _record_outputs(record: Any) -> list[dict[str, Any]]:
    output_images = _value(record, "output_images", [])
    outputs = [dict(item) for item in output_images if isinstance(item, Mapping)]
    image_path = _value(record, "image_path")
    if image_path and not any(str(item.get("path") or "") == str(image_path) for item in outputs):
        outputs.append({"path": str(image_path), "kind": "image"})
    if not outputs:
        outputs.append({"path": None, "kind": "metadata"})
    return outputs


def _artifact_from_generation(record: Any, output: Mapping[str, Any]) -> dict[str, Any]:
    job_id = str(_value(record, "job_id") or "")
    output_ref = str(output.get("path") or output.get("id") or len(json.dumps(output)))
    artifact_id = _stable_id("pct_art_hist", {"jobId": job_id, "output": output_ref})
    dimensions = _dimensions_from_record(record, output)
    gateway_id = _gateway_from_record(record)
    return {
        "schemaVersion": "pct.production_artifact.v1",
        "artifactId": artifact_id,
        "kind": "raster_generated",
        "status": str(_value(record, "status") or "unknown"),
        "displayName": Path(str(output.get("path") or artifact_id)).name,
        "createdAt": str(_value(record, "created_at") or ""),
        "completedAt": _value(record, "completed_at"),
        "source": {
            "type": "generation_history",
            "jobId": job_id,
            "serviceId": str(_value(record, "service_id") or ""),
            "serviceName": str(_value(record, "service_name") or ""),
        },
        "presetRef": {"gatewayId": gateway_id},
        "fileRef": _safe_file_ref(record, output),
        "dimensions": dimensions,
        "promptRef": {
            "prompt": _value(record, "prompt"),
            "negativePrompt": _value(record, "negative_prompt"),
            "modelName": _value(record, "model_name"),
            "seed": _value(record, "seed"),
        },
        "metadata": {
            "projectedAt": _now_iso(),
            "durationSec": _value(record, "duration_sec"),
        },
    }


def _artifacts_for_record(
    artifacts: Sequence[Mapping[str, Any]],
    record: Any,
) -> list[dict[str, Any]]:
    job_id = str(_value(record, "job_id") or "")
    return [
        dict(artifact)
        for artifact in artifacts
        if _mapping(artifact.get("source")).get("jobId") == job_id
    ]


def _edge_from_generation(record: Any, artifact: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schemaVersion": "pct.production_edge.v1",
        "edgeId": _stable_id(
            "pct_edge_hist",
            {
                "jobId": _value(record, "job_id"),
                "artifactId": artifact.get("artifactId"),
            },
        ),
        "kind": "generated",
        "fromArtifactId": None,
        "toArtifactId": artifact.get("artifactId"),
        "source": {
            "type": "generation_history",
            "jobId": _value(record, "job_id"),
        },
        "metadata": {"gatewayId": _gateway_from_record(record)},
    }


def _safe_file_ref(record: Any, output: Mapping[str, Any]) -> dict[str, Any]:
    path = output.get("path") or output.get("relativePath") or _value(record, "image_path")
    if isinstance(path, str) and path and not _looks_private_path(path):
        return {
            "relativePath": path,
            "thumbnailPath": _value(record, "thumbnail_path"),
            "mimeType": "image/png",
        }
    return {"relativePath": None, "thumbnailPath": None, "mimeType": "image/png"}


def _artifact_file_bytes(artifact: Mapping[str, Any]) -> bytes | None:
    file_ref = _mapping(artifact.get("fileRef"))
    rel = file_ref.get("relativePath")
    if not isinstance(rel, str) or not rel or _looks_private_path(rel):
        return None
    # PCT intentionally does not chase arbitrary absolute paths.  Relative
    # generation-history paths require the owning history store to expose bytes.
    return None


def _image_checks(png_bytes: bytes) -> list[dict[str, Any]]:
    image = QImage.fromData(png_bytes)
    if image.isNull():
        return [_qa_check("image_decodable", "error", "Image payload could not be decoded.")]
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    width = int(rgba.width())
    height = int(rgba.height())
    total = max(width * height, 1)
    sample_step = max(1, int((total / 65536) ** 0.5))
    alpha_garbage = 0
    halo = 0
    sampled = 0
    for y in range(0, height, sample_step):
        for x in range(0, width, sample_step):
            color = rgba.pixelColor(x, y)
            alpha = int(color.alpha())
            red = int(color.red())
            green = int(color.green())
            blue = int(color.blue())
            sampled += 1
            if alpha == 0 and (red or green or blue):
                alpha_garbage += 1
            if 0 < alpha < 245 and max(red, green, blue) > 230:
                halo += 1
    checks = [
        _qa_check(
            "image_decodable",
            "info",
            "Image payload decoded successfully.",
            {"width": width, "height": height},
        )
    ]
    if alpha_garbage:
        checks.append(
            _qa_check(
                "alpha_garbage",
                "warn",
                "Transparent pixels contain non-zero RGB data.",
                {"sampled": sampled, "count": alpha_garbage},
            )
        )
    if halo:
        checks.append(
            _qa_check(
                "edge_halo",
                "warn",
                "Semi-transparent bright pixels may create edge halo.",
                {"sampled": sampled, "count": halo},
            )
        )
    return checks


def _export_profile_checks(
    artifact: Mapping[str, Any] | None,
    profile: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if artifact is None:
        return []
    dimensions = _mapping(artifact.get("dimensions"))
    width = _optional_positive_int(profile.get("width"))
    height = _optional_positive_int(profile.get("height"))
    if (
        width
        and height
        and (
            int(dimensions.get("width") or 0) != width
            or int(dimensions.get("height") or 0) != height
        )
    ):
        return [
            _qa_check(
                "resolution_mismatch",
                "warn",
                "Artifact dimensions differ from export profile.",
                {
                    "artifact": dimensions,
                    "profile": {"width": width, "height": height},
                },
            )
        ]
    return []


def _bible_term_checks(
    artifact: Mapping[str, Any] | None,
    bible_terms: Sequence[str],
) -> list[dict[str, Any]]:
    if not bible_terms or artifact is None:
        return []
    haystack = json.dumps(scrub_for_pct(dict(artifact)), ensure_ascii=False).lower()
    missing = [term for term in bible_terms if str(term).lower() not in haystack]
    if missing:
        return [
            _qa_check(
                "bible_visible_token_missing",
                "warn",
                "Bible-required visible tokens were not found in artifact metadata.",
                {"missing": missing},
            )
        ]
    return []


def _qa_check(
    check_id: str,
    severity: str,
    message: str,
    details: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "checkId": check_id,
        "severity": severity,
        "message": message,
        "details": scrub_for_pct(dict(details or {})),
    }


def _dimensions_from_record(record: Any, output: Mapping[str, Any]) -> dict[str, int] | None:
    width = _optional_positive_int(output.get("width") or _value(record, "width"))
    height = _optional_positive_int(output.get("height") or _value(record, "height"))
    if width is None or height is None:
        return None
    return {"width": width, "height": height}


def _gateway_from_record(record: Any) -> str:
    service_id = str(_value(record, "service_id") or "").lower()
    snapshot = _mapping(_value(record, "service_snapshot"))
    candidates = [
        snapshot.get("gatewayId"),
        snapshot.get("gateway_id"),
        snapshot.get("gateway"),
        service_id,
    ]
    for candidate in candidates:
        text = str(candidate or "").lower()
        if "gptimage2" in text or "gpt_image_2" in text:
            return "gptimage2"
        if "nanobanana" in text or "nano_banana" in text:
            return "nanobanana"
        if "comfy" in text:
            return "comfyui"
    return service_id or "unknown"


def _source_type(record: Mapping[str, Any]) -> str:
    return str(_mapping(record.get("source")).get("type") or "")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _value(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _scrub_pct_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_sensitive_key(key_text):
                result[key_text] = "<redacted>"
                continue
            if isinstance(item, bytes | bytearray | memoryview):
                result[key_text] = "<omitted>"
                continue
            result[key_text] = _scrub_pct_value(item)
        return result
    if isinstance(value, list | tuple):
        return [_scrub_pct_value(item) for item in value]
    if isinstance(value, bytes | bytearray | memoryview):
        return "<omitted>"
    return value


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SAFE_SECURITY_METADATA_KEYS:
        return False
    return any(part in lowered for part in _SECRET_KEY_PARTS)


def _normalize_bible_scope(scope: str) -> str:
    normalized = scope.strip().lower().replace("-", "_")
    if normalized not in _BIBLE_SCOPE_FILES:
        normalized = "project"
    return normalized


def _stable_id(prefix: str, payload: Any) -> str:
    encoded = json.dumps(scrub_for_pct(payload), ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", value).strip("._")
    return safe or "item"


def _safe_name_template(value: str) -> bool:
    return not any(part in value for part in ("..", "/", "\\", ":", "\0"))


def _looks_private_path(value: str) -> bool:
    text = value.strip()
    return (
        "://" in text
        or text.startswith("\\\\")
        or Path(text).is_absolute()
        or any(part == ".." for part in Path(text).parts)
    )


def _optional_positive_int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
