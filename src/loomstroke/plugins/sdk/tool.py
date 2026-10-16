"""
Tool plugin interface for LoomStroke.

Tool plugins implement drawing tools like pen, eraser, selection, etc.
Per Master.md, tools generate Commands instead of directly manipulating pixels.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:

    from ...core.commands import Command
    from ...host_api import HostAPI, StrokePoint


@dataclass(frozen=True)
class ToolDescriptor:
    """
    Descriptor for a tool plugin.

    Provides metadata about the tool for UI and registration.
    """

    id: str
    display_name: str
    cursor: str | None = None  # Cursor resource name
    category: str = "brush"  # brush, selection, transform, shape, etc.
    icon_svg: str | None = None  # SVG icon data
    shortcut: str | None = None  # Default keyboard shortcut (e.g., "B" for brush)
    shortcut_category: str = "Tools"
    shortcut_description: str | None = None
    shortcut_owner: str | None = None  # Owner label surfaced in shortcut UI
    secondary_shortcut: str | None = None  # Optional alternate binding surfaced as long-press
    secondary_shortcut_description: str | None = None
    order: int = 0  # Display order in toolbar


class PropertyType(Enum):
    """Supported property types for ToolMetadata property specs."""

    FLOAT = auto()
    INT = auto()
    BOOL = auto()
    ENUM = auto()
    COLOR = auto()
    STRING = auto()


@dataclass(slots=True)
class PropertySpec:
    """Schema describing a tool property exposed in the UI."""

    key: str
    display_name: str
    type: PropertyType
    min_value: float | None = None
    max_value: float | None = None
    default_value: Any | None = None
    step: float | None = None
    enum_items: list[str] | None = None
    is_common: bool = False


@dataclass(slots=True)
class ToolActionSpec:
    """Action entry exposed in tool property panels."""

    key: str
    label: str
    kind: str = "button"
    enabled_when: str | None = None


@dataclass(slots=True)
class ToolActionGroup:
    """Group of tool actions rendered together in the UI."""

    id: str
    label: str
    actions: list[ToolActionSpec] = field(default_factory=list)


@dataclass(slots=True)
class ToolPreset:
    """Named preset for a tool's property values."""

    name: str
    values: dict[str, Any]


@dataclass(slots=True)
class ToolMetadata:
    """Tool metadata used by the host UI for grouping and property forms."""

    tool_id: str
    display_name: str
    category: str
    tags: list[str] = field(default_factory=list)
    properties: list[PropertySpec] = field(default_factory=list)
    presets: list[ToolPreset] = field(default_factory=list)
    icon_svg: str | None = None
    group_id: str | None = None
    group_label: str | None = None
    group_order: int | None = None
    action_groups: list[ToolActionGroup] = field(default_factory=list)


class ToolSettings:
    """Base class for tool-specific settings."""

    pass


@runtime_checkable
class ToolPlugin(Protocol):
    """
    Protocol for tool plugins.

    Tools handle input events and generate Commands for the canvas.
    They do not directly manipulate pixel data.
    """

    def descriptor(self) -> ToolDescriptor:
        """
        Get the tool descriptor.

        Returns:
            Tool descriptor with metadata
        """
        ...

    def tool_metadata(self) -> ToolMetadata | None:
        """Return rich metadata (properties, presets, tags) for UI panels."""

        ...

    def activate(self, host: HostAPI) -> None:
        """
        Called when the plugin is activated.

        Store the host API reference for later use.
        This is called once during plugin initialization.

        Args:
            host: The host API interface
        """
        ...

    def enable(self) -> None:
        """
        Called when this tool becomes the active tool.

        Set up tool-specific UI, subscribe to events, etc.
        """
        ...

    def disable(self) -> None:
        """
        Called when switching away from this tool.

        Clean up tool state, unsubscribe from events, etc.
        """
        ...

    def deactivate(self) -> None:
        """
        Called when the plugin is being unloaded.

        Clean up all resources.
        """
        ...

    def on_pointer_down(
        self,
        pt: StrokePoint,
        *,
        alt: bool = False,
        shift: bool = False,
        ctrl: bool = False,
        meta: bool = False,
    ) -> None:
        """
        Handle pointer down event.

        Args:
            pt: Stroke point with position and pressure
            alt: Alt/Option key pressed
            shift: Shift key pressed
            ctrl: Control key pressed
            meta: Meta/Windows/Command key pressed
        """
        ...

    def on_pointer_move(self, pts: Iterable[StrokePoint]) -> None:
        """
        Handle pointer move events.

        Multiple points may be provided for coalesced input.

        Args:
            pts: Sequence of stroke points since last move
        """
        ...

    def on_pointer_up(self, pt: StrokePoint) -> None:
        """
        Handle pointer up event.

        Args:
            pt: Final stroke point
        """
        ...

    def on_pointer_hover(self, pt: StrokePoint) -> None:
        """
        Handle pointer hover (no buttons pressed).

        Args:
            pt: Hover position
        """
        ...

    def on_key(self, key: str, pressed: bool) -> bool:
        """
        Handle keyboard input.

        Args:
            key: Key name (e.g., "Space", "Escape", "1", "A")
            pressed: True for key down, False for key up

        Returns:
            True if the event was handled, False to pass through
        """
        ...

    def on_wheel(self, delta: float, pt: StrokePoint) -> bool:
        """
        Handle mouse wheel events.

        Args:
            delta: Wheel delta (positive = up/forward)
            pt: Pointer position

        Returns:
            True if handled, False to pass through
        """
        ...

    def build_commands(self) -> list[Command]:
        """
        Build commands from accumulated tool state.

        This is called to generate Commands from the tool's
        internal state (e.g., accumulated stroke points).

        Returns:
            List of commands to execute (may be empty)
        """
        ...

    def get_settings(self) -> ToolSettings:
        """
        Get current tool settings.

        Returns:
            Tool-specific settings object
        """
        ...

    def set_settings(self, settings: ToolSettings) -> None:
        """
        Update tool settings.

        Args:
            settings: New settings to apply
        """
        ...

    def create_options_widget(self) -> Any:
        """
        Create a widget for tool options.

        Returns:
            QWidget for tool options panel, or None
        """
        ...

    def context_toolbar_spec(self) -> ContextToolbarSpec | None:
        """Return context toolbar spec, or None to disable."""
        return None

    def get_context_anchor_rect(self) -> Any:
        """Return anchor rect (QRect) for anchor_mode='custom'."""
        return None


# ---------------------------------------------------------------------------
# Context toolbar declarations
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class ContextWidgetSpec:
    """Custom widget declaration for context toolbar."""

    key: str
    kind: str  # "button", "progress", "separator", "label"
    label: str = ""
    icon_svg: str | None = None
    on_clicked: Callable[[], None] | None = None
    enabled_when: str | None = None
    # Optional toolbar row index. None keeps the legacy layout (all custom
    # widgets share one row); widgets with the same index are grouped into
    # their own row, rendered in ascending index order.
    row: int | None = None


@dataclass(slots=True)
class ContextToolbarSpec:
    """Declarative spec for a context toolbar.

    Plugins return this from ``context_toolbar_spec()`` and the core
    ``ContextToolbarManager`` takes care of everything else.
    """

    # Basic
    enabled: bool = True

    # When to show
    show_trigger: str = "pointer_up"

    # What to show
    property_keys: list[str] | None = None
    custom_widgets: list[ContextWidgetSpec] | None = None

    # Where to show
    anchor_mode: str = "stroke_bbox"

    # Placement strategy: "anchor" (follow object), "top_center", "bottom_center"
    placement: str = "anchor"

    # Mode
    mode: str = "settings"

    # Extra commit triggers (EventBus event names)
    extra_commit_triggers: list[str] = field(default_factory=list)

    # Preview-mode callbacks
    on_param_preview: Callable[[str, Any], None] | None = None
    on_commit: Callable[[], None] | None = None
    on_cancel: Callable[[], None] | None = None

    # Custom widget builder — when set, the toolbar calls this instead of
    # building from property_keys.  Signature:
    #   build_widget_fn(parent, on_changed) -> QWidget
    # The returned widget must expose set_value(key, value) for external sync.
    build_widget_fn: Callable[..., Any] | None = None
