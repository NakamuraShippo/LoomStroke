"""Keyboard shortcut management utilities."""

from __future__ import annotations

import importlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QKeySequence

from ..host_api import (
    ShortcutActionInfo,
    ShortcutSchemeValidation,
    ShortcutService,
)
from .settings_manager import SettingsManager

jsonschema: Any | None
try:  # pragma: no cover - optional dependency
    jsonschema = importlib.import_module("jsonschema")
except ImportError:  # pragma: no cover - exercised when jsonschema absent
    jsonschema = None


_SCHEME_VERSION = 1
_MNEMONIC_SUFFIX_RE = re.compile(r"\s*\(&.\)")
_SCHEME_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "LoomStrokeShortcutScheme",
    "type": "object",
    "properties": {
        "version": {
            "type": "integer",
            "minimum": 1,
        },
        "actions": {
            "type": "object",
            "additionalProperties": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "null"},
                ]
            },
        },
    },
    "required": ["actions"],
    "additionalProperties": False,
}

if jsonschema is not None:
    _SCHEME_VALIDATOR = jsonschema.Draft202012Validator(_SCHEME_SCHEMA)
else:  # pragma: no cover - optional dependency missing
    _SCHEME_VALIDATOR = None


@dataclass(slots=True)
class ShortcutAction:
    """Public representation of a shortcut that can be rebound."""

    action_id: str
    label: str
    category: str
    description: str | None
    default: str | None
    current: str | None
    owner: str | None = None


@dataclass(slots=True)
class _ShortcutDefinition:
    label: _RegisteredText
    category: _RegisteredText
    description: _RegisteredText | None
    default: str | None
    owner: _RegisteredText | None


@dataclass(slots=True)
class _RegisteredText:
    source_text: str
    domain: str | None = None
    strip_mnemonics: bool = False

    def translate(self) -> str:
        if not self.domain:
            translated = self.source_text
        else:
            from ..ui.translations import translate_with_fallback  # noqa: PLC0415

            translated = translate_with_fallback(self.domain, self.source_text)
        if self.strip_mnemonics:
            return _strip_mnemonic_text(translated)
        return translated


def _source_text(value: str) -> str:
    source = getattr(value, "source_text", None)
    if isinstance(source, str) and source:
        return source
    return str(value)


def _source_domain(value: str | None) -> str | None:
    domain = getattr(value, "translation_domain", None)
    if isinstance(domain, str) and domain:
        return domain
    return None


def _normalize_registered_text(
    value: str | None,
    *,
    strip_mnemonics: bool = False,
    strip_whitespace: bool = False,
) -> _RegisteredText | None:
    if value is None:
        return None
    source = _source_text(value)
    if strip_whitespace:
        source = source.strip()
    if not source:
        return None
    return _RegisteredText(
        source_text=source,
        domain=_source_domain(value),
        strip_mnemonics=strip_mnemonics,
    )


def _strip_mnemonic_text(text: str) -> str:
    escaped = "\0"
    normalized = text.replace("&&", escaped)
    normalized = _MNEMONIC_SUFFIX_RE.sub("", normalized)
    normalized = normalized.replace("&", "")
    return normalized.replace(escaped, "&").strip()


def _portable_sequence(sequence: str | QKeySequence | None) -> str | None:
    """Normalize a shortcut representation into portable Qt text."""

    if sequence is None:
        return None
    if isinstance(sequence, QKeySequence):
        text = sequence.toString(QKeySequence.SequenceFormat.PortableText)
        return text or None
    text = sequence.strip()
    if not text:
        return None
    portable = QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText).toString(
        QKeySequence.SequenceFormat.PortableText
    )
    if portable:
        return portable
    native = QKeySequence(text).toString(QKeySequence.SequenceFormat.PortableText)
    return native or text


def _normalize_sequence_value(value: Any, action_id: str) -> tuple[str | None, str | None]:
    """Normalize a raw shortcut value into canonical Qt portable text."""

    if value is None:
        return None, None
    if not isinstance(value, str):
        return None, f"Shortcut for '{action_id}' must be a string or null."
    portable = _portable_sequence(value)
    if portable is None:
        return None, None
    qt_sequence = QKeySequence.fromString(
        portable,
        QKeySequence.SequenceFormat.PortableText,
    )
    normalized = qt_sequence.toString(QKeySequence.SequenceFormat.PortableText)
    if not normalized:
        return None, f"Shortcut '{value}' for '{action_id}' is not recognized."
    return normalized, None


def _normalize_scheme_payload(payload: Any) -> tuple[dict[str, Any] | None, list[str]]:
    """Normalize incoming scheme payloads into the canonical structure."""

    if not isinstance(payload, Mapping):
        return None, ["Shortcut scheme payload must be a JSON object."]

    errors: list[str] = []
    version_value: Any | None = None
    actions_source: Mapping[Any, Any]
    if "actions" in payload:
        actions_source = payload["actions"]
        version_value = payload.get("version")
    else:
        actions_source = payload

    if not isinstance(actions_source, Mapping):
        return None, ['"actions" must be an object mapping action ids to shortcuts.']

    normalized_actions: dict[str, str | None] = {}
    for raw_action_id, raw_value in actions_source.items():
        if not isinstance(raw_action_id, str):
            errors.append("Shortcut action ids must be strings.")
            continue
        action_id = raw_action_id.strip()
        if not action_id:
            errors.append("Shortcut action ids must not be empty.")
            continue
        sequence, seq_error = _normalize_sequence_value(raw_value, action_id)
        if seq_error:
            errors.append(seq_error)
            continue
        normalized_actions[action_id] = sequence

    if version_value is None:
        version = _SCHEME_VERSION
    elif isinstance(version_value, int) and version_value >= 1:
        version = version_value
    else:
        errors.append('"version" must be an integer greater than or equal to 1.')
        version = _SCHEME_VERSION

    return {"version": version, "actions": normalized_actions}, errors


class ShortcutManager(QObject):
    """Tracks registered actions and persists user-defined shortcuts."""

    shortcut_changed = Signal(str, str)

    def __init__(self, settings: SettingsManager) -> None:
        super().__init__()
        self._settings = settings
        self._definitions: dict[str, _ShortcutDefinition] = {}

    # ------------------------------------------------------------------
    def register_action(
        self,
        action_id: str,
        *,
        label: str,
        default: str | None = None,
        category: str = "General",
        description: str | None = None,
        owner: str | None = None,
    ) -> str | None:
        """Declare an action that can be rebound and return its sequence."""

        normalized_label = _normalize_registered_text(label, strip_mnemonics=True)
        if normalized_label is None:
            normalized_label = _RegisteredText(source_text=action_id)
        normalized_default = _portable_sequence(default)
        normalized_category = _normalize_registered_text(category)
        if normalized_category is None:
            normalized_category = _RegisteredText(source_text="General")
        normalized_description = _normalize_registered_text(description)
        normalized_owner = _normalize_registered_text(owner, strip_whitespace=True)
        if normalized_owner is None and "." in action_id:
            normalized_owner = _RegisteredText(source_text=action_id.split(".", 1)[0])
        definition = self._definitions.get(action_id)
        if definition is None:
            self._definitions[action_id] = _ShortcutDefinition(
                label=normalized_label,
                category=normalized_category,
                description=normalized_description,
                default=normalized_default,
                owner=normalized_owner,
            )
        else:
            definition.label = normalized_label
            definition.category = normalized_category
            definition.description = normalized_description
            if normalized_default:
                definition.default = normalized_default
            if normalized_owner:
                definition.owner = normalized_owner
        return self.get_sequence(action_id)

    def get_sequence(self, action_id: str) -> str | None:
        """Return the effective shortcut for the given action."""

        stored = self._settings.get(self._key(action_id))
        if isinstance(stored, str) and stored.strip():
            return stored
        definition = self._definitions.get(action_id)
        return definition.default if definition else None

    def set_shortcut(
        self,
        action_id: str,
        sequence: str | QKeySequence | None,
    ) -> None:
        """Persist a new shortcut (or clear it when None)."""

        portable = _portable_sequence(sequence)
        definition = self._definitions.get(action_id)
        key = self._key(action_id)
        if (not portable) or (definition and portable == (definition.default or "")):
            self._settings.remove(key)
        else:
            self._settings.set(key, portable)
        self._emit_changed(action_id)

    def reset_shortcut(self, action_id: str) -> None:
        """Clear a user override and fall back to the default."""

        self._settings.remove(self._key(action_id))
        self._emit_changed(action_id)

    def list_actions(self) -> list[ShortcutAction]:
        """Return all registered actions sorted by category and label."""

        entries: list[ShortcutAction] = []
        for action_id, definition in self._definitions.items():
            label = definition.label.translate()
            category = definition.category.translate()
            description = definition.description.translate() if definition.description else None
            owner = definition.owner.translate() if definition.owner else None
            entries.append(
                ShortcutAction(
                    action_id=action_id,
                    label=label,
                    category=category,
                    description=description,
                    default=definition.default,
                    current=self.get_sequence(action_id),
                    owner=owner,
                )
            )
        entries.sort(key=lambda entry: (entry.category.lower(), entry.label.lower()))
        return entries

    def validate_scheme(
        self,
        payload: Any,
        *,
        require_known: bool = True,
    ) -> ShortcutSchemeValidation:
        """Validate a shortcut scheme payload before import/export."""

        normalized, errors = _normalize_scheme_payload(payload)
        warnings: list[str] = []
        if normalized is None:
            return ShortcutSchemeValidation(False, None, tuple(errors), tuple())

        if _SCHEME_VALIDATOR is not None:
            try:
                _SCHEME_VALIDATOR.validate(normalized)
            except Exception as exc:  # pragma: no cover - defensive guard
                errors.append(f"Schema validation failed: {exc}")
        else:
            warnings.append("jsonschema not available; schema checks skipped.")

        actions = normalized["actions"]
        if self._definitions:
            unknown_ids = sorted(
                action_id for action_id in actions if action_id not in self._definitions
            )
            if unknown_ids:
                message = "Unknown shortcut action(s): " + ", ".join(unknown_ids)
                if require_known:
                    errors.append(message)
                else:
                    warnings.append(message)

        valid = not errors
        normalized_actions = dict(actions) if valid else None
        return ShortcutSchemeValidation(valid, normalized_actions, tuple(errors), tuple(warnings))

    def export_scheme(self) -> dict[str, str]:
        """Export the effective shortcut mapping for sharing."""

        scheme: dict[str, str] = {}
        for entry in self.list_actions():
            scheme[entry.action_id] = entry.current or ""
        return scheme

    def import_scheme(
        self,
        mapping: Mapping[str, str | None],
        *,
        clear_unlisted: bool = False,
    ) -> None:
        """Apply a shortcut mapping previously created via :meth:`export_scheme`."""

        validation = self.validate_scheme(mapping, require_known=True)
        if not validation.valid or validation.actions is None:
            detail = "; ".join(validation.errors) or "Unknown validation error"
            raise ValueError(f"Invalid shortcut scheme: {detail}")

        actions = validation.actions
        for action_id, sequence in actions.items():
            self.set_shortcut(action_id, sequence)

        if clear_unlisted:
            known_ids = set(self._definitions)
            for action_id in known_ids - set(actions):
                self.reset_shortcut(action_id)

    # ------------------------------------------------------------------
    def _emit_changed(self, action_id: str) -> None:
        sequence = self.get_sequence(action_id) or ""
        self.shortcut_changed.emit(action_id, sequence)

    @staticmethod
    def _key(action_id: str) -> str:
        return f"shortcuts/{action_id}"


class ShortcutServiceAdapter(ShortcutService):
    """Adapter exposing :class:`ShortcutManager` through the HostAPI protocol."""

    def __init__(self, manager: ShortcutManager) -> None:
        self._manager = manager

    def register_action(
        self,
        action_id: str,
        *,
        label: str,
        default: str | None = None,
        category: str = "General",
        description: str | None = None,
        owner: str | None = None,
    ) -> str | None:
        return self._manager.register_action(
            action_id,
            label=label,
            default=default,
            category=category,
            description=description,
            owner=owner,
        )

    def set_shortcut(self, action_id: str, sequence: str | None) -> None:
        self._manager.set_shortcut(action_id, sequence)

    def reset_shortcut(self, action_id: str) -> None:
        self._manager.reset_shortcut(action_id)

    def list_actions(self) -> Sequence[ShortcutActionInfo]:
        entries = self._manager.list_actions()
        return tuple(
            ShortcutActionInfo(
                action_id=entry.action_id,
                label=entry.label,
                category=entry.category,
                description=entry.description,
                default=entry.default,
                current=entry.current,
                owner=entry.owner,
            )
            for entry in entries
        )

    def validate_scheme(
        self,
        payload: Any,
        *,
        require_known: bool = False,
    ) -> ShortcutSchemeValidation:
        return self._manager.validate_scheme(payload, require_known=require_known)


__all__ = ["ShortcutAction", "ShortcutManager", "ShortcutServiceAdapter"]
