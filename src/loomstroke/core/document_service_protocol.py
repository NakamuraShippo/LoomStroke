"""Type-checking contract for the existing document service implementation."""

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from loomstroke.host_api import LoadOptions, ProgressCallback, SaveOptions


class _DocumentServiceProtocol(Protocol):
    def save(
        self,
        path: str | Path,
        options: SaveOptions | None = None,
        *,
        progress: ProgressCallback | None = None,
    ) -> bool: ...

    def load(
        self,
        path: str | Path,
        options: LoadOptions | None = None,
        *,
        progress: ProgressCallback | None = None,
    ) -> bool: ...

    def export_layer(
        self,
        layer_id: str,
        path: str | Path,
        *,
        format: str = "PNG",
        metadata: Mapping[str, Any] | None = None,
    ) -> bool: ...

    def import_layer(
        self,
        path: str | Path,
        *,
        insert_above: bool = True,
        as_new: bool = True,
        progress: ProgressCallback | None = None,
    ) -> str | None: ...
