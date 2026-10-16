"""Production Control Tower dock panel."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from loomstroke.ai_selection.production_recipes import SAM3_PRODUCTION_RECIPE_IDS
from loomstroke.mcp.production_runner import SAM3_SUBMITTABLE_RECIPE_IDS
from loomstroke.mcp.types import McpStructuredError, ReasonCode

logger = logging.getLogger(__name__)


class ProductionControlPanel(QWidget):
    """Read-only overview for MCP Production Control Tower state."""

    def __init__(self, service: Any | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._summary_labels: dict[str, QLabel] = {}
        self._tables: dict[str, QTableWidget] = {}
        self._status_label = QLabel(self)
        self._refresh_button = QPushButton(self)
        self._tabs = QTabWidget(self)

        self._setup_ui()
        self.refresh()

    @property
    def service(self) -> Any | None:
        return self._service

    def set_service(self, service: Any | None) -> None:
        self._service = service
        self.refresh()

    def refresh(self) -> None:
        """Refresh panel data from local MCP service resources."""

        if self._service is None:
            self._set_status(self.tr("MCP runtime is not available."))
            self._clear_all_tables()
            self._update_summary({})
            return

        try:
            data = self._collect_data()
        except Exception as exc:  # pragma: no cover - defensive UI boundary
            logger.exception("Failed to refresh Production Control panel")
            self._set_status(self.tr("Refresh failed: {error}").format(error=type(exc).__name__))
            return

        self._update_summary(data)
        self._populate_catalog(data)
        self._populate_artifacts(data)
        self._populate_jobs(data)
        self._populate_bible(data)
        self._populate_visual_qa(data)
        self._populate_exports(data)
        self._populate_recipes(data)
        self._populate_lineage(data)
        if data["ai_catalog"].get("unavailable"):
            self._set_status(self.tr("MCP runtime is not available."))
        else:
            self._set_status(self.tr("Updated from local Production Control store."))

    def refresh_translations(self) -> None:
        self._refresh_button.setText(self.tr("Refresh"))
        self._tabs.setTabText(0, self.tr("Overview"))
        self._tabs.setTabText(1, self.tr("Catalog"))
        self._tabs.setTabText(2, self.tr("Artifacts"))
        self._tabs.setTabText(3, self.tr("Jobs"))
        self._tabs.setTabText(4, self.tr("Bible"))
        self._tabs.setTabText(5, self.tr("Visual QA"))
        self._tabs.setTabText(6, self.tr("Export"))
        self._tabs.setTabText(7, self.tr("Recipes"))
        self._tabs.setTabText(8, self.tr("Lineage"))
        self.refresh()

    def _setup_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)

        toolbar = QHBoxLayout()
        toolbar.setContentsMargins(0, 0, 0, 0)
        self._refresh_button.clicked.connect(self.refresh)
        toolbar.addWidget(self._refresh_button)
        toolbar.addWidget(self._status_label, 1)
        layout.addLayout(toolbar)

        self._tabs.addTab(self._create_overview_tab(), "")
        for key in (
            "catalog",
            "artifacts",
            "jobs",
            "bible",
            "visual_qa",
            "exports",
            "recipes",
            "lineage",
        ):
            table = self._create_table()
            self._tables[key] = table
            self._tabs.addTab(table, "")
        layout.addWidget(self._tabs, 1)

        self.refresh_translations()

    def _create_overview_tab(self) -> QWidget:
        tab = QWidget(self)
        form = QFormLayout(tab)
        form.setContentsMargins(8, 8, 8, 8)
        form.setSpacing(6)

        rows = (
            ("features", self.tr("Features")),
            ("ai_presets", self.tr("AI Effect presets")),
            ("artifacts", self.tr("Artifacts")),
            ("jobs", self.tr("Jobs")),
            ("bible_entries", self.tr("Bible entries")),
            ("qa_reports", self.tr("Visual QA reports")),
            ("export_profiles", self.tr("Export profiles")),
            ("recipes", self.tr("Recipes")),
            ("production_recipes", self.tr("Production recipes")),
            ("store_root", self.tr("Store root")),
            ("external_services", self.tr("External services")),
        )
        for key, title in rows:
            label = QLabel("-", tab)
            label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            self._summary_labels[key] = label
            form.addRow(title, label)
        return tab

    def _create_table(self) -> QTableWidget:
        table = QTableWidget(self)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        table.setSelectionBehavior(QAbstractItemView.SelectRows)
        table.setSelectionMode(QAbstractItemView.SingleSelection)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        table.setShowGrid(False)
        return table

    def _collect_data(self) -> dict[str, Any]:
        service = self._service
        capabilities = service.get_production_capabilities()
        try:
            ai_catalog = service.ai_effect_catalog_resource()
        except McpStructuredError as exc:
            if exc.reason_code != ReasonCode.CAPABILITY_UNAVAILABLE.value:
                raise
            # The UI is registered before the preset library finishes startup.
            # Keep local production data available and retry on the next refresh.
            ai_catalog = {"presets": [], "unavailable": True}
        graph = service.production_artifacts_resource()
        jobs = service.list_production_jobs_resource()
        exports = service.list_export_profiles_resource()
        recipes = (
            service.list_recipes_resource()
            if hasattr(service, "list_recipes_resource")
            else {"recipes": []}
        )
        # Runner recipe catalog (includes the experimental SAM3 recipes,
        # which the default filter would hide). This is read-only projection
        # data, so a failure must not take down the whole refresh.
        production_recipes: list[Mapping[str, Any]] = []
        if hasattr(service, "list_production_recipes"):
            try:
                catalog_payload = service.list_production_recipes({"includeExperimental": True})
                production_recipes = [
                    recipe
                    for recipe in _sequence(_mapping(catalog_payload).get("recipes"))
                    if isinstance(recipe, Mapping)
                ]
            except Exception:
                logger.debug("Production recipe catalog fetch failed", exc_info=True)

        bible_entries: list[Mapping[str, Any]] = []
        for scope in ("project", "style", "character", "asset", "export_target"):
            bible = service.get_bible_resource(scope, None)
            bible_entries.extend(bible.get("entries", []))

        qa_reports = list(getattr(service, "_production_qa_reports", {}).values())

        # SAM3 recipe submits persist scorecards / lineage edges in the
        # production store; read them directly (same precedent as the
        # _production_qa_reports access above) and never let a failure take
        # down the whole refresh.
        scorecards: list[Mapping[str, Any]] = []
        edges: list[Mapping[str, Any]] = []
        store_records = getattr(service, "_production_store", None)  # noqa: SLF001
        if store_records is not None:
            try:
                scorecards = [
                    record
                    for record in _sequence(store_records.read_records("scorecards"))
                    if isinstance(record, Mapping)
                ]
            except Exception:
                logger.debug("Production scorecard read failed", exc_info=True)
            try:
                edges = [
                    record
                    for record in _sequence(store_records.read_records("edges"))
                    if isinstance(record, Mapping)
                ]
            except Exception:
                logger.debug("Production lineage edge read failed", exc_info=True)

        store = capabilities.get("store")
        store_root = store.get("root", "") if isinstance(store, Mapping) else ""
        return {
            "capabilities": capabilities,
            "catalog": capabilities.get("catalog", {}),
            "ai_catalog": ai_catalog,
            "graph": graph,
            "jobs": jobs,
            "bible_entries": bible_entries,
            "qa_reports": qa_reports,
            "exports": exports,
            "recipes": recipes,
            "production_recipes": production_recipes,
            "scorecards": scorecards,
            "edges": edges,
            "store_root": store_root,
        }

    def _update_summary(self, data: Mapping[str, Any]) -> None:
        catalog = _mapping(data.get("catalog"))
        ai_catalog = _mapping(data.get("ai_catalog"))
        graph = _mapping(data.get("graph"))
        jobs = _mapping(data.get("jobs"))
        exports = _mapping(data.get("exports"))
        recipes = _mapping(data.get("recipes"))
        capabilities = _mapping(data.get("capabilities"))
        feature_catalog = _mapping(capabilities.get("catalog"))
        safety = _mapping(feature_catalog.get("safety"))

        summary = {
            "features": str(len(_sequence(catalog.get("features")))),
            "ai_presets": str(len(_sequence(ai_catalog.get("presets")))),
            "artifacts": str(len(_sequence(graph.get("artifacts")))),
            "jobs": str(len(_sequence(jobs.get("jobs")))),
            "bible_entries": str(len(_sequence(data.get("bible_entries")))),
            "qa_reports": str(len(_sequence(data.get("qa_reports")))),
            "export_profiles": str(len(_sequence(exports.get("profiles")))),
            "recipes": str(len(_sequence(recipes.get("recipes")))),
            "production_recipes": str(len(_sequence(data.get("production_recipes")))),
            "store_root": _text(data.get("store_root") or "-"),
            "external_services": _text(
                safety.get("externalServicesAllowed", safety.get("allowExternalServices", "-"))
            ),
        }
        for key, label in self._summary_labels.items():
            label.setText(summary.get(key, "-"))

    def _populate_catalog(self, data: Mapping[str, Any]) -> None:
        catalog = _mapping(data.get("catalog"))
        ai_catalog = _mapping(data.get("ai_catalog"))
        rows: list[Sequence[Any]] = []
        for feature in _sequence(catalog.get("features")):
            if not isinstance(feature, Mapping):
                continue
            rows.append(
                (
                    self.tr("Feature"),
                    feature.get("featureId") or feature.get("id"),
                    feature.get("name") or feature.get("title"),
                    feature.get("status"),
                    ", ".join(_as_strings(feature.get("tools")))
                    or ", ".join(_as_strings(feature.get("resources"))),
                )
            )
        for gateway in _sequence(ai_catalog.get("gateways")):
            if not isinstance(gateway, Mapping):
                continue
            rows.append(
                (
                    self.tr("Gateway"),
                    gateway.get("gatewayId") or gateway.get("id"),
                    gateway.get("gatewayKind") or gateway.get("name"),
                    gateway.get("credentialStatus") or gateway.get("status"),
                    self.tr("{count} presets").format(count=gateway.get("presetCount", 0)),
                )
            )
        for preset in _sequence(ai_catalog.get("presets")):
            if not isinstance(preset, Mapping):
                continue
            rows.append(
                (
                    self.tr("Preset"),
                    preset.get("presetId") or preset.get("id"),
                    preset.get("name") or preset.get("title"),
                    preset.get("gatewayId") or preset.get("gateway_id"),
                    preset.get("category") or preset.get("source"),
                )
            )
        self._set_table(
            self._tables["catalog"],
            (
                self.tr("Kind"),
                self.tr("ID"),
                self.tr("Name"),
                self.tr("Status"),
                self.tr("Details"),
            ),
            rows,
        )

    def _populate_artifacts(self, data: Mapping[str, Any]) -> None:
        graph = _mapping(data.get("graph"))
        rows = []
        for artifact in _sequence(graph.get("artifacts")):
            if not isinstance(artifact, Mapping):
                continue
            rows.append(
                (
                    artifact.get("artifactId") or artifact.get("id"),
                    artifact.get("kind"),
                    _source_label(artifact.get("source")),
                    artifact.get("status"),
                    artifact.get("gatewayId") or artifact.get("gateway_id"),
                    _dimensions(artifact),
                )
            )
        self._set_table(
            self._tables["artifacts"],
            (
                self.tr("Artifact"),
                self.tr("Kind"),
                self.tr("Source"),
                self.tr("Status"),
                self.tr("Gateway"),
                self.tr("Size"),
            ),
            rows,
        )

    def _populate_jobs(self, data: Mapping[str, Any]) -> None:
        jobs = _mapping(data.get("jobs"))
        rows = []
        for job in _sequence(jobs.get("jobs")):
            if not isinstance(job, Mapping):
                continue
            # MCP mirror rows only carry the recipe id inside metadata.
            recipe_id = job.get("recipeId") or _mapping(job.get("metadata")).get("recipeId") or "-"
            rows.append(
                (
                    job.get("jobId") or job.get("id"),
                    job.get("source"),
                    job.get("status"),
                    job.get("jobClass") or job.get("job_class"),
                    recipe_id,
                    job.get("progress"),
                    job.get("updatedAt") or job.get("updated_at"),
                )
            )
        self._set_table(
            self._tables["jobs"],
            (
                self.tr("Job"),
                self.tr("Source"),
                self.tr("Status"),
                self.tr("Class"),
                self.tr("Recipe"),
                self.tr("Progress"),
                self.tr("Updated"),
            ),
            rows,
        )

    def _populate_bible(self, data: Mapping[str, Any]) -> None:
        rows = []
        for entry in _sequence(data.get("bible_entries")):
            if not isinstance(entry, Mapping):
                continue
            rows.append(
                (
                    entry.get("scope"),
                    entry.get("scopeId") or entry.get("scope_id"),
                    entry.get("entryId") or entry.get("id"),
                    entry.get("title") or entry.get("name"),
                    entry.get("updatedAt") or entry.get("updated_at"),
                )
            )
        self._set_table(
            self._tables["bible"],
            (
                self.tr("Scope"),
                self.tr("Scope ID"),
                self.tr("Entry"),
                self.tr("Title"),
                self.tr("Updated"),
            ),
            rows,
        )

    def _populate_visual_qa(self, data: Mapping[str, Any]) -> None:
        rows = []
        for report in _sequence(data.get("qa_reports")):
            if not isinstance(report, Mapping):
                continue
            rows.append(
                (
                    report.get("reportId") or report.get("id"),
                    "ai_effect",
                    report.get("artifactId") or report.get("artifact_id"),
                    report.get("status"),
                    _checks_summary(report.get("checks")),
                    report.get("createdAt") or report.get("created_at"),
                )
            )
        # Recipe runner scorecards (SAM3 submits and generic recipes) live in
        # the production store; merge them after the in-memory reports. The
        # Overview "Visual QA reports" count intentionally keeps tracking the
        # in-memory reports only.
        for scorecard in _sequence(data.get("scorecards")):
            if not isinstance(scorecard, Mapping):
                continue
            rows.append(
                (
                    scorecard.get("scorecardId") or scorecard.get("id"),
                    "recipe_runner",
                    scorecard.get("jobId") or scorecard.get("job_id"),
                    _scorecard_status(scorecard),
                    str(len(_sequence(scorecard.get("results")))),
                    scorecard.get("createdAt") or scorecard.get("created_at"),
                )
            )
        self._set_table(
            self._tables["visual_qa"],
            (
                self.tr("Report"),
                self.tr("Source"),
                self.tr("Artifact/Job"),
                self.tr("Status"),
                self.tr("Checks"),
                self.tr("Created"),
            ),
            rows,
        )

    def _populate_lineage(self, data: Mapping[str, Any]) -> None:
        rows = []
        for edge in _sequence(data.get("edges")):
            if not isinstance(edge, Mapping):
                continue
            rows.append(
                (
                    edge.get("edgeId") or edge.get("id"),
                    edge.get("fromArtifactId"),
                    edge.get("toArtifactId"),
                    edge.get("edgeKind"),
                    _mapping(edge.get("metadata")).get("recipeId") or "-",
                )
            )
        self._set_table(
            self._tables["lineage"],
            (
                self.tr("Edge"),
                self.tr("From"),
                self.tr("To"),
                self.tr("Kind"),
                self.tr("Recipe"),
            ),
            rows,
        )

    def _populate_exports(self, data: Mapping[str, Any]) -> None:
        exports = _mapping(data.get("exports"))
        rows = []
        for profile in _sequence(exports.get("profiles")):
            if not isinstance(profile, Mapping):
                continue
            rows.append(
                (
                    profile.get("profileId") or profile.get("id"),
                    profile.get("name"),
                    profile.get("outputPath") or profile.get("root"),
                    profile.get("format") or profile.get("mimeType"),
                    (
                        profile.get("requiresConfirmation")
                        if "requiresConfirmation" in profile
                        else profile.get("requires_confirmation")
                    ),
                )
            )
        self._set_table(
            self._tables["exports"],
            (
                self.tr("Profile"),
                self.tr("Name"),
                self.tr("Root"),
                self.tr("Format"),
                self.tr("Confirmation"),
            ),
            rows,
        )

    def _populate_recipes(self, data: Mapping[str, Any]) -> None:
        recipes = _mapping(data.get("recipes"))
        rows = []
        for recipe in _sequence(recipes.get("recipes")):
            if not isinstance(recipe, Mapping):
                continue
            rows.append(
                (
                    recipe.get("recipeId") or recipe.get("id"),
                    recipe.get("name") or recipe.get("title"),
                    "store",
                    recipe.get("path") or recipe.get("source"),
                    "-",
                    "-",
                    "-",
                    recipe.get("updatedAt") or recipe.get("updated_at"),
                )
            )
        for recipe in _sequence(data.get("production_recipes")):
            if not isinstance(recipe, Mapping):
                continue
            recipe_id = _text(recipe.get("recipeId") or recipe.get("id"))
            is_sam3 = recipe_id in SAM3_PRODUCTION_RECIPE_IDS
            # SAM3 recipes whose submit is still hard-blocked
            # (ERR_RECIPE_NOT_IMPLEMENTED) only advertise dry_run.
            if is_sam3 and recipe_id not in SAM3_SUBMITTABLE_RECIPE_IDS:
                modes = ["dry_run"]
            else:
                modes = _as_strings(recipe.get("modes"))
            rows.append(
                (
                    recipe_id,
                    recipe.get("displayName") or recipe.get("name") or recipe.get("title"),
                    "runner",
                    recipe.get("domain"),
                    ", ".join(modes) or "-",
                    _yes_no(is_sam3),
                    _yes_no(bool(recipe.get("experimental"))),
                    "-",
                )
            )
        self._set_table(
            self._tables["recipes"],
            (
                self.tr("Recipe"),
                self.tr("Title"),
                self.tr("Origin"),
                self.tr("Source"),
                self.tr("Modes"),
                self.tr("SAM3 Worker"),
                self.tr("Experimental"),
                self.tr("Updated"),
            ),
            rows,
        )

    def _set_table(
        self,
        table: QTableWidget,
        headers: Sequence[str],
        rows: Iterable[Sequence[Any]],
    ) -> None:
        row_list = list(rows)
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(list(headers))
        table.setRowCount(len(row_list))
        for row_index, row in enumerate(row_list):
            for column_index, value in enumerate(row):
                item = QTableWidgetItem(_text(value))
                item.setToolTip(_text(value))
                table.setItem(row_index, column_index, item)
        table.resizeRowsToContents()

    def _clear_all_tables(self) -> None:
        for table in self._tables.values():
            table.setRowCount(0)

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)
        self._status_label.setToolTip(text)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: Any) -> Sequence[Any]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else ()


def _as_strings(value: Any) -> list[str]:
    return [_text(item) for item in _sequence(value)]


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _yes_no(value: bool) -> str:
    return "Yes" if value else "No"


def _dimensions(artifact: Mapping[str, Any]) -> str:
    metadata = artifact.get("metadata")
    if isinstance(metadata, Mapping):
        width = metadata.get("width")
        height = metadata.get("height")
        if width and height:
            return f"{width} x {height}"
    width = artifact.get("width")
    height = artifact.get("height")
    return f"{width} x {height}" if width and height else ""


def _source_label(value: Any) -> str:
    if isinstance(value, Mapping):
        return _text(value.get("type") or value.get("name") or value.get("id"))
    return _text(value)


def _scorecard_status(scorecard: Mapping[str, Any]) -> str:
    sam3 = scorecard.get("sam3Scorecard")
    if isinstance(sam3, Mapping) and "ok" in sam3:
        return "ok" if sam3.get("ok") else "ng"
    counts: dict[str, int] = {}
    for result in _sequence(scorecard.get("results")):
        if not isinstance(result, Mapping):
            continue
        key = _text(result.get("decisionRecommendation") or "")
        if key:
            counts[key] = counts.get(key, 0) + 1
    if not counts:
        return "-"
    return max(counts.items(), key=lambda item: (item[1], item[0]))[0]


def _checks_summary(value: Any) -> str:
    checks = _sequence(value)
    if not checks:
        return ""
    counts: dict[str, int] = {}
    for check in checks:
        if isinstance(check, Mapping):
            key = _text(check.get("severity") or check.get("status") or "check")
        else:
            key = _text(check)
        counts[key] = counts.get(key, 0) + 1
    return ", ".join(f"{key}: {count}" for key, count in sorted(counts.items()))
