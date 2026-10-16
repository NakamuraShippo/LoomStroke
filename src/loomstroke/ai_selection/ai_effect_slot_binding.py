"""Adapter from SAM3 mask assets to AI Effect slot bindings."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .assets import Sam3AssetStore
from .layer_mask_adapter import (
    Sam3LayerMaskAdapterError,
    build_layer_mask_image_from_asset,
)

if TYPE_CHECKING:
    from PySide6.QtGui import QImage

logger = logging.getLogger(__name__)


class Sam3AIEffectSlotBindingError(ValueError):
    """Raised when a SAM3 mask asset cannot be bound to an AI Effect slot."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


def build_ai_effect_mask_slot_binding_command(
    asset_store: Sam3AssetStore,
    asset_id: str,
    *,
    effect_layer_id: str,
    slot_id: str,
    canvas_width: int,
    canvas_height: int,
    mask_layer_name: str | None = None,
    exclude_blend: bool = True,
) -> Any:
    """Build a command that binds a SAM3 mask asset to an AI Effect slot.

    The command materializes the SAM3 asset as a normal mask layer and
    stores a regular ``SlotRef(layer_id=...)`` in ``AIEffectState.slot_refs``.
    This keeps the persisted AI Effect data model unchanged.
    """

    target_effect_id = _require_non_empty(
        effect_layer_id,
        code="target_ai_effect_layer_missing",
        message="SAM3 AI Effect slot binding requires a target AI Effect layer id.",
    )
    target_slot_id = _require_non_empty(
        slot_id,
        code="target_slot_missing",
        message="SAM3 AI Effect slot binding requires a target slot id.",
    )
    try:
        mask_image = build_layer_mask_image_from_asset(
            asset_store,
            asset_id,
            canvas_width=canvas_width,
            canvas_height=canvas_height,
        )
    except Sam3LayerMaskAdapterError as exc:
        raise Sam3AIEffectSlotBindingError(exc.code, str(exc)) from exc

    return _Sam3AIEffectMaskSlotBindingCommand(
        effect_layer_id=target_effect_id,
        slot_id=target_slot_id,
        mask_image=mask_image,
        mask_layer_name=(
            str(mask_layer_name).strip()
            if mask_layer_name is not None and str(mask_layer_name).strip()
            else "SAM3 Mask"
        ),
        exclude_blend=bool(exclude_blend),
    )


class _Sam3AIEffectMaskSlotBindingCommand:
    sam3_command_kind = "ai_effect_mask_slot_binding"

    def __init__(
        self,
        *,
        effect_layer_id: str,
        slot_id: str,
        mask_image: QImage,
        mask_layer_name: str,
        exclude_blend: bool,
    ) -> None:
        self.merge_key = None
        self._effect_layer_id = effect_layer_id
        self._slot_id = slot_id
        self._mask_image = mask_image.copy()
        self._mask_layer_name = mask_layer_name
        self._exclude_blend = exclude_blend
        self._mask_layer_id: str | None = None
        self._prev_param_values: dict[str, Any] | None = None
        self._prev_slot_refs: dict[str, Any] | None = None
        self._prev_param_ui_state: dict[str, Any] | None = None
        self._applied = False

    @property
    def mask_layer_id(self) -> str | None:
        """Return the mask layer created by the last successful apply."""

        return self._mask_layer_id

    def apply(self, canvas: Any) -> bool:
        effect_layer = self._require_effect_layer(canvas)
        ai_state = self._require_effect_state(effect_layer)
        self._snapshot_state(ai_state)

        mask_layer_id = self._create_mask_layer(canvas)
        if mask_layer_id is None:
            self._restore_state(canvas)
            self._activate_effect_layer(canvas)
            return False
        self._mask_layer_id = mask_layer_id

        if not self._write_mask_layer_image(canvas, mask_layer_id):
            self._remove_mask_layer(canvas)
            self._restore_state(canvas)
            self._activate_effect_layer(canvas)
            return False

        slot_refs = dict(self._prev_slot_refs or {})
        from loomstroke.plugins.ai_effect.data_model import SlotRef  # noqa: PLC0415

        slot_refs[self._slot_id] = SlotRef(
            layer_id=mask_layer_id,
            exclude_blend=self._exclude_blend,
        )
        if not self._set_ai_effect_state(
            canvas,
            param_values=self._prev_param_values or {},
            slot_refs=slot_refs,
            param_ui_state=self._prev_param_ui_state,
        ):
            self._remove_mask_layer(canvas)
            self._restore_state(canvas)
            self._activate_effect_layer(canvas)
            return False

        self._applied = True
        self._activate_effect_layer(canvas)
        self._invalidate_and_recompose(canvas)
        return True

    def revert(self, canvas: Any) -> bool:
        if not self._applied:
            return False
        restored = self._restore_state(canvas)
        removed = self._remove_mask_layer(canvas)
        if restored and removed:
            self._applied = False
            self._activate_effect_layer(canvas)
            self._invalidate_and_recompose(canvas)
            return True
        return False

    def _require_effect_layer(self, canvas: Any) -> Any:
        layer = _resolve_layer_by_id(canvas, self._effect_layer_id)
        if layer is None or getattr(layer, "layer_type", None) != "ai_effect":
            raise Sam3AIEffectSlotBindingError(
                "target_ai_effect_layer_missing",
                "SAM3 AI Effect slot binding target is not available.",
            )
        return layer

    @staticmethod
    def _require_effect_state(layer: Any) -> Any:
        ai_state = getattr(layer, "ai_effect", None)
        if ai_state is None:
            raise Sam3AIEffectSlotBindingError(
                "target_ai_effect_state_missing",
                "SAM3 AI Effect slot binding target has no AI Effect state.",
            )
        return ai_state

    def _snapshot_state(self, ai_state: Any) -> None:
        self._prev_param_values = dict(getattr(ai_state, "param_values", {}) or {})
        self._prev_slot_refs = dict(getattr(ai_state, "slot_refs", {}) or {})
        raw_param_ui_state = getattr(ai_state, "param_ui_state", {}) or {}
        self._prev_param_ui_state = dict(raw_param_ui_state)

    def _create_mask_layer(self, canvas: Any) -> str | None:
        add_mask_layer = getattr(canvas, "add_mask_layer", None)
        if callable(add_mask_layer):
            ref = add_mask_layer(self._mask_layer_name)
        else:
            add_layer = getattr(canvas, "add_layer", None)
            if not callable(add_layer):
                raise Sam3AIEffectSlotBindingError(
                    "mask_layer_create_rejected",
                    "SAM3 AI Effect slot binding could not create a mask layer.",
                )
            ref = add_layer(self._mask_layer_name, layer_type="mask")
        layer_id = getattr(ref, "id", None)
        if not isinstance(layer_id, str) or not layer_id:
            raise Sam3AIEffectSlotBindingError(
                "mask_layer_create_rejected",
                "SAM3 AI Effect slot binding could not create a mask layer.",
            )
        return layer_id

    def _write_mask_layer_image(self, canvas: Any, mask_layer_id: str) -> bool:
        layer = _resolve_layer_by_id(canvas, mask_layer_id)
        if layer is None or getattr(layer, "layer_type", None) != "mask":
            return False
        layer.image = self._mask_image.copy()
        texture_version = getattr(layer, "texture_version", 0)
        if isinstance(texture_version, int):
            layer.texture_version = texture_version + 1
        return True

    def _set_ai_effect_state(
        self,
        canvas: Any,
        *,
        param_values: dict[str, Any],
        slot_refs: dict[str, Any],
        param_ui_state: dict[str, Any] | None,
    ) -> bool:
        set_state = getattr(canvas, "set_ai_effect_layer_state", None)
        if not callable(set_state):
            return False
        return bool(
            set_state(
                self._effect_layer_id,
                param_values=dict(param_values),
                slot_refs=dict(slot_refs),
                param_ui_state=dict(param_ui_state or {}),
            )
        )

    def _restore_state(self, canvas: Any) -> bool:
        if self._prev_param_values is None or self._prev_slot_refs is None:
            return False
        return self._set_ai_effect_state(
            canvas,
            param_values=self._prev_param_values,
            slot_refs=self._prev_slot_refs,
            param_ui_state=self._prev_param_ui_state,
        )

    def _remove_mask_layer(self, canvas: Any) -> bool:
        if self._mask_layer_id is None:
            return True
        remove_layer = getattr(canvas, "remove_layer", None)
        if not callable(remove_layer):
            return False
        ok = bool(remove_layer(self._mask_layer_id))
        if ok:
            self._mask_layer_id = None
        return ok

    def _activate_effect_layer(self, canvas: Any) -> None:
        set_active_layer = getattr(canvas, "set_active_layer", None)
        if not callable(set_active_layer):
            return
        try:
            set_active_layer(self._effect_layer_id)
        except Exception:
            logger.debug(
                "SAM3 AI Effect slot binding target activation failed",
                exc_info=True,
            )

    @staticmethod
    def _invalidate_and_recompose(canvas: Any) -> None:
        invalidate = getattr(canvas, "_invalidate_layer_cache", None)
        if callable(invalidate):
            invalidate()
        recompose = getattr(canvas, "_recompose", None)
        if callable(recompose):
            recompose()


def _require_non_empty(value: str, *, code: str, message: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise Sam3AIEffectSlotBindingError(code, message)
    return normalized


def _resolve_layer_by_id(canvas: Any, layer_id: str) -> Any | None:
    for method_name in ("_find_layer_by_id_tree", "_get_layer_by_id"):
        finder = getattr(canvas, method_name, None)
        if callable(finder):
            try:
                layer = finder(layer_id)
            except Exception:
                layer = None
            if layer is not None:
                return layer
    roots = getattr(canvas, "_layers", None)
    if isinstance(roots, list):
        return _walk_layers(roots, layer_id)
    return None


def _walk_layers(layers: list[Any], layer_id: str) -> Any | None:
    for layer in layers:
        if getattr(layer, "id", None) == layer_id:
            return layer
        children = getattr(layer, "children", None)
        if isinstance(children, list):
            found = _walk_layers(children, layer_id)
            if found is not None:
                return found
    return None


__all__ = [
    "Sam3AIEffectSlotBindingError",
    "build_ai_effect_mask_slot_binding_command",
]
