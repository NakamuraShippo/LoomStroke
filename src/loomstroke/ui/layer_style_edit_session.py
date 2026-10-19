"""Group a modal live style edit into one command-backed history transaction."""

from __future__ import annotations

from loomstroke.core.appearance import LayerStyleStack

from .appearance_service import AppearanceService


class LayerStyleEditSession:
    def __init__(self, service: AppearanceService, layer_id: str) -> None:
        self._service = service
        self._layer_id = layer_id
        self._active = False

    def apply(self, stack: LayerStyleStack) -> bool:
        if self._service.layer(self._layer_id) is None:
            return False
        if stack == self._service.styles_for(self._layer_id):
            return True
        if not self._active:
            self._service._begin("Layer Styles")
            self._active = True
        return self._service.set_styles(self._layer_id, stack, transaction=False)

    def finish(self) -> None:
        if self._active:
            self._active = False
            self._service._end()

    def ready(self) -> bool:
        view = getattr(self._service._host, "_canvas_widget", None)
        presenter = getattr(view, "_native_presenter", None)
        if presenter is None:
            presenter = getattr(view, "_wgpu_bitmap_presenter", None)
        projection = getattr(presenter, "_native_cmyk_projection", None)
        # Cold device/style preparation may take seconds. Accept current input
        # during it; the display worker rejects superseded scene snapshots.
        # Keep normal render-time coalescing so continuous edits make progress.
        return bool(getattr(projection, "preparing", False)) or not bool(
            getattr(projection, "pending", False)
        )
