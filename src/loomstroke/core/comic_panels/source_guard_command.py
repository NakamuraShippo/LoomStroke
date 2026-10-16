"""Read-only preflight of the snapshots used by a panel edit draft."""

from __future__ import annotations

from typing import Any

from loomstroke.core.command_types import Command


class PanelSourceGuardCommand(Command):
    vector_object = None
    _layer_id = "__loomstroke_comic_tools__"

    def __init__(
        self,
        expected: dict[str, tuple[str | None, int, dict[str, Any]]],
        *,
        page_size: tuple[int, int] | None = None,
    ) -> None:
        super().__init__()
        # Callers provide already detached snapshots. No source is mutated or copied on apply.
        self.expected = expected
        self.page_size = page_size
        self.last_error = ""
        self._initial_preflight = True

    def apply(self, canvas: Any) -> bool:
        if self.page_size and (canvas._width, canvas._height) != self.page_size:
            self.last_error = "page dimensions changed during preview"
            return False
        for layer_id, (parent_id, version, values) in self.expected.items():
            layer = canvas._get_layer_by_id(layer_id)
            if (
                layer is None
                or layer.parent_id != parent_id
                or (self._initial_preflight and layer.texture_version != version)
                or any(getattr(layer, key) != value for key, value in values.items())
            ):
                self.last_error = "panel sources changed during preview"
                return False
        self._executed = True
        self._initial_preflight = False
        return True

    def revert(self, canvas: Any) -> bool:
        del canvas
        self._executed = False
        return True
