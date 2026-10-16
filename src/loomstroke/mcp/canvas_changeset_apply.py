"""Command-backed CanvasChangeset apply helpers."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from loomstroke.core.commands import (
    Command,
    DuplicateLayerCommand,
    SelectionOp,
    UpdateSelectionMaskCommand,
)
from loomstroke.plugins.text_tool.common import TextToolSettings, ensure_text_vector_object
from loomstroke.plugins.vector_tools.common import (
    VectorObject,
    ensure_vector_element_ids,
    normalize_rgb,
)

from .ai_effect_transaction import (
    AIEffectPresetApplyCommand,
    operation_has_valid_ai_effect_apply_transaction,
)
from .canvas_changeset import CHANGESET_SCHEMA_VERSION
from .canvas_shapes import build_canvas_shape
from .safety import scrub_secrets

APPLY_RESULT_SCHEMA_VERSION = "canvas_changeset_apply_result.v1"
VERIFY_SCHEMA_VERSION = "canvas_edit_verification.v1"

SUPPORTED_APPLY_OPERATIONS = frozenset(
    {
        "create_layer",
        "duplicate_layer",
        "rename_layer",
        "set_layer_opacity",
        "set_layer_blend_mode",
        "add_text_object",
        "add_vector_shape",
        "import_asset_as_layer",
        "submit_generation_to_new_layer",
        "apply_selection_mask",
        "apply_ai_effect_preset",
        "panel_frame_edit",
    }
)


class CanvasChangesetApplyCommand(Command):
    """Apply CanvasChangeset operations as one undoable canvas command."""

    vector_object = object()

    def __init__(
        self,
        changeset: Mapping[str, Any],
        *,
        asset_images: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__()
        self.changeset = scrub_secrets(dict(changeset))
        self._asset_images = dict(asset_images or {})
        self.apply_id = f"apply_{uuid.uuid4().hex[:12]}"
        self._layer_id = "__loomstroke_canvas_changeset_apply__"
        self._applied: list[tuple[str, tuple[Any, ...]]] = []
        self._created_layer_ids: list[str] = []
        self._changed_layer_ids: list[str] = []
        self._created_asset_ids: list[str] = []
        self._operation_records: list[dict[str, Any]] = []
        self._warnings: list[str] = []
        self._panel_frame_commands: dict[int, Command] = {}

    @property
    def created_layer_ids(self) -> list[str]:
        return list(dict.fromkeys(self._created_layer_ids))

    @property
    def changed_layer_ids(self) -> list[str]:
        return list(dict.fromkeys(self._changed_layer_ids))

    @property
    def created_asset_ids(self) -> list[str]:
        return list(dict.fromkeys(self._created_asset_ids))

    @property
    def operation_records(self) -> list[dict[str, Any]]:
        return [dict(record) for record in self._operation_records]

    @property
    def warnings(self) -> list[str]:
        return list(dict.fromkeys(self._warnings))

    def apply(self, canvas: Any) -> bool:
        self._reset_runtime_state()
        layer_id_map: dict[str, str] = {}
        try:
            for index, operation in enumerate(_operation_list(self.changeset)):
                self._apply_operation(canvas, operation, index=index, layer_id_map=layer_id_map)
            self._bump_texture_versions(canvas)
            self._executed = True
            return True
        except Exception as exc:
            self._warnings.append(f"apply_failed:{type(exc).__name__}")
            if not self._rollback(canvas):
                self._warnings.append("rollback_failed")
            self._executed = False
            return False

    def revert(self, canvas: Any) -> bool:
        if not self._executed:
            return False
        reverted = self._rollback(canvas)
        self._executed = False
        return reverted

    def _reset_runtime_state(self) -> None:
        self._applied = []
        self._created_layer_ids = []
        self._changed_layer_ids = []
        self._created_asset_ids = []
        self._operation_records = []
        self._warnings = []

    def _apply_operation(  # noqa: PLR0911
        self,
        canvas: Any,
        operation: Mapping[str, Any],
        *,
        index: int,
        layer_id_map: dict[str, str],
    ) -> None:
        op_name = _operation_name(operation)
        if op_name not in SUPPORTED_APPLY_OPERATIONS:
            raise RuntimeError(f"unsupported_apply_operation:{op_name}")
        if op_name == "panel_frame_edit":
            from .panel_frame_changeset import apply_frame_operation  # noqa: PLC0415

            command, changed = apply_frame_operation(
                canvas,
                operation,
                self._panel_frame_commands.get(index),
                asset_images=self._asset_images,
            )
            self._panel_frame_commands[index] = command
            self._applied.append(("command", (command,)))
            self._changed_layer_ids.extend(changed)
            self._operation_records.append(
                {"index": index, "operation": op_name, "panelId": operation.get("panelId")}
            )
            return
        if op_name == "create_layer":
            self._apply_create_layer(canvas, operation, index=index, layer_id_map=layer_id_map)
            return
        if op_name == "duplicate_layer":
            self._apply_duplicate_layer(canvas, operation, index=index, layer_id_map=layer_id_map)
            return
        if op_name == "rename_layer":
            self._apply_rename_layer(canvas, operation, index=index, layer_id_map=layer_id_map)
            return
        if op_name == "set_layer_opacity":
            self._apply_set_layer_opacity(
                canvas,
                operation,
                index=index,
                layer_id_map=layer_id_map,
            )
            return
        if op_name == "set_layer_blend_mode":
            self._apply_set_layer_blend_mode(
                canvas,
                operation,
                index=index,
                layer_id_map=layer_id_map,
            )
            return
        if op_name == "add_text_object":
            self._apply_add_text_object(canvas, operation, index=index, layer_id_map=layer_id_map)
            return
        if op_name == "add_vector_shape":
            self._apply_add_vector_shape(canvas, operation, index=index, layer_id_map=layer_id_map)
            return
        if op_name in {"import_asset_as_layer", "submit_generation_to_new_layer"}:
            self._apply_import_asset_as_layer(
                canvas,
                operation,
                index=index,
                layer_id_map=layer_id_map,
            )
            return
        if op_name == "apply_selection_mask":
            self._apply_selection_mask(canvas, operation, index=index)
            return
        if op_name == "apply_ai_effect_preset":
            self._apply_ai_effect_preset(canvas, operation, index=index)

    def _apply_create_layer(
        self,
        canvas: Any,
        operation: Mapping[str, Any],
        *,
        index: int,
        layer_id_map: dict[str, str],
    ) -> None:
        requested_id = str(operation.get("layerId") or "").strip()
        if requested_id and _get_layer(canvas, requested_id) is not None:
            raise RuntimeError("layer_id_conflict")
        name = str(operation.get("name") or "Canvas Agent Layer")
        layer_type = str(operation.get("layerType") or "raster").strip().lower()
        vector_kind = str(operation.get("vectorKind") or "draw").strip().lower()
        ref: Any = None
        if layer_type == "vector":
            restore = getattr(canvas, "restore_vector_layer", None)
            if requested_id and callable(restore):
                ref = restore(layer_id=requested_id, name=name, vector_kind=vector_kind)
            if ref is None:
                add_vector_layer = getattr(canvas, "add_vector_layer", None)
                if callable(add_vector_layer):
                    ref = add_vector_layer(name, vector_kind=vector_kind)
        else:
            add_layer = getattr(canvas, "add_layer", None)
            if callable(add_layer):
                ref = add_layer(name, layer_type=layer_type or "raster")
        actual_id = str(getattr(ref, "id", "") or "")
        if not actual_id:
            raise RuntimeError("layer_creation_failed")
        if requested_id:
            layer_id_map[requested_id] = actual_id
        self._created_layer_ids.append(actual_id)
        self._changed_layer_ids.append(actual_id)
        self._applied.append(("remove_layer", (actual_id,)))
        self._operation_records.append(
            {
                "index": index,
                "operation": "create_layer",
                "recordType": "layer",
                "recordId": actual_id,
                "requestedLayerId": requested_id or None,
                "layerType": layer_type or "raster",
                "vectorKind": vector_kind if layer_type == "vector" else None,
            }
        )

    def _apply_duplicate_layer(
        self,
        canvas: Any,
        operation: Mapping[str, Any],
        *,
        index: int,
        layer_id_map: dict[str, str],
    ) -> None:
        source_id = _resolve_layer_id(operation.get("sourceLayerId"), layer_id_map)
        if not source_id:
            raise RuntimeError("source_layer_required")
        command = DuplicateLayerCommand(source_id)
        if not command.apply(canvas):
            raise RuntimeError("duplicate_layer_failed")
        actual_id = str(getattr(command, "_new_layer_id", "") or "")
        if not actual_id:
            raise RuntimeError("duplicate_layer_missing_id")
        requested_id = str(operation.get("newLayerId") or "").strip()
        if requested_id:
            layer_id_map[requested_id] = actual_id
        source_layer = _get_layer(canvas, source_id)
        new_layer = _get_layer(canvas, actual_id)
        if source_layer is not None and new_layer is not None:
            new_layer.vector_kind = str(getattr(source_layer, "vector_kind", "draw") or "draw")
        new_name = str(operation.get("name") or "").strip()
        if new_name and new_layer is not None:
            new_layer.name = new_name
        self._applied.append(("command", (command,)))
        self._created_layer_ids.append(actual_id)
        self._changed_layer_ids.append(actual_id)
        self._operation_records.append(
            {
                "index": index,
                "operation": "duplicate_layer",
                "recordType": "layer",
                "recordId": actual_id,
                "sourceLayerId": source_id,
                "requestedLayerId": requested_id or None,
            }
        )

    def _apply_rename_layer(
        self,
        canvas: Any,
        operation: Mapping[str, Any],
        *,
        index: int,
        layer_id_map: Mapping[str, str],
    ) -> None:
        layer_id = _resolve_layer_id(operation.get("layerId"), layer_id_map)
        layer = _require_layer(canvas, layer_id)
        old_name = str(getattr(layer, "name", "") or "")
        new_name = str(operation.get("name") or "").strip()
        if not new_name:
            raise RuntimeError("layer_name_required")
        layer.name = new_name
        self._publish_layer_changed(canvas, layer, "name", old_name, new_name)
        self._applied.append(("rename", (layer_id, old_name)))
        self._changed_layer_ids.append(layer_id)
        self._operation_records.append(
            {
                "index": index,
                "operation": "rename_layer",
                "recordType": "layer_property",
                "recordId": layer_id,
                "property": "name",
            }
        )

    def _apply_set_layer_opacity(
        self,
        canvas: Any,
        operation: Mapping[str, Any],
        *,
        index: int,
        layer_id_map: Mapping[str, str],
    ) -> None:
        layer_id = _resolve_layer_id(operation.get("layerId"), layer_id_map)
        layer = _require_layer(canvas, layer_id)
        old_opacity = float(getattr(layer, "opacity", 1.0))
        new_opacity = max(0.0, min(1.0, float(operation.get("opacity"))))
        setter = getattr(canvas, "set_layer_opacity", None)
        if not callable(setter):
            raise RuntimeError("set_layer_opacity_unavailable")
        setter(layer_id, new_opacity)
        self._applied.append(("opacity", (layer_id, old_opacity)))
        self._changed_layer_ids.append(layer_id)
        self._operation_records.append(
            {
                "index": index,
                "operation": "set_layer_opacity",
                "recordType": "layer_property",
                "recordId": layer_id,
                "property": "opacity",
            }
        )

    def _apply_set_layer_blend_mode(
        self,
        canvas: Any,
        operation: Mapping[str, Any],
        *,
        index: int,
        layer_id_map: Mapping[str, str],
    ) -> None:
        layer_id = _resolve_layer_id(operation.get("layerId"), layer_id_map)
        layer = _require_layer(canvas, layer_id)
        old_mode = str(getattr(getattr(layer, "blend_mode", None), "value", "normal"))
        new_mode = str(operation.get("blendMode") or "normal")
        setter = getattr(canvas, "set_layer_blend_mode", None)
        if not callable(setter):
            raise RuntimeError("set_layer_blend_mode_unavailable")
        setter(layer_id, new_mode)
        self._applied.append(("blend_mode", (layer_id, old_mode)))
        self._changed_layer_ids.append(layer_id)
        self._operation_records.append(
            {
                "index": index,
                "operation": "set_layer_blend_mode",
                "recordType": "layer_property",
                "recordId": layer_id,
                "property": "blend_mode",
            }
        )

    def _apply_add_text_object(
        self,
        canvas: Any,
        operation: Mapping[str, Any],
        *,
        index: int,
        layer_id_map: Mapping[str, str],
    ) -> None:
        layer_id = _resolve_layer_id(operation.get("layerId"), layer_id_map)
        _require_vector_layer(canvas, layer_id)
        text = str(operation.get("text") or "")[:2000]
        settings = _text_settings(operation.get("style"))
        obj = ensure_text_vector_object(
            object_id=_object_id(operation, "text", index),
            content=text,
            rect=_rect_tuple(operation.get("rect")),
            settings=settings,
            word_wrap=True,
        )
        meta = dict(obj.meta)
        meta["canvas_agent"] = {
            "schemaVersion": "canvas_agent_vector_object.v1",
            "role": "text",
            "operationId": operation.get("operationId"),
            "changesetId": self.changeset.get("changesetId"),
        }
        obj = ensure_vector_element_ids(
            VectorObject(
                id=obj.id,
                paths=obj.paths,
                style=obj.style,
                meta=meta,
                preview=False,
            ),
            object_index=index,
        )
        self._insert_vector_object(canvas, layer_id, obj, index=index, operation="add_text_object")

    def _apply_add_vector_shape(
        self,
        canvas: Any,
        operation: Mapping[str, Any],
        *,
        index: int,
        layer_id_map: Mapping[str, str],
    ) -> None:
        layer_id = _resolve_layer_id(operation.get("layerId"), layer_id_map)
        _require_vector_layer(canvas, layer_id)
        obj = ensure_vector_element_ids(
            _shape_object(operation, object_id=_object_id(operation, "shape", index)),
            object_index=index,
        )
        self._insert_vector_object(canvas, layer_id, obj, index=index, operation="add_vector_shape")

    def _apply_import_asset_as_layer(
        self,
        canvas: Any,
        operation: Mapping[str, Any],
        *,
        index: int,
        layer_id_map: dict[str, str],
    ) -> None:
        asset_id = _operation_asset_id(operation)
        if not asset_id:
            raise RuntimeError("asset_id_required_for_apply")
        image = self._asset_images.get(asset_id)
        if image is None:
            raise RuntimeError("asset_image_unavailable")
        layer_id = str(operation.get("newLayerId") or "").strip()
        name = str(operation.get("name") or "Canvas Agent Asset").strip()
        add_layer = getattr(canvas, "add_layer", None)
        if not callable(add_layer):
            raise RuntimeError("add_layer_unavailable")
        ref = add_layer(name or "Canvas Agent Asset", layer_type="raster")
        actual_id = str(getattr(ref, "id", "") or "")
        if not actual_id:
            raise RuntimeError("asset_layer_creation_failed")
        if layer_id:
            layer_id_map[layer_id] = actual_id
        layer = _require_layer(canvas, actual_id)
        copy = getattr(image, "copy", None)
        layer.image = copy() if callable(copy) else image
        opacity = operation.get("opacity")
        if opacity is not None:
            layer.opacity = max(0.0, min(1.0, float(opacity)))
        blend_mode = str(operation.get("blendMode") or "").strip()
        if blend_mode:
            setter = getattr(canvas, "set_layer_blend_mode", None)
            if callable(setter):
                setter(actual_id, blend_mode)
        self._created_layer_ids.append(actual_id)
        self._changed_layer_ids.append(actual_id)
        self._applied.append(("remove_layer", (actual_id,)))
        self._operation_records.append(
            {
                "index": index,
                "operation": _operation_name(operation),
                "recordType": "asset_layer",
                "recordId": actual_id,
                "assetId": asset_id,
                "requestedLayerId": layer_id or None,
            }
        )

    def _apply_selection_mask(
        self,
        canvas: Any,
        operation: Mapping[str, Any],
        *,
        index: int,
    ) -> None:
        rect = _selection_rect_tuple(operation.get("rect"))
        if rect is None:
            raise RuntimeError("selection_rect_required")
        command = UpdateSelectionMaskCommand(
            rect,
            _selection_operation(operation.get("operation")),
            source="canvas_agent",
        )
        if not command.apply(canvas):
            raise RuntimeError("selection_apply_failed")
        self._applied.append(("command", (command,)))
        self._operation_records.append(
            {
                "index": index,
                "operation": "apply_selection_mask",
                "recordType": "selection",
                "recordId": str(operation.get("selectionId") or "__selection_mask__"),
                "rect": rect,
                "selectionOperation": str(command.operation.value),
            }
        )

    def _apply_ai_effect_preset(
        self,
        canvas: Any,
        operation: Mapping[str, Any],
        *,
        index: int,
    ) -> None:
        command = AIEffectPresetApplyCommand(operation)
        if not command.apply(canvas):
            raise RuntimeError("ai_effect_transaction_apply_failed")
        self._applied.append(("command", (command,)))
        self._created_layer_ids.extend(command.created_layer_ids)
        self._changed_layer_ids.extend(command.changed_layer_ids)
        self._warnings.extend(command.warnings)
        self._operation_records.append(
            {
                "index": index,
                "operation": "apply_ai_effect_preset",
                "recordType": "ai_effect_transaction",
                "recordId": command.transaction_id,
                "presetId": command.preset_id,
                "createdLayerIds": command.created_layer_ids,
                "changedLayerIds": command.changed_layer_ids,
                "schemaVersion": "ai_effect_apply_transaction.v1",
                "nestedRecords": command.operation_records,
            }
        )

    def _insert_vector_object(
        self,
        canvas: Any,
        layer_id: str,
        obj: VectorObject,
        *,
        index: int,
        operation: str,
    ) -> None:
        add_vector = getattr(canvas, "add_vector_object", None)
        if not callable(add_vector):
            raise RuntimeError("add_vector_object_unavailable")
        result = add_vector(obj, layer_id=layer_id, recompose=False)
        if result is None:
            raise RuntimeError("add_vector_object_failed")
        actual_layer_id, insert_index = result
        actual_layer_id = str(actual_layer_id)
        object_id = str(getattr(obj, "id", "") or "")
        self._applied.append(("vector", (actual_layer_id, int(insert_index), object_id)))
        self._changed_layer_ids.append(actual_layer_id)
        self._operation_records.append(
            {
                "index": index,
                "operation": operation,
                "recordType": "vector_object",
                "recordId": object_id,
                "layerId": actual_layer_id,
            }
        )

    def _rollback(self, canvas: Any) -> bool:  # noqa: PLR0912
        ok = True
        for kind, payload in reversed(self._applied):
            try:
                if kind == "vector":
                    layer_id, index, object_id = payload
                    remove_vector = getattr(canvas, "remove_vector_object", None)
                    ok = (
                        bool(
                            callable(remove_vector)
                            and remove_vector(layer_id, index, recompose=False, vec_id=object_id)
                        )
                        and ok
                    )
                elif kind == "opacity":
                    layer_id, old_opacity = payload
                    setter = getattr(canvas, "set_layer_opacity", None)
                    ok = bool(callable(setter)) and ok
                    if callable(setter):
                        setter(layer_id, old_opacity)
                elif kind == "opacity_direct":
                    layer_id, old_opacity = payload
                    layer = _get_layer(canvas, layer_id)
                    ok = (layer is not None) and ok
                    if layer is not None:
                        layer.opacity = float(old_opacity)
                elif kind == "blend_mode":
                    layer_id, old_mode = payload
                    setter = getattr(canvas, "set_layer_blend_mode", None)
                    ok = bool(callable(setter)) and ok
                    if callable(setter):
                        setter(layer_id, old_mode)
                elif kind == "rename":
                    layer_id, old_name = payload
                    layer = _get_layer(canvas, layer_id)
                    ok = (layer is not None) and ok
                    if layer is not None:
                        current_name = str(getattr(layer, "name", "") or "")
                        layer.name = old_name
                        self._publish_layer_changed(canvas, layer, "name", current_name, old_name)
                elif kind == "command":
                    (command,) = payload
                    ok = bool(command.revert(canvas)) and ok
                elif kind == "remove_layer":
                    (layer_id,) = payload
                    remove_layer = getattr(canvas, "remove_layer", None)
                    ok = bool(callable(remove_layer) and remove_layer(layer_id)) and ok
            except Exception:
                ok = False
        self._applied = []
        self._bump_texture_versions(canvas)
        return ok

    def _bump_texture_versions(self, canvas: Any) -> None:
        layer_ids = self.changed_layer_ids
        bump = getattr(canvas, "bump_layer_texture_versions", None)
        if callable(bump) and layer_ids:
            bump(layer_ids)
        invalidate = getattr(canvas, "_invalidate_layer_cache", None)
        if callable(invalidate):
            invalidate()
        recompose = getattr(canvas, "_recompose", None)
        if callable(recompose):
            recompose()

    @staticmethod
    def _publish_layer_changed(
        canvas: Any,
        layer: Any,
        change_type: str,
        old_value: Any,
        new_value: Any,
    ) -> None:
        publish = getattr(canvas, "_publish_layer_event", None)
        if not callable(publish):
            return
        from loomstroke.core.events import Events  # noqa: PLC0415

        publish(
            Events.LAYER_CHANGED,
            layer=layer,
            change_type=change_type,
            old_value=old_value,
            new_value=new_value,
        )


def unsupported_apply_operations(changeset: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Return CanvasChangeset operations that Section 4 cannot apply yet."""

    unsupported: list[dict[str, Any]] = []
    for index, operation in enumerate(_operation_list(changeset)):
        op_name = _operation_name(operation)
        if op_name == "apply_ai_effect_preset":
            if not operation_has_valid_ai_effect_apply_transaction(operation):
                unsupported.append({"index": index, "operation": op_name or "<missing>"})
            continue
        if op_name not in SUPPORTED_APPLY_OPERATIONS:
            unsupported.append({"index": index, "operation": op_name or "<missing>"})
    return unsupported


def asset_ids_for_apply(changeset: Mapping[str, Any]) -> list[str]:
    """Return selected asset ids required before Command-backed apply."""

    asset_ids: list[str] = []
    for operation in _operation_list(changeset):
        if _operation_name(operation) in {
            "import_asset_as_layer",
            "submit_generation_to_new_layer",
        } or (
            _operation_name(operation) == "panel_frame_edit"
            and operation.get("action") == "place_image"
        ):
            asset_id = _operation_asset_id(operation)
            if asset_id:
                asset_ids.append(asset_id)
    return list(dict.fromkeys(asset_ids))


def build_apply_result(
    changeset: Mapping[str, Any],
    command: CanvasChangesetApplyCommand,
    *,
    post_context: Mapping[str, Any],
    history_label: str,
) -> dict[str, Any]:
    """Build the secret-free CanvasChangeset apply result schema."""

    return scrub_secrets(
        {
            "schemaVersion": APPLY_RESULT_SCHEMA_VERSION,
            "applyId": command.apply_id,
            "changesetId": changeset.get("changesetId"),
            "status": "applied",
            "undoGroupId": command.apply_id,
            "changedLayerIds": command.changed_layer_ids,
            "createdLayerIds": command.created_layer_ids,
            "createdAssetIds": command.created_asset_ids,
            "postApplyPreviewAssetId": None,
            "postApplyContext": _post_apply_context(post_context),
            "warnings": command.warnings,
            "historyLabel": history_label,
            "documentMutation": "command_backed",
            "operationRecords": command.operation_records,
        }
    )


def build_verification_result(
    apply_result: Mapping[str, Any],
    context: Mapping[str, Any],
) -> dict[str, Any]:
    """Return a read-only verification summary for a Canvas Agent apply result."""

    layers = context.get("layers") if isinstance(context.get("layers"), list) else []
    live_layer_ids = {
        str(layer.get("layerId") or "")
        for layer in layers
        if isinstance(layer, Mapping) and str(layer.get("layerId") or "")
    }
    changed_layer_ids = [
        str(layer_id) for layer_id in apply_result.get("changedLayerIds", []) if str(layer_id)
    ]
    missing = [layer_id for layer_id in changed_layer_ids if layer_id not in live_layer_ids]
    status = "verified" if not missing else "changed_layers_missing"
    return scrub_secrets(
        {
            "schemaVersion": VERIFY_SCHEMA_VERSION,
            "status": status,
            "applyId": apply_result.get("applyId"),
            "changesetId": apply_result.get("changesetId"),
            "currentDocumentRevision": context.get("contextDigest"),
            "appliedDocumentRevision": (
                (apply_result.get("postApplyContext") or {}).get("contextDigest")
                if isinstance(apply_result.get("postApplyContext"), Mapping)
                else None
            ),
            "changedLayerIds": changed_layer_ids,
            "missingLayerIds": missing,
            "warnings": ([] if not missing else ["changed_layer_missing_or_undone_after_apply"]),
        }
    )


def _post_apply_context(context: Mapping[str, Any]) -> dict[str, Any]:
    canvas = context.get("canvas") if isinstance(context.get("canvas"), Mapping) else {}
    return {
        "contextId": context.get("contextId"),
        "contextDigest": context.get("contextDigest"),
        "canvas": {
            "width": canvas.get("width"),
            "height": canvas.get("height"),
            "activeLayerId": canvas.get("activeLayerId"),
            "layerCacheVersion": canvas.get("layerCacheVersion"),
        },
        "layerSummary": scrub_secrets(context.get("layerSummary") or {}),
    }


def _operation_list(changeset: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    if str(changeset.get("schemaVersion") or "") != CHANGESET_SCHEMA_VERSION:
        return []
    operations = changeset.get("operations")
    if not isinstance(operations, list):
        return []
    return [operation for operation in operations if isinstance(operation, Mapping)]


def _operation_name(operation: Mapping[str, Any]) -> str:
    return str(operation.get("op") or operation.get("kind") or "").strip()


def _operation_asset_id(operation: Mapping[str, Any]) -> str:
    return str(operation.get("assetId") or operation.get("selectedAssetId") or "").strip()


def _resolve_layer_id(value: Any, layer_id_map: Mapping[str, str]) -> str:
    requested = str(value or "").strip()
    return layer_id_map.get(requested, requested)


def _get_layer(canvas: Any, layer_id: str) -> Any | None:
    if not layer_id:
        return None
    for name in ("_get_layer_by_id", "_find_layer_by_id_tree", "get_layer_by_id"):
        getter = getattr(canvas, name, None)
        if callable(getter):
            layer = getter(layer_id)
            if layer is not None:
                return layer
    return None


def _require_layer(canvas: Any, layer_id: str) -> Any:
    layer = _get_layer(canvas, layer_id)
    if layer is None:
        raise RuntimeError("target_layer_missing")
    if bool(getattr(layer, "locked", False)):
        raise RuntimeError("target_layer_locked")
    return layer


def _require_vector_layer(canvas: Any, layer_id: str) -> Any:
    layer = _require_layer(canvas, layer_id)
    if str(getattr(layer, "layer_type", "") or "") != "vector":
        raise RuntimeError("target_layer_not_vector")
    return layer


def _text_settings(style_value: Any) -> TextToolSettings:
    style = dict(style_value) if isinstance(style_value, Mapping) else {}
    color = normalize_rgb(style.get("color") or style.get("colorRgb") or style.get("fillColor"))
    if color is None:
        color = (17, 17, 17)
    return TextToolSettings(
        word_wrap=True,
        font_family=str(style.get("fontFamily") or ""),
        font_style=str(style.get("fontStyle") or ""),
        font_size_pt=float(_number(style.get("fontSize") or style.get("font_size_pt"), 32.0)),
        color_rgba=(*color, 255),
        align=str(style.get("alignment") or style.get("align") or "left"),
        line_height=float(_number(style.get("lineHeight"), 1.1)),
        padding_top=float(_number(style.get("paddingTop"), 0.0)),
        padding_right=float(_number(style.get("paddingRight"), 0.0)),
        padding_bottom=float(_number(style.get("paddingBottom"), 0.0)),
        padding_left=float(_number(style.get("paddingLeft"), 0.0)),
    )


def _shape_object(operation: Mapping[str, Any], *, object_id: str) -> VectorObject:
    return build_canvas_shape(operation, object_id=object_id)


def _rect_tuple(value: Any) -> tuple[float, float, float, float]:
    if isinstance(value, Mapping):
        return (
            float(_number(value.get("x"), 0.0)),
            float(_number(value.get("y"), 0.0)),
            max(1.0, float(_number(value.get("width"), 1.0))),
            max(1.0, float(_number(value.get("height"), 1.0))),
        )
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        parts = list(value)
        if len(parts) >= 4:
            return (
                float(_number(parts[0], 0.0)),
                float(_number(parts[1], 0.0)),
                max(1.0, float(_number(parts[2], 1.0))),
                max(1.0, float(_number(parts[3], 1.0))),
            )
    return (0.0, 0.0, 1.0, 1.0)


def _selection_rect_tuple(value: Any) -> tuple[int, int, int, int] | None:
    if isinstance(value, Mapping):
        width_value = value.get("width", value.get("w"))
        height_value = value.get("height", value.get("h"))
        parts = (value.get("x"), value.get("y"), width_value, height_value)
    elif isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        parts = tuple(value[:4]) if len(value) >= 4 else ()
    else:
        return None
    if len(parts) != 4:
        return None
    x = int(_number(parts[0], 0.0))
    y = int(_number(parts[1], 0.0))
    width = int(_number(parts[2], 0.0))
    height = int(_number(parts[3], 0.0))
    if width <= 0 or height <= 0:
        return None
    return (x, y, width, height)


def _selection_operation(value: Any) -> SelectionOp:
    try:
        return SelectionOp(str(value or "replace").strip().lower())
    except ValueError:
        return SelectionOp.REPLACE


def _object_id(operation: Mapping[str, Any], prefix: str, index: int) -> str:
    explicit = str(operation.get("objectId") or "").strip()
    if explicit:
        return explicit
    seed = str(operation.get("operationId") or f"op_{index}")
    return f"canvas-agent-{prefix}-{uuid.uuid5(uuid.NAMESPACE_URL, seed).hex[:12]}"


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


__all__ = [
    "APPLY_RESULT_SCHEMA_VERSION",
    "SUPPORTED_APPLY_OPERATIONS",
    "VERIFY_SCHEMA_VERSION",
    "CanvasChangesetApplyCommand",
    "asset_ids_for_apply",
    "build_apply_result",
    "build_verification_result",
    "unsupported_apply_operations",
]
