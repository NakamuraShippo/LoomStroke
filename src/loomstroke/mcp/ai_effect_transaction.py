"""AI Effect transaction contract for Canvas Agent changeset apply."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from loomstroke.core.commands import Command
from loomstroke.plugins.ai_effect.data_model import AIEffectState

from .ai_effect_state import (
    find_live_ai_effect_layer,
    make_ai_effect_state_backup,
    restore_ai_effect_state_backup,
)
from .safety import scrub_secrets

AI_EFFECT_TRANSACTION_SCHEMA_VERSION = "ai_effect_apply_transaction.v1"

_VALID_TRANSACTION_OPS = {"create_ai_effect_layer", "set_ai_effect_state"}
_VALID_REVERT_OPS = {"remove_layer", "restore_ai_effect_state", "set_ai_effect_state"}
_DENIED_KEYS = {
    "apikey",
    "api_key",
    "binarypayload",
    "credential",
    "credentialvalue",
    "imagebytes",
    "password",
    "pngbytes",
    "providerrequestbody",
    "rawprompt",
    "requestbody",
    "secret",
    "sessiontoken",
    "sourceprompt",
    "token",
}


class AIEffectTransactionError(ValueError):
    """Raised when an AI Effect transaction is unsafe or malformed."""


class AIEffectPresetApplyCommand(Command):
    """Apply an already-prepared AI Effect transaction as an undoable command."""

    def __init__(self, operation: Mapping[str, Any]) -> None:
        super().__init__()
        self.operation = scrub_secrets(dict(operation))
        self.transaction = validate_ai_effect_apply_transaction(
            _transaction_from_operation(operation),
            operation=operation,
        )
        self.transaction_id = str(self.transaction.get("transactionId") or "")
        self.preset_id = str(self.transaction.get("presetId") or "")
        self._created_layer_ids: list[str] = []
        self._changed_layer_ids: list[str] = []
        self._operation_records: list[dict[str, Any]] = []
        self._warnings: list[str] = []
        self._applied: list[tuple[str, tuple[Any, ...]]] = []
        self._layer_id_map: dict[str, str] = {}

    @property
    def created_layer_ids(self) -> list[str]:
        return list(dict.fromkeys(self._created_layer_ids))

    @property
    def changed_layer_ids(self) -> list[str]:
        return list(dict.fromkeys(self._changed_layer_ids))

    @property
    def operation_records(self) -> list[dict[str, Any]]:
        return [dict(record) for record in self._operation_records]

    @property
    def warnings(self) -> list[str]:
        return list(dict.fromkeys(self._warnings))

    def apply(self, canvas: Any) -> bool:
        self._reset_runtime_state()
        try:
            for index, transaction_op in enumerate(_transaction_operations(self.transaction)):
                op_name = str(transaction_op.get("op") or "")
                if op_name == "create_ai_effect_layer":
                    self._apply_create_ai_effect_layer(canvas, transaction_op, index=index)
                    continue
                if op_name == "set_ai_effect_state":
                    self._apply_set_ai_effect_state(canvas, transaction_op, index=index)
                    continue
                raise RuntimeError(f"unsupported_ai_effect_transaction_op:{op_name}")
            self._executed = True
            return True
        except Exception as exc:
            self._warnings.append(f"ai_effect_transaction_failed:{type(exc).__name__}")
            self.revert(canvas)
            self._executed = False
            return False

    def revert(self, canvas: Any) -> bool:
        ok = True
        for kind, payload in reversed(self._applied):
            try:
                if kind == "remove_layer":
                    (layer_id,) = payload
                    remover = getattr(canvas, "remove_layer", None)
                    ok = bool(callable(remover) and remover(layer_id)) and ok
                elif kind == "restore_ai_effect_state":
                    layer_id, backup = payload
                    layer = find_live_ai_effect_layer(canvas, layer_id)
                    restore_ai_effect_state_backup(canvas, layer_id, layer.ai_effect, backup)
                else:
                    ok = False
            except Exception:
                ok = False
        self._applied = []
        self._executed = False
        return ok

    def _reset_runtime_state(self) -> None:
        self._created_layer_ids = []
        self._changed_layer_ids = []
        self._operation_records = []
        self._warnings = []
        self._applied = []
        self._layer_id_map = {}

    def _apply_create_ai_effect_layer(
        self,
        canvas: Any,
        transaction_op: Mapping[str, Any],
        *,
        index: int,
    ) -> None:
        adder = getattr(canvas, "add_ai_effect_layer", None)
        if not callable(adder):
            raise RuntimeError("add_ai_effect_layer_unavailable")
        logical_id = _required_str(transaction_op, "layerId")
        state = _state_from_operation(transaction_op)
        name = str(transaction_op.get("name") or "AI Effect")
        parent_group_id = _optional_str(transaction_op.get("parentGroupId"))
        position = _int_or_default(transaction_op.get("position"), -1)
        actual_id = adder(
            state,
            name=name,
            parent_group_id=parent_group_id,
            position=position,
        )
        actual_id_str = str(actual_id or "").strip()
        if not actual_id_str:
            raise RuntimeError("ai_effect_layer_creation_failed")
        self._layer_id_map[logical_id] = actual_id_str
        self._created_layer_ids.append(actual_id_str)
        self._changed_layer_ids.append(actual_id_str)
        self._applied.append(("remove_layer", (actual_id_str,)))
        self._operation_records.append(
            {
                "index": index,
                "operation": "create_ai_effect_layer",
                "recordType": "ai_effect_layer",
                "recordId": actual_id_str,
                "transactionId": self.transaction_id,
                "presetId": self.preset_id,
                "logicalLayerId": logical_id,
                "schemaVersion": AI_EFFECT_TRANSACTION_SCHEMA_VERSION,
            }
        )

    def _apply_set_ai_effect_state(
        self,
        canvas: Any,
        transaction_op: Mapping[str, Any],
        *,
        index: int,
    ) -> None:
        logical_id = _required_str(transaction_op, "layerId")
        layer_id = self._layer_id_map.get(logical_id, logical_id)
        layer = find_live_ai_effect_layer(canvas, layer_id)
        backup = make_ai_effect_state_backup(layer.ai_effect)
        state = _state_from_operation(transaction_op)
        setter = getattr(canvas, "set_ai_effect_layer_state", None)
        if not callable(setter):
            raise RuntimeError("set_ai_effect_layer_state_unavailable")
        ok = setter(
            layer_id,
            param_values=copy.deepcopy(state.param_values),
            slot_refs=copy.deepcopy(state.slot_refs),
            param_ui_state=copy.deepcopy(state.param_ui_state),
        )
        if not ok:
            raise RuntimeError("set_ai_effect_layer_state_failed")
        output_setter = getattr(canvas, "set_ai_effect_layer_output_mode_override", None)
        if callable(output_setter) and not output_setter(layer_id, state.output_mode_override):
            raise RuntimeError("set_ai_effect_output_override_failed")
        self._applied.append(("restore_ai_effect_state", (layer_id, backup)))
        self._changed_layer_ids.append(layer_id)
        self._operation_records.append(
            {
                "index": index,
                "operation": "set_ai_effect_state",
                "recordType": "ai_effect_state",
                "recordId": layer_id,
                "transactionId": self.transaction_id,
                "presetId": self.preset_id,
                "logicalLayerId": logical_id,
                "schemaVersion": AI_EFFECT_TRANSACTION_SCHEMA_VERSION,
            }
        )


def validate_ai_effect_apply_transaction(  # noqa: PLR0912
    transaction: Any,
    *,
    operation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a safe transaction copy or raise ``AIEffectTransactionError``."""

    if not isinstance(transaction, Mapping):
        raise AIEffectTransactionError("transaction_required")
    _reject_denied_payload(transaction)
    safe = scrub_secrets(copy.deepcopy(dict(transaction)))
    if safe.get("schemaVersion") != AI_EFFECT_TRANSACTION_SCHEMA_VERSION:
        raise AIEffectTransactionError("schema_version_invalid")
    transaction_id = str(safe.get("transactionId") or "").strip()
    if not transaction_id:
        raise AIEffectTransactionError("transaction_id_required")
    preset_id = str(safe.get("presetId") or "").strip()
    if not preset_id:
        raise AIEffectTransactionError("preset_id_required")
    if operation is not None:
        operation_preset = str(operation.get("presetId") or "").strip()
        if operation_preset and operation_preset != preset_id:
            raise AIEffectTransactionError("preset_id_mismatch")

    created_layer_ids = _string_list(safe.get("createdLayerIds"))
    changed_layer_ids = _string_list(safe.get("changedLayerIds"))
    if not changed_layer_ids:
        raise AIEffectTransactionError("changed_layer_ids_required")

    safety = safe.get("safety")
    if not isinstance(safety, Mapping):
        raise AIEffectTransactionError("safety_required")
    if safety.get("externalGenerationStarted") is not False:
        raise AIEffectTransactionError("external_generation_started")
    if safety.get("credentialReadPerformed") is not False:
        raise AIEffectTransactionError("credential_read_performed")
    if safety.get("binaryPayloadOmitted") is not True:
        raise AIEffectTransactionError("binary_payload_not_omitted")

    operations = _transaction_operations(safe)
    if not operations:
        raise AIEffectTransactionError("operations_required")
    for index, transaction_op in enumerate(operations):
        _validate_transaction_operation(
            transaction_op,
            index=index,
            created_layer_ids=created_layer_ids,
            changed_layer_ids=changed_layer_ids,
        )
    return safe


def operation_has_valid_ai_effect_apply_transaction(operation: Mapping[str, Any]) -> bool:
    try:
        validate_ai_effect_apply_transaction(
            _transaction_from_operation(operation),
            operation=operation,
        )
    except AIEffectTransactionError:
        return False
    return True


def ai_effect_transaction_summary(transaction: Mapping[str, Any]) -> dict[str, Any]:
    safe = validate_ai_effect_apply_transaction(transaction)
    return {
        "schemaVersion": AI_EFFECT_TRANSACTION_SCHEMA_VERSION,
        "transactionId": safe.get("transactionId"),
        "presetId": safe.get("presetId"),
        "targetLayerId": safe.get("targetLayerId"),
        "createdLayerIds": _string_list(safe.get("createdLayerIds")),
        "changedLayerIds": _string_list(safe.get("changedLayerIds")),
        "operationCount": len(_transaction_operations(safe)),
        "preview": scrub_secrets(safe.get("preview") or {}),
        "safety": {
            "externalGenerationStarted": False,
            "credentialReadPerformed": False,
            "binaryPayloadOmitted": True,
        },
    }


def _transaction_from_operation(operation: Mapping[str, Any]) -> Any:
    return operation.get("aiEffectTransaction") or operation.get("transaction")


def _validate_transaction_operation(
    transaction_op: Mapping[str, Any],
    *,
    index: int,
    created_layer_ids: Sequence[str],
    changed_layer_ids: Sequence[str],
) -> None:
    op_name = str(transaction_op.get("op") or "").strip()
    if op_name not in _VALID_TRANSACTION_OPS:
        raise AIEffectTransactionError(f"operation_{index}_unsupported")
    layer_id = _required_str(transaction_op, "layerId")
    if layer_id not in changed_layer_ids:
        raise AIEffectTransactionError(f"operation_{index}_changed_layer_missing")
    revert = transaction_op.get("revert")
    if not isinstance(revert, Mapping):
        raise AIEffectTransactionError(f"operation_{index}_revert_required")
    revert_op = str(revert.get("op") or "").strip()
    if revert_op not in _VALID_REVERT_OPS:
        raise AIEffectTransactionError(f"operation_{index}_revert_invalid")
    if op_name == "create_ai_effect_layer":
        if layer_id not in created_layer_ids:
            raise AIEffectTransactionError(f"operation_{index}_created_layer_missing")
        if revert_op != "remove_layer":
            raise AIEffectTransactionError(f"operation_{index}_create_revert_invalid")
    elif revert_op not in {"restore_ai_effect_state", "set_ai_effect_state"}:
        raise AIEffectTransactionError(f"operation_{index}_state_revert_invalid")
    if str(revert.get("layerId") or "").strip() != layer_id:
        raise AIEffectTransactionError(f"operation_{index}_revert_layer_mismatch")
    _state_from_operation(transaction_op)


def _state_from_operation(transaction_op: Mapping[str, Any]) -> AIEffectState:
    raw_state = transaction_op.get("state") or transaction_op.get("newState")
    if not isinstance(raw_state, Mapping):
        raise AIEffectTransactionError("ai_effect_state_required")
    try:
        return AIEffectState.from_dict(copy.deepcopy(dict(raw_state)))
    except Exception as exc:
        raise AIEffectTransactionError(f"ai_effect_state_invalid:{type(exc).__name__}") from exc


def _transaction_operations(transaction: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    operations = transaction.get("operations")
    if not isinstance(operations, Sequence) or isinstance(operations, str | bytes | bytearray):
        return []
    return [op for op in operations if isinstance(op, Mapping)]


def _reject_denied_payload(value: Any, path: str = "$") -> None:
    if isinstance(value, bytes | bytearray | memoryview):
        raise AIEffectTransactionError(f"binary_payload:{path}")
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_str = str(key)
            if _normal_key(key_str) in _DENIED_KEYS:
                raise AIEffectTransactionError(f"denied_key:{path}.{key_str}")
            _reject_denied_payload(child, f"{path}.{key_str}")
        return
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        for index, child in enumerate(value):
            _reject_denied_payload(child, f"{path}[{index}]")


def _normal_key(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum() or ch == "_")


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _required_str(mapping: Mapping[str, Any], key: str) -> str:
    value = str(mapping.get(key) or "").strip()
    if not value:
        raise AIEffectTransactionError(f"{key}_required")
    return value


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


__all__ = [
    "AI_EFFECT_TRANSACTION_SCHEMA_VERSION",
    "AIEffectPresetApplyCommand",
    "AIEffectTransactionError",
    "ai_effect_transaction_summary",
    "operation_has_valid_ai_effect_apply_transaction",
    "validate_ai_effect_apply_transaction",
]
