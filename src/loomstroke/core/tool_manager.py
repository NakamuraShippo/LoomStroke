"""Tool manager for active tool management and event dispatching."""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping
from dataclasses import asdict, is_dataclass
from typing import Any, cast

from PySide6.QtCore import QObject, Signal

from loomstroke.core.brush_size_key_stepper import ACCELERATION_SETTING_KEY, BrushSizeKeyStepper
from loomstroke.core.commands import Command, CutSelectionCommand
from loomstroke.core.event_bus import EventBus
from loomstroke.core.events import Events
from loomstroke.core.task_registry import ToolTaskRegistry
from loomstroke.core.tool_manager_size_keys import (
    SIZE_KEY_DIRECTIONS,
    step_active_tool_size,
    step_active_tool_size_direction,
)
from loomstroke.core.tool_registry import ToolRegistry, ToolState
from loomstroke.core.tool_session_manager import ToolSession, ToolSessionManager
from loomstroke.host_api import HostAPI, StrokePoint
from loomstroke.plugins.sdk.tool import (
    ToolDescriptor,
    ToolMetadata,
    ToolPlugin,
    ToolPreset,
)

logger = logging.getLogger(__name__)


class ToolManager(QObject):
    """Manages tool plugins and dispatches input events.

    Responsibilities:
    - Register and manage tool plugins
    - Handle tool activation/deactivation
    - Dispatch input events to active tool
    - Collect and submit commands
    """

    # Signals
    tool_activated = Signal(str)  # tool_id
    tool_deactivated = Signal(str)  # tool_id
    tool_registered = Signal(dict)  # tool descriptor dict
    commands_generated = Signal(list)  # List[Command]
    tool_setting_changed = Signal(str, str, object)  # tool_id, key, value
    tool_undo_state_changed = Signal(bool)  # undo available for active tool
    tool_action_completed = Signal(str, str, bool)  # tool_id, action_key, handled

    def __init__(self, host_api: HostAPI, event_bus: EventBus | None = None) -> None:
        """Initialize tool manager.

        Args:
            host_api: Host API instance for tools
            event_bus: Optional event bus for integration
        """
        super().__init__()

        self.host_api = host_api
        self.event_bus = event_bus

        self._registry = ToolRegistry()
        self._sessions = ToolSessionManager()
        self._initializing_tools: dict[str, ToolPlugin] = {}

        # Registered shortcut action IDs
        self._registered_shortcuts: set[str] = set()
        self._event_handlers: list[Any] = []
        self._event_tokens: list[Any] = []

        # Input state
        self._modifier_state = {"shift": False, "ctrl": False, "alt": False}
        self._tool_can_undo = False
        self._size_key_stepper = BrushSizeKeyStepper()

        # Connect to event bus if provided
        if self.event_bus:
            self._connect_event_bus()

        logger.info("ToolManager initialized")

    @property
    def _tools(self) -> dict[str, ToolState]:
        return self._registry.tools

    @property
    def _metadata(self) -> dict[str, ToolMetadata]:
        return self._registry.metadata

    @property
    def _active_tool_id(self) -> str | None:
        return self._registry.active_tool_id

    @property
    def _active_tool(self) -> ToolPlugin | None:
        return self._registry.active_tool

    @property
    def _session(self) -> ToolSession | None:
        return self._sessions.session

    @property
    def _pending_commands(self) -> list[Command]:
        return self._sessions.pending_commands

    def set_size_key_acceleration(self, level: object) -> None:
        """Set the ``[`` / ``]`` size key acceleration level (input setting)."""
        self._size_key_stepper.set_level(level)

    def apply_input_setting(self, key: str, value: object) -> None:
        """Slot for settings changes that affect tool input handling."""
        if key == ACCELERATION_SETTING_KEY:
            self.set_size_key_acceleration(value)

    def step_tool_size(self, tool_id: str, direction: int) -> bool:
        """Step ``tool_id``'s size like a ``[`` / ``]`` press if it is the active tool."""
        if tool_id != self._active_tool_id or self._stroke_active():
            return False
        return step_active_tool_size_direction(self, direction)

    def update_modifier_state(self, modifiers: Mapping[str, bool]) -> None:
        """Update modifier state (connected to canvas input handler)."""

        for key in ("shift", "ctrl", "alt"):
            if key in modifiers:
                self._modifier_state[key] = bool(modifiers[key])

    def _stroke_active(self) -> bool:
        return self._sessions.stroke_active()

    def _bind_task_registry(self, tool: ToolPlugin, registry: ToolTaskRegistry | None) -> None:
        setter = getattr(tool, "set_task_registry", None)
        if callable(setter):
            try:
                setter(registry)
            except Exception:
                logger.debug("Tool task registry setter failed", exc_info=True)
            return
        if hasattr(tool, "task_registry"):
            try:
                tool.task_registry = registry
            except Exception:
                logger.debug("Tool task registry assignment failed", exc_info=True)

    def _release_pointer_capture(self, tool_id: str) -> None:
        input_service = getattr(self.host_api, "input", None)
        releaser = getattr(input_service, "release_capture", None) if input_service else None
        if callable(releaser):
            try:
                releaser(tool_id)
            except Exception:
                logger.debug("Pointer capture release failed for %s", tool_id, exc_info=True)

    def _cancel_session_tasks(self, session: ToolSession, *, reason: str) -> None:
        self._sessions.cancel_session_tasks(session, reason=reason)

    def _close_active_session(self, *, reason: str) -> None:
        self._sessions.close_active_session(
            reason=reason,
            force_end_stroke=lambda session: self._force_end_stroke(session, reason=reason),
            release_pointer_capture=self._release_pointer_capture,
            bind_task_registry=self._bind_task_registry,
        )

    def _force_end_stroke(self, session: ToolSession, *, reason: str) -> None:
        self._sessions.force_end_stroke(
            session,
            reason=reason,
            collect_commands=self._collect_commands,
            flush_commands=self._flush_commands,
            update_tool_undo_state=self._update_tool_undo_state,
            release_pointer_capture=self._release_pointer_capture,
        )

    def _settings_to_dict(self, settings: Any) -> dict[str, Any]:
        """Convert tool settings objects into plain dictionaries."""
        if settings is None:
            return {}
        if isinstance(settings, dict):
            return dict(settings)
        if is_dataclass(settings):
            try:
                return asdict(cast(Any, settings))
            except Exception:
                return dict(vars(settings))
        if hasattr(settings, "__dict__"):
            return dict(vars(settings))
        return {}

    def _connect_event_bus(self) -> None:
        """Connect to event bus for tool events."""
        if not self.event_bus:
            return
        # Subscribe to tool-related events
        activate_handler = self._on_tool_activate_event
        settings_handler = self._on_tool_settings_event
        language_handler = self._on_language_changed
        self._event_handlers.extend([activate_handler, settings_handler, language_handler])
        self._event_tokens.append(self.event_bus.subscribe("tool.activate", activate_handler))
        self._event_tokens.append(
            self.event_bus.subscribe("tool.settings_changed", settings_handler)
        )
        self._event_tokens.append(
            self.event_bus.subscribe(Events.LANGUAGE_CHANGED, language_handler)
        )

    def _on_language_changed(self, _payload: object) -> None:
        """Refresh all registered tool descriptors/metadata for runtime translation."""
        if not self._tools:
            return

        for tool_id, state in self._tools.items():
            plugin = state.plugin
            descriptor_provider = getattr(plugin, "descriptor", None)
            if not callable(descriptor_provider):
                continue

            try:
                descriptor = descriptor_provider()
            except Exception:
                logger.debug("Failed to refresh descriptor for tool %s on language change", tool_id)
                logger.debug("Tool metadata refresh failed", exc_info=True)
                continue

            if not isinstance(descriptor, ToolDescriptor) or descriptor.id != tool_id:
                logger.debug(
                    "Ignoring descriptor refresh for tool %s because tool_id changed",
                    tool_id,
                )
                continue

            state.descriptor = descriptor

            metadata_provider = getattr(plugin, "tool_metadata", None)
            if callable(metadata_provider):
                try:
                    metadata = metadata_provider()
                except Exception:
                    logger.debug(
                        "Failed to refresh metadata for tool %s on language change", tool_id
                    )
                    logger.debug("Tool metadata refresh failed", exc_info=True)
                    metadata = None
                else:
                    if isinstance(metadata, ToolMetadata):
                        self.register_tool_metadata(metadata)
            elif isinstance(metadata_provider, ToolMetadata):
                self.register_tool_metadata(metadata_provider)
            else:
                self._ensure_metadata(descriptor)

            self._register_tool_shortcut(descriptor)

            self.tool_registered.emit(
                {
                    "id": descriptor.id,
                    "name": descriptor.display_name,
                    "category": descriptor.category,
                    "icon": descriptor.icon_svg,
                }
            )

    # Metadata -----------------------------------------------------------------

    def register_tool_metadata(self, metadata: ToolMetadata) -> None:
        """Register metadata for a tool (plugins may call before activation)."""
        self._registry.register_tool_metadata(metadata)

    def iter_tool_metadata(self) -> Iterable[ToolMetadata]:
        """Iterate over known tool metadata entries."""
        return self._registry.iter_tool_metadata()

    def list_tool_categories(self) -> list[str]:
        """Return sorted list of tool categories for UI grouping."""
        return self._registry.list_tool_categories()

    def get_tool_metadata(self, tool_id: str) -> ToolMetadata | None:
        """Get metadata for a tool if registered."""
        return self._registry.get_tool_metadata(tool_id)

    def get_tool_presets(self, tool_id: str) -> list[ToolPreset]:
        """Return presets for a tool (copy to avoid accidental mutation)."""
        return self._registry.get_tool_presets(tool_id)

    def apply_preset(self, tool_id: str, preset_name: str) -> None:
        """Apply a preset by name for the given tool."""
        values = self._registry.get_preset_values(tool_id, preset_name)
        if values is None:
            return
        for key, value in values.items():
            self.set_tool_setting(tool_id, key, value)

    def save_preset(self, tool_id: str, preset: ToolPreset, *, overwrite: bool = True) -> None:
        """Save or update a preset definition."""
        self._registry.save_preset(tool_id, preset, overwrite=overwrite)

    def delete_preset(self, tool_id: str, preset_name: str) -> None:
        """Delete a preset if it exists."""
        self._registry.delete_preset(tool_id, preset_name)

    def _apply_metadata_defaults(self, metadata: ToolMetadata, state: ToolState | None) -> None:
        self._registry.apply_metadata_defaults(metadata, state)

    def _ensure_metadata(self, descriptor: ToolDescriptor) -> ToolMetadata:
        return self._registry.ensure_metadata(descriptor)

    # Tool Registration

    def register_tool(self, tool: ToolPlugin) -> None:
        """Register a tool plugin.

        Args:
            tool: Tool plugin to register
        """
        # Get descriptor
        descriptor = tool.descriptor()
        tool_id = descriptor.id

        # activate() may register this same instance through HostAPI.
        # Only suppress that reentry; ordinary replacement remains supported.
        if self._initializing_tools.get(tool_id) is tool:
            return

        # Check for duplicate
        if self._registry.has_tool(tool_id):
            logger.warning(f"Tool {tool_id} already registered, replacing")
            self.unregister_tool(tool_id)

        # Create tool state
        state = ToolState(plugin=tool, descriptor=descriptor, is_active=False, is_enabled=False)

        if hasattr(tool, "get_settings"):
            try:
                initial_settings = tool.get_settings()
            except Exception:
                initial_settings = None
            state.settings = self._settings_to_dict(initial_settings)

        # Store tool
        self._registry.register_state(tool_id, state)

        # Register or synthesize metadata before activation to seed defaults
        metadata: ToolMetadata | None = None
        provider = getattr(tool, "tool_metadata", None)
        if callable(provider):
            try:
                metadata = provider()
            except Exception:
                logger.debug("Tool %s metadata provider failed", tool_id, exc_info=True)
        elif isinstance(provider, ToolMetadata):
            metadata = provider

        if metadata:
            self.register_tool_metadata(metadata)
        else:
            self._ensure_metadata(descriptor)

        # Initialize tool with host API
        self._initializing_tools[tool_id] = tool
        try:
            tool.activate(self.host_api)
        finally:
            self._initializing_tools.pop(tool_id, None)
        self._register_tool_shortcut(descriptor)

        # Emit signal
        self.tool_registered.emit(
            {
                "id": descriptor.id,
                "name": descriptor.display_name,
                "category": descriptor.category,
                "icon": descriptor.icon_svg,
            }
        )

        # Publish to event bus
        if self.event_bus:
            self.event_bus.publish(
                "tool.registered", {"tool_id": tool_id, "descriptor": descriptor}
            )

        logger.info(f"Registered tool: {descriptor.display_name} ({tool_id})")

    def has_tool(self, tool_id: str) -> bool:
        """Return True if a tool is already registered."""
        return self._registry.has_tool(tool_id)

    def unregister_tool(self, tool_id: str) -> None:
        """Unregister a tool.

        Args:
            tool_id: ID of tool to unregister
        """
        state = self._registry.get_state(tool_id)
        if state is None:
            logger.warning(f"Tool {tool_id} not registered")
            return

        # Deactivate if active
        if state.is_active:
            self.deactivate_tool()

        # Disable if enabled
        if state.is_enabled:
            state.plugin.disable()

        # Deactivate plugin (suppress recursive unregisters via host API)
        host_unregistering: set[str] | None = None
        if hasattr(self.host_api, "_tool_unregistering"):
            candidate = getattr(self.host_api, "_tool_unregistering", None)
            if isinstance(candidate, set):
                host_unregistering = candidate
                host_unregistering.add(tool_id)
        try:
            state.plugin.deactivate()
        finally:
            if host_unregistering is not None:
                host_unregistering.discard(tool_id)

        # Remove from registry
        self._registry.remove_state(tool_id)
        if state.descriptor.shortcut:
            primary_id = self._tool_shortcut_action_id(tool_id)
            self._registered_shortcuts.discard(primary_id)
        if state.descriptor.secondary_shortcut:
            secondary_id = self._tool_secondary_shortcut_action_id(tool_id)
            self._registered_shortcuts.discard(secondary_id)

        logger.info(f"Unregistered tool: {tool_id}")

    # Tool Activation

    def activate_tool(self, tool_id: str) -> None:
        """Activate a tool, deactivating current tool if any.

        Args:
            tool_id: ID of tool to activate
        """
        if not self._registry.has_tool(tool_id):
            logger.error(f"Tool {tool_id} not registered")
            return

        from .text_area_selection_handoff import capture, restore  # noqa: PLC0415

        canvas = getattr(self.host_api, "canvas", None)
        area_selection = capture(canvas, tool_id)
        # Deactivate current tool
        if self._active_tool_id:
            self.deactivate_tool()
        restore(canvas, area_selection)

        self._size_key_stepper.reset()

        # Get tool state
        state = self._registry.activate_tool(tool_id)

        # Set as active
        self._sessions.open_session(
            tool_id,
            state.plugin,
            bind_task_registry=self._bind_task_registry,
        )

        # Enable tool
        state.plugin.enable()
        state.is_enabled = True
        state.is_active = True

        # Emit signal
        self.tool_activated.emit(tool_id)
        self._update_tool_undo_state(force=True)

        # Publish to event bus
        if self.event_bus:
            self.event_bus.publish(
                "tool.activated", {"tool_id": tool_id, "descriptor": state.descriptor}
            )

        logger.info(f"Activated tool: {state.descriptor.display_name}")

    def deactivate_tool(self) -> None:
        """Deactivate current active tool."""
        active_tool_id = self._active_tool_id
        if not active_tool_id:
            return

        state = self._tools[active_tool_id]

        # Defer recompose across the entire deactivation sequence so that
        # _force_end_stroke → flush → disable → end_transaction produces at
        # most a single recompose at the end.
        canvas = getattr(self.host_api, "canvas", None)
        has_defer = canvas is not None and hasattr(canvas, "defer_recompose")
        if has_defer:
            cast(Any, canvas).defer_recompose()
        try:
            # Ensure any active stroke is closed before disabling the tool
            self._close_active_session(reason="deactivate")

            # Flush any pending commands
            self._flush_commands()

            # Disable tool
            state.plugin.disable()
        finally:
            if has_defer:
                cast(Any, canvas).resume_recompose()

        state.is_enabled = False
        state.is_active = False

        # Emit signal
        self.tool_deactivated.emit(active_tool_id)

        # Publish to event bus
        if self.event_bus:
            self.event_bus.publish("tool.deactivated", {"tool_id": active_tool_id})

        logger.info(f"Deactivated tool: {state.descriptor.display_name}")

        # Clear active tool
        self._registry.clear_active_tool()
        self._update_tool_undo_state(force=True)

    # Shortcut registration -------------------------------------------------

    def _register_tool_shortcut(self, descriptor: ToolDescriptor) -> None:
        if not (descriptor.shortcut or descriptor.secondary_shortcut):
            return
        register_shortcut = getattr(self.host_api, "register_shortcut_action", None)
        if not callable(register_shortcut):
            return

        owner = descriptor.shortcut_owner
        if not owner and "." in descriptor.id:
            owner = descriptor.id.split(".", 1)[0]
        owner = owner or "tools"
        for slot, sequence, description, action_id in self._iter_tool_shortcut_payloads(descriptor):
            if not sequence:
                continue
            label = descriptor.display_name
            if slot == "secondary":
                label = f"{label} ({slot.capitalize()})"
            slot_description = description
            if slot == "secondary" and not slot_description:
                slot_description = "Alternate binding"
            try:
                register_shortcut(
                    action_id,
                    label=label,
                    default=sequence,
                    category=descriptor.shortcut_category or "Tools",
                    description=slot_description,
                    owner=owner,
                )
                self._registered_shortcuts.add(action_id)
            except Exception:  # pragma: no cover - defensive guard
                logger.debug(
                    "Failed to register %s shortcut for tool %s",
                    slot,
                    descriptor.id,
                    exc_info=True,
                )
            else:
                self._registered_shortcuts.add(action_id)

    @staticmethod
    def _tool_shortcut_action_id(tool_id: str) -> str:
        return f"tools.{tool_id}"

    @staticmethod
    def _tool_secondary_shortcut_action_id(tool_id: str) -> str:
        return f"tools.{tool_id}.secondary"

    def _iter_tool_shortcut_payloads(
        self, descriptor: ToolDescriptor
    ) -> list[tuple[str, str | None, str | None, str]]:
        primary = (
            "primary",
            descriptor.shortcut,
            descriptor.shortcut_description,
            self._tool_shortcut_action_id(descriptor.id),
        )
        payloads: list[tuple[str, str | None, str | None, str]] = [primary]
        if descriptor.secondary_shortcut:
            payloads.append(
                (
                    "secondary",
                    descriptor.secondary_shortcut,
                    descriptor.secondary_shortcut_description,
                    self._tool_secondary_shortcut_action_id(descriptor.id),
                )
            )
        return payloads

    def _evaluate_tool_undo_state(self) -> bool:
        tool = self._active_tool
        if tool is None:
            return False
        checker = getattr(tool, "can_undo", None)
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except Exception:
            logger.debug("Tool can_undo failed", exc_info=True)
            return False

    def _update_tool_undo_state(self, *, force: bool = False) -> None:
        available = self._evaluate_tool_undo_state()
        if not force and available == self._tool_can_undo:
            return
        self._tool_can_undo = available
        self.tool_undo_state_changed.emit(available)

    # Input Dispatching

    def dispatch_pointer_down(self, point: StrokePoint) -> None:
        """Dispatch pointer down event to active tool.

        Args:
            point: Stroke point with position and pressure
        """
        active_tool = self._active_tool
        if active_tool is None or not self._sessions.begin_stroke(point):
            return

        # Dispatch to tool with modifiers
        active_tool.on_pointer_down(
            point,
            alt=self._modifier_state["alt"],
            shift=self._modifier_state["shift"],
            ctrl=self._modifier_state["ctrl"],
        )

        # Collect and flush commands immediately so pointer_down paint
        # reaches the canvas without waiting for the first pointer_move.
        self._collect_commands()
        self._flush_commands()
        self._update_tool_undo_state()

    def dispatch_pointer_move(self, points: list[StrokePoint]) -> None:
        """Dispatch pointer move events to active tool.

        Args:
            points: List of stroke points (coalesced)
        """
        active_tool = self._active_tool
        if active_tool is None or not self._sessions.update_last_point(points):
            return

        # Dispatch to tool
        active_tool.on_pointer_move(points)

        # Collect and flush commands immediately for real-time drawing.
        # Commands use merge_key to merge with previous stroke segments.
        self._collect_commands()
        self._flush_commands()

    def dispatch_pointer_up(self, point: StrokePoint) -> None:
        """Dispatch pointer up event to active tool.

        Args:
            point: Final stroke point
        """
        active_tool = self._active_tool
        if active_tool is None or not self._sessions.end_stroke(point):
            return

        # Dispatch to tool
        active_tool.on_pointer_up(point)

        # Collect and flush final commands
        self._collect_commands()
        self._flush_commands()
        self._update_tool_undo_state()

    def dispatch_pointer_hover(self, point: StrokePoint) -> None:
        """Dispatch pointer hover event to active tool."""
        if not self._active_tool:
            return
        handler = getattr(self._active_tool, "on_pointer_hover", None)
        if handler is not None:
            handler(point)

    def dispatch_key_event(self, key: str, pressed: bool) -> None:
        """Dispatch keyboard event to active tool.

        Args:
            key: Key identifier
            pressed: True if pressed, False if released
        """
        if not self._active_tool:
            return

        # Update modifier state
        if key in ["shift", "ctrl", "alt"]:
            self._modifier_state[key] = pressed

        # Dispatch to tool
        handled = self._active_tool.on_key(key, pressed)
        if pressed and not handled and key in SIZE_KEY_DIRECTIONS and not self._stroke_active():
            step_active_tool_size(self, key)

        # Collect commands that may be queued from key handling.
        self._collect_commands()
        if not self._stroke_active():
            self._flush_commands()
        self._update_tool_undo_state()

    def dispatch_undo(self) -> bool:
        """Dispatch an undo request to the active tool if supported."""

        if not self._active_tool:
            self._update_tool_undo_state()
            return False
        handler = getattr(self._active_tool, "on_undo", None)
        if not callable(handler) or not self._evaluate_tool_undo_state():
            self._update_tool_undo_state()
            return False
        try:
            handled = bool(handler())
        except Exception:
            logger.debug("Tool undo failed", exc_info=True)
            handled = False
        self._update_tool_undo_state()
        return handled

    # Command Management

    def dispatch_tool_action(
        self, tool_id: str, action_key: str, payload: dict[str, Any] | None = None
    ) -> bool:
        """Dispatch a tool-specific action from the UI.

        Returns True when an action was handled or one or more commands were submitted.
        """

        state = self._tools.get(tool_id)
        if not state:
            return False
        handled = False
        try:
            handler = getattr(state.plugin, "on_action", None)
            if not callable(handler):
                handled = self._dispatch_context_action(state.plugin, action_key, payload)
                return handled
            try:
                commands = handler(action_key, payload)
            except Exception:
                logger.exception("Tool action failed: %s", action_key)
                return False
            if not commands:
                return False
            self.commands_generated.emit(commands)
            canvas = getattr(self.host_api, "canvas", None)
            if canvas is None:
                return False
            any_submitted = False
            for cmd in commands:
                try:
                    success = bool(canvas.submit(cmd))
                except Exception as exc:
                    logger.warning("Failed to submit tool action command: %s", exc)
                    success = False
                if success:
                    any_submitted = True
                    self._handle_action_result(cmd)
            handled = any_submitted
            return handled
        finally:
            self._emit_tool_action_completed(tool_id, action_key, handled)

    def _emit_tool_action_completed(self, tool_id: str, action_key: str, handled: bool) -> None:
        try:
            self.tool_action_completed.emit(tool_id, action_key, handled)
        except Exception:
            logger.debug(
                "Tool action completion signal failed for %s.%s",
                tool_id,
                action_key,
                exc_info=True,
            )

    async def dispatch_tool_action_async(
        self, tool_id: str, action_key: str, payload: dict[str, Any] | None = None
    ) -> bool:
        """Async variant for tool-specific context actions."""

        state = self._tools.get(tool_id)
        if not state:
            return False
        if callable(getattr(state.plugin, "on_action", None)):
            return self.dispatch_tool_action(tool_id, action_key, payload)
        handled = False
        try:
            handled = await self._dispatch_context_action_async(state.plugin, action_key, payload)
            return handled
        finally:
            self._emit_tool_action_completed(tool_id, action_key, handled)

    def schedule_tool_action_async(
        self, tool_id: str, action_key: str, payload: dict[str, Any] | None = None
    ) -> bool:
        """Schedule an async context action on the active tool session."""

        state = self._tools.get(tool_id)
        if not state:
            return False
        async_handler = getattr(state.plugin, "execute_bound_context_action", None)
        if not callable(async_handler) or callable(getattr(state.plugin, "on_action", None)):
            return self.dispatch_tool_action(tool_id, action_key, payload)
        session = self._session
        if session is None or session.tool_id != tool_id:
            return False
        try:
            session.task_registry.create_task(
                self.dispatch_tool_action_async(tool_id, action_key, payload)
            )
        except Exception:
            logger.exception("Tool async context action scheduling failed: %s", action_key)
            return False
        return True

    def _dispatch_context_action(
        self,
        plugin: ToolPlugin,
        action_key: str,
        payload: dict[str, Any] | None,
    ) -> bool:
        handler = getattr(plugin, "execute_context_action", None)
        if not callable(handler):
            return False
        kwargs = self._context_action_kwargs(payload)
        try:
            result = handler(action_key, **kwargs)
        except Exception:
            logger.exception("Tool context action failed: %s", action_key)
            return False
        return self._context_action_result_is_handled(result)

    async def _dispatch_context_action_async(
        self,
        plugin: ToolPlugin,
        action_key: str,
        payload: dict[str, Any] | None,
    ) -> bool:
        handler = getattr(plugin, "execute_bound_context_action", None)
        if not callable(handler):
            return self._dispatch_context_action(plugin, action_key, payload)
        kwargs = self._bound_context_action_kwargs(payload)
        try:
            result = await handler(action_key, **kwargs)
        except Exception:
            logger.exception("Tool async context action failed: %s", action_key)
            return False
        return self._context_action_result_is_handled(result)

    @staticmethod
    def _context_action_result_is_handled(result: object) -> bool:
        if not isinstance(result, Mapping):
            return False
        if not bool(result.get("handled")):
            return False
        return result.get("status") != "blocked"

    def _context_action_kwargs(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"canvas": getattr(self.host_api, "canvas", None)}
        if not isinstance(payload, dict):
            return kwargs
        if "source" in payload:
            kwargs["source"] = payload.get("source")
        if "merge_key" in payload:
            kwargs["merge_key"] = payload.get("merge_key")
        if "asset_store" in payload:
            kwargs["asset_store"] = payload.get("asset_store")
        return kwargs

    def _bound_context_action_kwargs(self, payload: dict[str, Any] | None) -> dict[str, Any]:
        kwargs = self._context_action_kwargs(payload)
        kwargs["host_api"] = self.host_api
        if not isinstance(payload, dict):
            return kwargs
        if "request_id" in payload:
            kwargs["request_id"] = payload.get("request_id")
        if "metadata" in payload:
            kwargs["metadata"] = payload.get("metadata")
        if "binding" in payload:
            kwargs["binding"] = payload.get("binding")
        return kwargs

    def _handle_action_result(self, cmd: Command) -> None:
        if isinstance(cmd, CutSelectionCommand):
            payload = getattr(cmd, "payload", None)
            clipboard = getattr(self.host_api, "selection_clipboard", None)
            if payload is not None and clipboard is not None:
                try:
                    clipboard.set(payload)
                except Exception:
                    logger.debug("Failed to update selection clipboard", exc_info=True)

    def _collect_commands(self) -> None:
        """Collect commands from active tool."""
        if not self._active_tool:
            return

        # Get commands from tool
        commands = self._active_tool.build_commands()

        if commands:
            self._sessions.extend_pending_commands(commands)

            # Emit signal for immediate commands
            if not self._stroke_active():
                self.commands_generated.emit(commands)

    def _flush_commands(self) -> None:
        """Flush all pending commands."""
        if not self._sessions.has_pending_commands():
            return
        pending_commands = self._sessions.pending_commands

        # Emit all pending commands
        self.commands_generated.emit(pending_commands)

        # Submit to canvas if we have host API canvas service
        if hasattr(self.host_api, "canvas"):
            canvas = self.host_api.canvas
            # Defer recompose so it fires only once after the batch,
            # not once per command.
            has_defer = hasattr(canvas, "defer_recompose")
            if has_defer:
                cast(Any, canvas).defer_recompose()
            try:
                for cmd in pending_commands:
                    # Use merge key for stroke continuity
                    merge_key = None
                    if self._stroke_active() and self._session is not None:
                        merge_key = f"{self._session.tool_id}_stroke"
                    canvas.submit(cmd, merge_key=merge_key)
            finally:
                if has_defer:
                    cast(Any, canvas).resume_recompose()

        # Clear pending
        self._sessions.clear_pending_commands()

    # Tool Queries

    def get_active_tool(self) -> ToolDescriptor | None:
        """Get active tool descriptor."""
        if self._active_tool_id:
            return self._tools[self._active_tool_id].descriptor
        return None

    def get_registered_tools(self) -> list[ToolDescriptor]:
        """Get list of all registered tool descriptors."""
        return [state.descriptor for state in self._tools.values()]

    def get_tools_by_category(self, category: str) -> list[ToolDescriptor]:
        """Get tools filtered by category.

        Args:
            category: Tool category (e.g., 'brush', 'selection', 'transform')
        """
        return [
            state.descriptor
            for state in self._tools.values()
            if state.descriptor.category == category
        ]

    def is_tool_active(self, tool_id: str) -> bool:
        """Check if a tool is currently active.

        Args:
            tool_id: Tool ID to check
        """
        return self._active_tool_id == tool_id

    # Tool Settings

    def get_tool_settings(self, tool_id: str) -> dict[str, Any]:
        """Get a snapshot of tool settings."""
        state = self._tools.get(tool_id)
        if not state:
            return {}
        metadata = self._metadata.get(tool_id)
        if not metadata:
            metadata = self._ensure_metadata(state.descriptor)
        self._apply_metadata_defaults(metadata, state)
        if not state.settings and hasattr(state.plugin, "get_settings"):
            try:
                state.settings = self._settings_to_dict(state.plugin.get_settings())
            except Exception:
                state.settings = {}
        return dict(state.settings)

    def set_tool_setting(self, tool_id: str, key: str, value: Any) -> None:
        """Set a tool setting."""
        if tool_id not in self._tools:
            logger.warning(f"Tool {tool_id} not registered")
            return

        state = self._tools[tool_id]
        state.settings[key] = value

        plugin = state.plugin
        if hasattr(plugin, "set_setting"):
            try:
                plugin.set_setting(key, value)
            except Exception:
                logger.debug("Tool %s set_setting failed for %s", tool_id, key, exc_info=True)
        elif hasattr(plugin, "set_settings"):
            try:
                plugin.set_settings(cast(Any, dict(state.settings)))
            except Exception:
                logger.debug("Tool %s set_settings failed", tool_id, exc_info=True)
        elif hasattr(plugin, "get_settings") and hasattr(plugin, "set_settings"):
            try:
                settings_obj = plugin.get_settings()
                if hasattr(settings_obj, key):
                    setattr(settings_obj, key, value)
                plugin.set_settings(cast(Any, self._settings_to_dict(settings_obj)))
            except Exception:
                logger.debug("Tool %s could not sync composite settings", tool_id, exc_info=True)

        if self.event_bus:
            self.event_bus.publish(
                "tool.setting_changed", {"tool_id": tool_id, "key": key, "value": value}
            )

        try:
            self.tool_setting_changed.emit(tool_id, key, value)
        except Exception:
            logger.debug(
                "Tool setting change signal failed for %s.%s",
                tool_id,
                key,
                exc_info=True,
            )

    def sync_tool_setting(self, tool_id: str, key: str, value: Any) -> None:
        """
        Sync a tool setting from the host side without calling back into the tool plugin.

        This is intended for reflecting external state (e.g., selection-driven properties)
        into the UI without triggering a feedback loop that mutates the document.
        """
        if tool_id not in self._tools:
            return
        state = self._tools[tool_id]
        state.settings[key] = value
        try:
            self.tool_setting_changed.emit(tool_id, key, value)
        except Exception:
            logger.debug(
                "Tool setting sync signal failed for %s.%s",
                tool_id,
                key,
                exc_info=True,
            )

    def get_tool_setting(self, tool_id: str, key: str, default: Any = None) -> Any:
        """Get a tool setting.

        Args:
            tool_id: Tool ID
            key: Setting key
            default: Default value if not found
        """
        if tool_id not in self._tools:
            return default

        return self._tools[tool_id].settings.get(key, default)

    # Event Handlers

    def _on_tool_activate_event(self, data: dict[str, Any]) -> None:
        """Handle tool activation from event bus."""
        tool_id = data.get("tool_id") or data.get("id")
        if tool_id:
            self.activate_tool(str(tool_id))

    def _on_tool_settings_event(self, data: dict[str, Any]) -> None:
        """Handle tool settings change from event bus."""
        tool_id = data.get("tool_id")
        key = data.get("key")
        value = data.get("value")

        if tool_id and key is not None:
            if data.get("sync_only"):
                self.sync_tool_setting(tool_id, key, value)
            else:
                self.set_tool_setting(tool_id, key, value)
