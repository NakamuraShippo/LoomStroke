"""Optional ``export-report.json`` next to the exported pages (CT-10 spec §13).

Relative file names only — never absolute paths (spec §20 privacy).  The
report is written atomically and its failure must not fail the export;
the caller reports that separately (spec §16).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from loomstroke.io.atomic_export_writer import write_atomically

from .batch_export_result import BatchExportResult


class ExportReportWriter:
    """Serialize a :class:`BatchExportResult` as ``export-report.json``."""

    SCHEMA: ClassVar[str] = "loomstroke.export_report.v1"
    FILE_NAME: ClassVar[str] = "export-report.json"

    def payload(
        self,
        result: BatchExportResult,
        *,
        format_id: str,
        document_name: str = "",
        created: str | None = None,
    ) -> dict[str, Any]:
        return {
            "schema": self.SCHEMA,
            "created": created or datetime.now(UTC).isoformat(),
            "format": format_id,
            "document": document_name,
            "status": result.status,
            "counts": {
                "exported": result.count("success", "warning"),
                "warnings": result.count("warning"),
                "failed": result.count("failed"),
                "skipped": result.count("skipped", "cancelled"),
            },
            "items": [
                {
                    "pageId": item.page_id,
                    "file": item.file_name,
                    "status": item.status,
                    "issues": [
                        {"code": issue.code, "severity": issue.severity} for issue in item.issues
                    ],
                }
                for item in result.items
            ],
        }

    def write(
        self,
        directory: Path,
        result: BatchExportResult,
        *,
        format_id: str,
        document_name: str = "",
    ) -> Path:
        target = Path(directory) / self.FILE_NAME
        text = json.dumps(
            self.payload(result, format_id=format_id, document_name=document_name),
            ensure_ascii=False,
            indent=2,
        )

        def encode(tmp: Path) -> None:
            tmp.write_text(text, encoding="utf-8")

        write_atomically(target, encode)
        return target
