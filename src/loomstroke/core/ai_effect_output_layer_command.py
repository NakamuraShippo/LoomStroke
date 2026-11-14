"""Restore one generated result containing images and optional 3D layers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from inspect import getattr_static
from typing import TYPE_CHECKING, Any

from .ai_effect_result_payload import (
    _prepare_result,
    _prepare_result_restore,
    _PreparedResult,
    _ResultImage,
)
from .canvas_layer_model import _Layer
from .command_types import Command
from .imported_3d_layer import _prepare_generated_3d_output_layer, _retained_scene_bytes
from .layer_command_commit_context import _LayerCommandContext
from .layer_history_memory import estimate_retained_layer_bytes
from .layer_tree_service import LayerTreeQueryService, sync_active_markers

if TYPE_CHECKING:
    from loomstroke.plugins.ai_effect.data_model import LastResult


def _standard_output_canvas(canvas: Any) -> bool:
    from .canvas_service_simple_filter_api import _DEFAULT_AI_OUTPUT_CREATE  # noqa: PLC0415

    create = getattr_static(canvas, "create_ai_effect_output_layer", None)
    return create is _DEFAULT_AI_OUTPUT_CREATE and not any(
        layer.native_cmyk is not None for layer in canvas._flatten_layers()
    )


@dataclass(frozen=True)
class _NewLayerOutput:
    name: str
    slot_id: str | None
    png: bytes


def _result_identity(last: LastResult) -> tuple[str, datetime, str]:
    return last.param_hash, last.finished_at, last.mode


class _CreateAIEffectOutputLayersCommand(Command):
    def __init__(
        self,
        layer_id: str,
        outputs: tuple[_NewLayerOutput, ...],
        *,
        self_png: bytes | None = None,
        model_layers: tuple[dict[str, Any], ...] = (),
    ) -> None:
        super().__init__()
        self.layer_id = layer_id
        self._outputs = outputs
        self._layers: tuple[_Layer, ...] = ()
        self._model_outputs = model_layers
        self._models: tuple[_Layer, ...] = ()
        self._model_start: int | None = None
        self._active_before: str | None = None
        self._parent_id: str | None = None
        self._generation: tuple[str, datetime, str] | None = None
        self._self_png = self_png
        self._image_payload: tuple[_ResultImage, _ResultImage] | None = None
        self._initial_image: _PreparedResult | None = None

    @property
    def retained_bytes(self) -> int:
        return (
            sum(estimate_retained_layer_bytes(layer) for layer in self._new_layers())
            + sum(_retained_scene_bytes(layer.scene_3d) for layer in self._models)
            + sum(image.retained_bytes for image in self._image_payload or ())
        )

    def _new_layers(self) -> tuple[_Layer, ...]:
        return self._layers + self._models

    def _prepare(
        self, canvas: Any, source: _Layer, scope: _LayerCommandContext, generation_index: int
    ) -> bool:
        materializer = canvas._filter_apply()._ai_output_materializer
        layers = []
        for output in self._outputs:
            layer = materializer.prepare_ai_effect_output_layer(
                source,
                name=output.name,
                png_bytes=output.png,
                output_internal_id=output.slot_id,
                generation_index=generation_index,
            )
            if layer is None:
                return False
            layers.append(layer)
        models = []
        for values in self._model_outputs:
            model = _prepare_generated_3d_output_layer(canvas, **values)
            if model is None:
                return False
            models.append(model)
        if not (layers or models) or len({layer.id for layer in (*layers, *models)}) != len(
            layers
        ) + len(models):
            return False
        if self._self_png is not None:
            self._image_payload, self._initial_image = _prepare_result(
                canvas, source, scope, self._self_png
            )
        self._layers, self._parent_id = tuple(layers), source.parent_id
        self._models = tuple(models)
        self._outputs, self._model_outputs, self._self_png = (), (), None
        return True

    def _remember_last_result(self, last: LastResult) -> None:
        self._generation = _result_identity(last)
        self._update_last_result(last, added=True)

    def _update_last_result(self, last: LastResult | None, *, added: bool) -> None:
        # Runtime LastResult objects can be rebuilt when a page is revisited.
        if last is None or self._generation != _result_identity(last):
            return
        identifiers = [layer.id for layer in self._layers]
        owned = set(identifiers)
        remaining = [identifier for identifier in last.output_layer_ids if identifier not in owned]
        last.output_layer_ids = [*remaining, *identifiers] if added else remaining

    def apply(self, canvas: Any) -> bool:
        return self._restore(canvas, use_after=True)

    def revert(self, canvas: Any) -> bool:
        return self._restore(canvas, use_after=False)

    def _restore(self, canvas: Any, *, use_after: bool) -> bool:
        if self._executed == use_after:
            return False
        payload = self._image_payload
        prepared = self._initial_image
        if payload is not None and prepared is None:
            source = canvas._find_layer_by_id_tree(self.layer_id)
            if type(source) is not _Layer or source.ai_effect is None:
                return False
            prepared = _prepare_result_restore(
                canvas,
                source,
                _LayerCommandContext(canvas, (source,)),
                payload[int(use_after)],
                original_format=not use_after,
            )
        try:
            return (prepared is None or prepared.install()) and self._restore_layers(
                canvas, use_after=use_after
            )
        except Exception:
            if self._executed == use_after:
                self._restore_layers(canvas, use_after=not use_after)
            raise
        finally:
            if prepared is not None and self._executed != use_after:
                self._rollback_image(prepared)

    def _initial_image_is_current(self) -> bool:
        prepared = self._initial_image
        return prepared is None or (prepared.installed is not None and prepared.installed.current())

    def _initial_image_is_installed(self) -> bool:
        return self._initial_image is not None and self._initial_image.installed is not None

    def _rollback_initial_application(self, canvas: Any) -> bool:
        prepared = self._initial_image
        if self._initial_image_is_installed() and not self._initial_image_is_current():
            return False
        if self._executed and not self._restore_layers(canvas, use_after=False):
            return False
        return prepared is None or self._rollback_image(prepared)

    @staticmethod
    def _rollback_image(prepared: _PreparedResult) -> bool:
        if prepared.installed is not None and not prepared.rollback():
            return False
        prepared.installed = None
        return True

    def _restore_layers(self, canvas: Any, *, use_after: bool) -> bool:
        if not self._new_layers() or self._executed == use_after:
            return False
        location = canvas._find_parent(self.layer_id)
        if location is None:
            return False
        siblings, source_index = location
        source = siblings[source_index]
        if source.layer_type != "ai_effect" or source.parent_id != self._parent_id:
            return False
        if use_after:
            active = self._attach_layers(canvas, siblings, source_index + 1)
        else:
            active = self._detach_layers(canvas, siblings, source_index + 1, source.id)
        if active is None:
            return False
        self._executed = use_after
        canvas._active_layer_id = active
        sync_active_markers(canvas, LayerTreeQueryService(canvas))
        canvas._invalidate_layer_cache()
        self._update_last_result(getattr(source.ai_effect, "last_result", None), added=use_after)
        return True

    def _attach_layers(self, canvas: Any, siblings: list[_Layer], start: int) -> str | None:
        if any(canvas._find_layer_by_id_tree(layer.id) is not None for layer in self._new_layers()):
            return None
        active = canvas.current_layer().id
        # Each existing image output was inserted immediately above the source.
        siblings[start:start] = reversed(self._layers)
        if not self._models:
            return active
        roots = canvas._layers
        if self._model_start is None:
            self._model_start = len(roots)
            self._active_before = active
        model_start = min(self._model_start, len(roots))
        roots[model_start:model_start] = self._models
        self._model_start = model_start
        return self._models[-1].id

    def _detach_layers(
        self, canvas: Any, siblings: list[_Layer], start: int, source_id: str
    ) -> str | None:
        stop = start + len(self._layers)
        live = tuple(reversed(siblings[start:stop]))
        if len(live) != len(self._layers) or any(
            layer.id != previous.id or layer.origin != previous.origin
            for layer, previous in zip(live, self._layers, strict=True)
        ):
            return None
        active = canvas.current_layer().id
        if self._models:
            model_start = self._model_start
            if model_start is None:
                return None
            roots = canvas._layers
            model_stop = model_start + len(self._models)
            live_models = tuple(roots[model_start:model_stop])
            if len(live_models) != len(self._models) or any(
                layer.id != previous.id or layer.layer_type != "3d"
                for layer, previous in zip(live_models, self._models, strict=True)
            ):
                return None
            # Remove models first because the image siblings may share the root list.
            self._models = live_models
            del roots[model_start:model_stop]
            active = self._active_before or source_id
        # Page hydration can rebuild the live layers. Retain their current
        # pixels and IDs so redo restores the same generated content.
        self._layers = live
        del siblings[start:stop]
        return active if canvas._find_layer_by_id_tree(active) is not None else source_id
