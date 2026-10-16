"""Read-only view of the host for planning / executing on one page (CT-20 §8)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["ActionContext"]


@dataclass(frozen=True, slots=True)
class ActionContext:
    host: Any
    page_id: str = ""
    document_dir: Path | None = None
    extras: Mapping[str, Any] = field(default_factory=dict)

    @property
    def canvas(self) -> Any:
        return getattr(self.host, "canvas", None)

    @property
    def pages(self) -> Any:
        return getattr(self.host, "pages", None)

    def layers(self) -> tuple[Any, ...]:
        getter = getattr(self.canvas, "get_layers", None)
        if not callable(getter):
            return ()
        try:
            return tuple(getter())
        except Exception:
            return ()

    def active_layer_id(self) -> str | None:
        getter = getattr(self.canvas, "current_layer", None)
        if not callable(getter):
            return None
        try:
            ref = getter()
        except Exception:
            return None
        layer_id = getattr(ref, "id", None)
        return str(layer_id) if layer_id else None

    def layer_ids(self) -> tuple[str, ...]:
        return tuple(str(layer.id) for layer in self.layers() if getattr(layer, "id", None))

    def layer_name(self, layer_id: str) -> str:
        for layer in self.layers():
            if str(getattr(layer, "id", "")) == layer_id:
                return str(getattr(layer, "name", "") or "")
        return ""
