"""Strict reads for checked PCT record mutations, without changing legacy reads."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def read_checked_records(
    path: Path, *, container_key: str | None = None, jsonl: bool = False
) -> list[dict[str, Any]]:
    """Only a missing file proves an empty collection; malformed data is retained."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    if jsonl:
        records = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        records = json.loads(text)
        if container_key is not None:
            if not isinstance(records, Mapping) or container_key not in records:
                raise ValueError("Invalid PCT record container")
            records = records[container_key]
    if not isinstance(records, list) or any(not isinstance(row, Mapping) for row in records):
        raise ValueError("Invalid PCT record collection")
    # json.loads accepts NaN/Infinity by default; those cannot provide an exact
    # durable before/after comparison for a checked mutation.
    json.dumps(records, allow_nan=False)
    return [dict(row) for row in records]
