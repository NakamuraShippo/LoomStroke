"""Action and shortcut coordination for MainWindow."""

from __future__ import annotations

import logging
from collections.abc import Callable
from contextlib import suppress
from typing import Any

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut

from .translations import translate_runtime_text

logger = logging.getLogger(__name__)


class MainWindowActionShortcutCoordinator:
    """Coordinate QAction creation and shortcut runtime metadata."""

    def __init__(self, window: Any) -> None:
        self._window = window

    def initialize_shortcut_components(
        self,
        shortcut_manager: Any | None,
        *,
        shortcut_manager_type: Callable[[Any], Any],
        shortcut_service_adapter_type: Callable[[Any], Any],
    ) -> None:
        """Initialize MainWindow shortcut manager, service, and runtime metadata."""
        window = self._window
        window._shortcut_manager = shortcut_manager or shortcut_manager_type(window._settings)
        window._shortcut_manager.shortcut_changed.connect(window._on_shortcut_changed)
        window._shortcut_service = shortcut_service_adapter_type(window._shortcut_manager)
        attach_shortcuts = getattr(window.host, "attach_shortcut_service", None)
        if callable(attach_shortcuts):
            with suppress(Exception):
                attach_shortcuts(window._shortcut_service)
        window._shortcut_actions = {}
        window._shortcut_labels = {}
        window._shortcut_defaults = {}
        window._custom_shortcuts = set()
        window._last_hovered_shortcut = None
        window._shortcut_focus_shortcut = QShortcut(QKeySequence("Ctrl+Alt+,"), window)
        window._shortcut_focus_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
        window._shortcut_focus_shortcut.activated.connect(window._open_hover_shortcut_preferences)
        window._shortcut_bindings = {}

    def make_action(
        self,
        text: str,
        slot: Callable[..., Any] | None = None,
        shortcut: str | None = None,
        tip: str | None = None,
        checkable: bool = False,
        checked: bool = False,
        *,
        action_id: str | None = None,
        shortcut_category: str = "General",
        shortcut_description: str | None = None,
        shortcut_owner: str | None = "LoomStroke Core",
    ) -> QAction:
        """Create a menu action bound to the shortcut manager when possible."""

        window = self._window
        source_text = window._translation_token(text)
        translated = translate_runtime_text(source_text, window.tr)
        action = QAction(translated, window)
        if action_id:
            action_identifier: str = action_id

            def _apply_label(
                translated: str,
                act: QAction = action,
                aid: str = action_identifier,
            ) -> None:
                window._set_action_label(act, aid, translated)

            window._translation_binder.register(_apply_label, source_text)
        else:
            window._translation_binder.register(action.setText, source_text)
        if slot is not None:
            window._connect_action_slot(action, slot)
        if tip:
            tip_text = window._translation_token(tip)
            action.setStatusTip(translate_runtime_text(tip_text, window.tr))
        if checkable:
            action.setCheckable(True)
            action.setChecked(checked)

        shortcut_manager = getattr(window, "_shortcut_manager", None)
        if action_id and shortcut_manager:
            description_text = (
                window._translation_token(shortcut_description)
                if isinstance(shortcut_description, str) and shortcut_description
                else shortcut_description
            )
            sequence = shortcut_manager.register_action(
                action_id,
                label=source_text,
                default=shortcut,
                category=shortcut_category,
                description=description_text,
                owner=shortcut_owner,
            )
            window._shortcut_actions[action_id] = action
            window._shortcut_defaults[action_id] = shortcut
            action.setProperty("shortcut_action_id", action_id)
            action.hovered.connect(lambda aid=action_id: window._set_last_hovered_shortcut(aid))
            window._apply_action_shortcut_metadata(action, action_id, sequence, shortcut)
        elif shortcut:
            action.setShortcut(window._sequence_from_text(shortcut))

        return action

    def set_action_label(self, action: QAction, action_id: str, text: str) -> None:
        """Store the translated base label and reapply dynamic badge state."""

        window = self._window
        window._shortcut_labels[action_id] = text
        action.setText(window._decorate_action_label(text, action_id))

    def decorate_action_label(self, text: str, action_id: str) -> str:
        if action_id in self._window._custom_shortcuts:
            return f"{text}  ⭑"
        return text

    def apply_action_shortcut_metadata(
        self,
        action: QAction,
        action_id: str,
        sequence: str | None,
        default: str | None,
    ) -> None:
        window = self._window
        action.setShortcut(window._sequence_from_text(sequence))

        is_custom = bool(sequence and default and sequence != default)
        if is_custom:
            window._custom_shortcuts.add(action_id)
        else:
            window._custom_shortcuts.discard(action_id)

        label = window._shortcut_labels.get(action_id, action.text())
        action.setText(window._decorate_action_label(label, action_id))
        window._update_action_tooltip(action, action_id, sequence, default)

    @staticmethod
    def sequence_from_text(sequence: str | None) -> QKeySequence:
        """Build a QKeySequence from portable text, returning empty when unset."""

        if not sequence:
            return QKeySequence()
        return QKeySequence.fromString(
            sequence,
            QKeySequence.SequenceFormat.PortableText,
        )

    def ensure_shortcut_binding(self, action_id: str, handler: Callable[[], None]) -> None:
        window = self._window
        binding = window._shortcut_bindings.get(action_id)
        if binding is None:
            binding = QShortcut(window)
            binding.setContext(Qt.ShortcutContext.ApplicationShortcut)
            binding.activated.connect(handler)
            window._shortcut_bindings[action_id] = binding
        sequence = (
            window._shortcut_manager.get_sequence(action_id) if window._shortcut_manager else None
        )
        binding.setKey(window._sequence_from_text(sequence))

    def remove_shortcut_binding(self, action_id: str) -> None:
        binding = self._window._shortcut_bindings.pop(action_id, None)
        if binding is None:
            return
        binding.setKey(QKeySequence())
        binding.deleteLater()

    def sync_tool_shortcut_bindings(self) -> None:
        window = self._window
        manager = getattr(window, "tool_manager", None)
        if manager is None or window._shortcut_manager is None:
            return
        desired: dict[str, Callable[[], None]] = {}
        for descriptor in manager.get_registered_tools():
            tool_id = descriptor.id
            legacy_key = f"shortcuts/tool.activate.{tool_id}"
            primary_key = f"shortcuts/tools.{tool_id}"
            if window._settings.has(legacy_key) and not window._settings.has(primary_key):
                legacy_value = window._settings.get(legacy_key)
                if isinstance(legacy_value, str) and legacy_value.strip():
                    window._settings.set(primary_key, legacy_value)

            def _activate(tid: str = tool_id) -> None:
                window._activate_tool_id(tid)

            primary_action_id = f"tools.{tool_id}"
            if primary_action_id not in window._shortcut_actions:
                desired[primary_action_id] = _activate
            secondary_action_id = f"tools.{tool_id}.secondary"
            if descriptor.secondary_shortcut:
                desired[secondary_action_id] = _activate
        for action_id in list(window._shortcut_bindings):
            if action_id not in desired:
                window._remove_shortcut_binding(action_id)
        for action_id, handler in desired.items():
            window._ensure_shortcut_binding(action_id, handler)

    def update_action_tooltip(
        self,
        action: QAction,
        action_id: str,
        sequence: str | None,
        default: str | None,
    ) -> None:
        window = self._window
        parts: list[str] = []
        status_tip = action.statusTip()
        if status_tip:
            parts.append(status_tip)
        active = sequence or default
        if active:
            shortcut_template = window._ts("Shortcut: {shortcut}")
            parts.append(shortcut_template.format(shortcut=active))
        else:
            parts.append(window._ts("Shortcut: (none)"))
        parts.append("Ctrl+Alt+, → Open Shortcut Preferences")
        parts.append(window._ts("Action ID: {action_id}").format(action_id=action_id))
        action.setToolTip("\n".join(parts))

    def register_shortcut_definition(
        self,
        action_id: str,
        *,
        label: str,
        default: str | None,
        category: str,
        description: str | None,
    ) -> None:
        manager = self._window._shortcut_manager
        if manager is None:
            return
        try:
            manager.register_action(
                action_id,
                label=label,
                default=default,
                category=category,
                description=description,
                owner="LoomStroke Core",
            )
        except Exception:
            logger.debug("Failed to register shortcut definition %s", action_id, exc_info=True)

    def on_shortcut_changed(self, action_id: str, sequence: str) -> None:
        window = self._window
        action = window._shortcut_actions.get(action_id)
        if action is None:
            binding = window._shortcut_bindings.get(action_id)
            if binding is not None:
                binding.setKey(window._sequence_from_text(sequence or None))
            return
        default = window._shortcut_defaults.get(action_id)
        value = sequence or None
        window._apply_action_shortcut_metadata(action, action_id, value, default)
        binding = window._shortcut_bindings.get(action_id)
        if binding is not None:
            binding.setKey(window._sequence_from_text(value))
