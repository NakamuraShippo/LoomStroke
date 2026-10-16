"""Protocol and data contracts for the LoomStroke HostAPI.

The public compatibility surface remains ``loomstroke.host_api``.  This module
only owns the contract definitions so the ``HostAPI`` facade can stay focused on
runtime delegation.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from .core.selection_clipboard import SelectionPayload

if TYPE_CHECKING:
    from numpy.typing import NDArray
    from PySide6.QtCore import QTranslator

    from .core.commands import Command

# Coordinate type for clarity
Point = tuple[float, float]

# Progress callback used by long-running operations (save/load).
ProgressCallback = Callable[[float, str | None], None]


@dataclass(frozen=True)
class PanelLayout:
    """Dock panel layout information used for UI persistence."""

    panel_id: str
    geometry: tuple[int, int, int, int]
    dock_area: str | None = None
    floating: bool = False
    visible: bool = True
    z_index: int = 0
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class UILayoutSnapshot:
    """Snapshot of the main window layout."""

    window_state: str | None = None
    window_geometry: str | None = None
    panels: Sequence[PanelLayout] = ()


@dataclass(frozen=True)
class SaveOptions:
    """Options that control document save behaviour."""

    include_ui_layout: bool = True
    include_history: bool = False
    embed_icc: bool = True
    compression: str = "auto"
    autosave: bool = False
    metadata: Mapping[str, Any] | None = None


@dataclass(frozen=True)
class LoadOptions:
    """Options for document loading."""

    apply_ui_layout: bool = True
    strict: bool = True
    validate_manifest: bool = True


@dataclass(frozen=True)
class StrokePoint:
    """A single point in a stroke with tablet input data."""

    pos: Point
    pressure: float = 1.0
    tilt: tuple[float, float] | None = None
    time: float | None = None  # milliseconds since stroke start


@dataclass(frozen=True)
class Selection:
    """Represents a selection in the canvas."""

    mask_layer_id: str | None = None
    bounds: tuple[int, int, int, int] | None = None  # x, y, width, height


@dataclass(frozen=True)
class LayerRef:
    """Reference to a layer."""

    id: str
    name: str | None = None
    layer_type: str | None = None
    vector_kind: str | None = None


@dataclass(frozen=True)
class DocumentRef:
    """Reference to a document."""

    id: str
    name: str | None = None


PageId = str


@dataclass(frozen=True)
class CanvasSpec:
    """Canvas configuration values."""

    width: int
    height: int
    dpi: tuple[float, float]
    background_color: tuple[int, int, int, int]


@dataclass(frozen=True)
class PageSummary:
    """Summary information about a page for list views."""

    id: PageId
    index: int
    name: str
    canvas: CanvasSpec
    has_canvas_override: bool
    dirty: bool
    thumbnail_available: bool


@dataclass(frozen=True)
class PageTemplateSummary:
    """Summary information about an add-page template."""

    id: str
    name: str
    category: str
    source: str
    description: str | None = None


@dataclass(frozen=True)
class ShortcutActionInfo:
    """Description of a registered shortcut for introspection."""

    action_id: str
    label: str
    category: str = "General"
    description: str | None = None
    default: str | None = None
    current: str | None = None
    owner: str | None = None


@dataclass(frozen=True)
class ShortcutSchemeValidation:
    """Result of validating a shortcut import/export payload."""

    valid: bool
    actions: Mapping[str, str | None] | None = None
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


@runtime_checkable
class ShortcutService(Protocol):
    """Service that lets plugins register and inspect keyboard shortcuts."""

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
        """Register or update a shortcut-enabled action and return its current sequence."""

    def set_shortcut(self, action_id: str, sequence: str | None) -> None:
        """Persist a shortcut override for an action."""

    def reset_shortcut(self, action_id: str) -> None:
        """Clear the user override and restore the default shortcut."""

    def list_actions(self) -> Sequence[ShortcutActionInfo]:
        """Return the current list of registered shortcut actions."""

    def validate_scheme(
        self,
        payload: Any,
        *,
        require_known: bool = False,
    ) -> ShortcutSchemeValidation:
        """Validate a shortcut mapping payload before importing it."""


TranslationDomainLoader = Callable[[str], "QTranslator | None"]


# ---- Service Protocols ----


@runtime_checkable
class CanvasService(Protocol):
    """Service for canvas operations."""

    def current_document(self) -> DocumentRef:
        """Get the current document reference."""
        ...

    def current_layer(self) -> LayerRef:
        """Get the current active layer reference."""
        ...

    def add_3d_layer(
        self,
        name: str | None = None,
        *,
        scene: Any = None,
        camera: Any = None,
        lighting: Any = None,
        display_mode: str = "solid",
        gen_3d: Any = None,
        origin: Any = None,
    ) -> LayerRef:
        """Add an editable 3D layer."""
        ...

    def add_3d_layer_from_file(
        self,
        path: str | Path,
        *,
        name: str | None = None,
        display_mode: str = "solid",
        gen_3d: Any = None,
    ) -> LayerRef:
        """Import a glTF/GLB/OBJ scene as an editable 3D layer."""
        ...

    def get_layers(self) -> list[LayerRef]:
        """Get list of all layers in the document."""
        ...

    def get_selection(self) -> Selection | None:
        """Get the current selection if any."""
        ...

    def get_selection_snapshot(self) -> SelectionPayload | None:
        """Return a snapshot of the current selection (image + mask)."""
        ...

    def submit(self, cmd: Command, *, merge_key: str | None = None) -> bool:
        """
        Submit a command for execution.

        Args:
            cmd: The command to execute
            merge_key: Optional key for merging with previous command

        Returns:
            True if command was executed successfully
        """
        ...

    def begin_transaction(self, label: str) -> None:
        """
        Begin a transaction for grouping commands.

        Args:
            label: Human-readable label for the transaction
        """
        ...

    def end_transaction(self) -> None:
        """End the current transaction."""
        ...

    def is_busy(self) -> bool:
        """Check if canvas is currently processing commands."""
        ...

    def cache_layer_snapshot(
        self,
        layer_id: str | None = None,
        fmt: str = "png",
        name: str | None = None,
    ) -> str:
        """Cache a snapshot of a single layer image.

        Args:
            layer_id: Identifier of the layer to capture; None uses the active layer.
            fmt: Requested image format supported by Qt writers.
            name: Optional filename stem applied to the cached file.

        Returns:
            Absolute path to the cached image file.

        Raises:
            ValueError: If the layer cannot be resolved.
            OSError: If the snapshot cannot be written.
        """
        ...

    def apply_image(
        self,
        image: NDArray[Any],
        *,
        position: tuple[int, int] = (0, 0),
        blend_mode: str = "normal",
        opacity: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Apply image data onto the canvas."""
        ...

    def undo_image(
        self,
        *,
        position: tuple[int, int] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """Revert the most recent image application if possible."""
        ...

    def get_layer_content_bounds(
        self, layer_id: str | None = None
    ) -> tuple[int, int, int, int] | None:
        """Return the bounding box of non-transparent content in canvas coordinates.

        Args:
            layer_id: Target layer.  ``None`` uses the active layer.

        Returns:
            ``(x, y, width, height)`` in canvas coordinates, or ``None`` when
            the layer is fully transparent / empty.
        """
        ...

    def get_canvas_size(self) -> tuple[int, int]:
        """Return the current canvas size as ``(width, height)``."""
        ...

    def rasterize_layer(self, layer_id: str) -> bool:
        """Convert a vector layer into a raster layer if supported."""
        ...

    def bake_3d_layer_snapshot(self, layer_id: str, *, name: str | None = None) -> str | None:
        """Add the current 3D layer view as a new raster layer."""
        ...

    def render_3d_layer_maps(
        self,
        layer_id: str,
        *,
        width: int | None = None,
        height: int | None = None,
    ) -> Any | None:
        """Render depth/normal/outline maps for a 3D layer."""
        ...

    def bake_3d_controlnet_guides(
        self,
        layer_id: str,
        *,
        kinds: Sequence[str] = ("depth", "normal", "outline"),
        name_prefix: str | None = None,
    ) -> dict[str, str]:
        """Add 3D guide maps as raster layers for ComfyUI ControlNet input."""
        ...


@runtime_checkable
class HistoryAwareCanvas(CanvasService, Protocol):
    """Canvas implementations that track undo/redo-able operations.

    This covers native strokes as well as plugin-submitted commands because
    everything funnels through ``canvas.submit()``. Host features such as the
    global history service or UI menu actions can rely on these hooks to
    expose consistent undo/redo state regardless of the underlying canvas
    backend.
    """

    def history_can_undo(self) -> bool:
        """Return ``True`` when at least one undo step is available."""
        ...

    def history_can_redo(self) -> bool:
        """Return ``True`` when a redo step exists."""
        ...

    def history_undo(self) -> bool:
        """Undo the most recent operation in the history stack."""
        ...

    def history_redo(self) -> bool:
        """Redo the last undone operation if possible."""
        ...

    def history_clear(self) -> None:
        """Clear undo/redo stacks (e.g., after document replacement)."""
        ...

    def history_undo_count(self) -> int:
        """Return the number of undo-able operations."""
        ...

    def history_redo_count(self) -> int:
        """Return the number of redo-able operations."""
        ...

    def update_canvas_config(
        self,
        *,
        width: int | None = None,
        height: int | None = None,
        dpi: int | None = None,
        tile_size: int | None = None,
        max_layers: int | None = None,
        anchor: tuple[float, float] | None = None,
    ) -> dict[str, Any]:
        """Update canvas configuration values.

        Args:
            width: Optional new canvas width in pixels.
            height: Optional new canvas height in pixels.
            dpi: Optional new document DPI.
            tile_size: Optional new tile size hint used for rendering caches.
            max_layers: Optional maximum allowed layers.
            anchor: Optional anchor point (x, y) in normalized 0-1 coordinates.

        Returns:
            Dictionary with the applied configuration values.
        """
        ...

    def set_layer_locked(self, layer_id: str, locked: bool) -> None:
        """Update layer lock state."""
        ...

    def set_layer_alpha_locked(self, layer_id: str, alpha_locked: bool) -> None:
        """Update layer alpha lock (transparency lock) state."""
        ...

    def set_active_layer(self, layer_id: str) -> None:
        """Set the active layer by identifier."""
        ...


@runtime_checkable
class HistoryService(Protocol):
    """Service for undo/redo operations."""

    def can_undo(self) -> bool:
        """Check if undo is available."""
        ...

    def can_redo(self) -> bool:
        """Check if redo is available."""
        ...

    def undo(self) -> bool:
        """Perform undo operation."""
        ...

    def redo(self) -> bool:
        """Perform redo operation."""
        ...

    def get_undo_stack_size(self) -> int:
        """Get the number of undoable operations."""
        ...

    def get_redo_stack_size(self) -> int:
        """Get the number of redoable operations."""
        ...


@runtime_checkable
class InputService(Protocol):
    """Service for input handling."""

    def pointer_capture(self, tool_id: str) -> None:
        """
        Capture pointer input for a tool.

        Args:
            tool_id: ID of the tool requesting capture
        """
        ...

    def release_capture(self, tool_id: str) -> None:
        """
        Release pointer capture.

        Args:
            tool_id: ID of the tool releasing capture
        """
        ...

    def coalesced_points(self) -> Sequence[StrokePoint]:
        """
        Get coalesced input points since last call.

        Returns:
            Sequence of stroke points with tablet data
        """
        ...

    def predicted_points(self, count: int = 1) -> Sequence[StrokePoint]:
        """
        Get predicted future points for smoothing.

        Args:
            count: Number of points to predict

        Returns:
            Sequence of predicted stroke points
        """
        ...


@runtime_checkable
class SettingsService(Protocol):
    """Service for settings/preferences access."""

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a setting value.

        Args:
            key: Setting key
            default: Default value if key not found

        Returns:
            Setting value or default
        """
        ...

    def set(self, key: str, value: Any) -> None:
        """
        Set a setting value.

        Args:
            key: Setting key
            value: Value to set
        """
        ...

    def get_section(self, section: str) -> dict[str, Any]:
        """
        Get all settings in a section.

        Args:
            section: Section name

        Returns:
            Dictionary of settings in the section
        """
        ...

    def subscribe(self, key: str, callback: Any) -> Any:
        """
        Subscribe to setting changes.

        Args:
            key: Setting key to watch
            callback: Function to call on change

        Returns:
            Subscription token for unsubscribing
        """
        ...

    def unsubscribe(self, token: Any) -> None:
        """
        Unsubscribe from setting changes.

        Args:
            token: Subscription token from subscribe()
        """
        ...


@runtime_checkable
class DocumentService(Protocol):
    """Service responsible for saving and loading documents."""

    def save(
        self,
        path: str | Path,
        options: SaveOptions | None = None,
        *,
        progress: ProgressCallback | None = None,
    ) -> bool:
        """Save the current document to the specified path."""
        ...

    async def save_async(
        self,
        path: str | Path,
        options: SaveOptions | None = None,
        *,
        progress: ProgressCallback | None = None,
    ) -> bool:
        """Asynchronously save the current document to the specified path.

        Snapshot capture and completion notification run on the Qt main
        thread; only the ZIP encoding stage is offloaded to a worker thread
        via ``asyncio.to_thread``. Existing :meth:`save` remains fully
        synchronous and contract-compatible.

        Pre-release audit §12-C #6 / §12-O (Phase A, 2026-04-11).
        """
        ...

    def load(
        self,
        path: str | Path,
        options: LoadOptions | None = None,
        *,
        progress: ProgressCallback | None = None,
    ) -> bool:
        """Load a document from the specified path."""
        ...

    def export_layer(
        self,
        layer_id: str,
        path: str | Path,
        *,
        format: str = "PNG",
        metadata: Mapping[str, Any] | None = None,
    ) -> bool:
        """Export a single layer to an external file."""
        ...

    def import_layer(
        self,
        path: str | Path,
        *,
        insert_above: bool = True,
        as_new: bool = True,
        progress: ProgressCallback | None = None,
    ) -> str | None:
        """Import an external image as a new layer and return its ID."""
        ...


@runtime_checkable
class PageService(Protocol):
    """Service responsible for page management."""

    def list_pages(self) -> Sequence[PageSummary]:
        """Return a summary of all pages in display order."""
        ...

    def get_active_page_id(self) -> PageId:
        """Return the current active page id."""
        ...

    def set_active_page(self, page_id: PageId) -> None:
        """Switch the active page."""
        ...

    def add_page(self, *, after_page_id: PageId | None = None) -> PageId:
        """Create a new page and return its id."""
        ...

    def list_page_templates(self) -> Sequence[PageTemplateSummary]:
        """Return available page templates."""
        ...

    def add_page_from_template(
        self,
        template_id: str,
        *,
        after_page_id: PageId | None = None,
    ) -> PageId:
        """Create a new page from a template and return its id."""
        ...

    def save_page_as_template(self, page_id: PageId, name: str) -> str | None:
        """Save the given page as a user template and return the template id."""
        ...

    def duplicate_page(self, page_id: PageId) -> PageId:
        """Duplicate the given page and return the new id."""
        ...

    def delete_page(self, page_id: PageId) -> None:
        """Delete the given page."""
        ...

    def rename_page(self, page_id: PageId, name: str) -> None:
        """Rename the given page."""
        ...

    def move_page(self, page_id: PageId, new_index: int) -> None:
        """Move the page to a new index."""
        ...

    def request_page_thumbnail(self, page_id: PageId) -> None:
        """Request thumbnail generation for a page."""
        ...

    def get_page_view_state(self, page_id: PageId) -> dict[str, Any] | None:
        """Return the stored view_state for a page, if any."""
        ...

    def set_page_view_state(self, page_id: PageId, view_state: dict[str, Any] | None) -> None:
        """Update the stored view_state for a page."""
        ...

    def get_active_canvas_spec(self) -> CanvasSpec:
        """Return the effective canvas spec for the active page."""
        ...


@runtime_checkable
class EventBus(Protocol):
    """Event bus for plugin communication."""

    def on(self, event: str, handler: Any) -> Any:
        """
        Subscribe to an event.

        Args:
            event: Event name
            handler: Event handler function

        Returns:
            Subscription token
        """
        ...

    def off(self, event: str, token: Any) -> None:
        """
        Unsubscribe from an event.

        Args:
            event: Event name
            token: Subscription token from on()
        """
        ...

    def emit(self, event: str, payload: Any) -> None:
        """
        Emit an event.

        Args:
            event: Event name
            payload: Event payload data
        """
        ...

    async def emit_async(self, event: str, payload: Any) -> None:
        """
        Emit an event and await any async handlers.

        Args:
            event: Event name
            payload: Event payload data
        """
        ...

    def subscribe(self, event: str, handler: Any) -> Any:
        """
        Subscribe to an event (alias for 'on').

        Args:
            event: Event name
            handler: Event handler function

        Returns:
            Subscription token
        """
        ...

    def publish(self, event: str, payload: Any) -> None:
        """
        Publish an event (alias for 'emit').

        Args:
            event: Event name
            payload: Event payload data
        """
        ...

    def once(self, event: str, handler: Any) -> Any:
        """
        Subscribe to an event for one-time execution only.

        Args:
            event: Event name
            handler: Event handler function to invoke once

        Returns:
            Subscription token
        """
        ...


@runtime_checkable
@runtime_checkable
class UILayoutService(Protocol):
    """Service responsible for capturing and restoring UI layouts."""

    def snapshot(self, *, include_panels: bool = True) -> UILayoutSnapshot:
        """Capture the current UI layout."""
        ...

    def apply(
        self,
        snapshot: UILayoutSnapshot,
        *,
        allow_restore_state: bool = True,
        clear_missing: bool = False,
        preserve_toolbars: bool = False,
    ) -> None:
        """Apply a previously captured UI layout."""
        ...

    def iter_panels(self) -> Iterable[PanelLayout]:
        """Iterate over the known panels in the UI."""
        ...


@runtime_checkable
class UIService(Protocol):
    """Service for UI operations."""

    def get_main_window(self) -> Any:
        """Get the main window widget (QMainWindow)."""
        ...

    def add_dock_widget(self, widget: Any, area: str = "left") -> None:
        """
        Add a dock widget to the main window.

        Args:
            widget: QDockWidget to add
            area: Dock area (left, right, top, bottom)
        """
        ...

    def register_panel_widget(
        self,
        panel_id: str,
        title: str,
        widget: Any,
        *,
        area: str = "right",
        default_visible: bool = True,
        owner_plugin_id: str | None = None,
        closable: bool = True,
        resizable: bool = True,
        floating: bool = False,
        default_size: tuple[int, int] | None = None,
    ) -> bool:
        """Register a dock panel widget."""
        ...

    def unregister_panel(self, panel_id: str) -> bool:
        """Unregister a dock panel by ID."""
        ...

    def has_panel(self, panel_id: str) -> bool:
        """Return True when a panel is already registered."""
        ...

    def set_panel_visible(self, panel_id: str, visible: bool) -> bool:
        """Set panel visibility by ID."""
        ...

    def add_toolbar(self, toolbar: Any) -> None:
        """
        Add a toolbar to the main window.

        Args:
            toolbar: QToolBar to add
        """
        ...

    def show_message(self, message: str, duration: int = 3000) -> None:
        """
        Show a status message.

        Args:
            message: Message text
            duration: Display duration in milliseconds
        """
        ...

    def show_progress(self, message: str, value: int, max_value: int = 100) -> None:
        """
        Show progress in status bar.

        Args:
            message: Progress message
            value: Current progress value
            max_value: Maximum progress value
        """
        ...

    def get_layout_service(self) -> UILayoutService | None:
        """Return the UILayoutService if the UI exposes one."""
        ...

    def layout_snapshot(self, *, include_panels: bool = True) -> UILayoutSnapshot | None:
        """Capture a UI layout snapshot if supported."""
        ...


__all__ = (
    "CanvasService",
    "CanvasSpec",
    "DocumentRef",
    "DocumentService",
    "EventBus",
    "HistoryAwareCanvas",
    "HistoryService",
    "InputService",
    "LayerRef",
    "LoadOptions",
    "PageId",
    "PageService",
    "PageSummary",
    "PageTemplateSummary",
    "PanelLayout",
    "Point",
    "ProgressCallback",
    "SaveOptions",
    "Selection",
    "SelectionPayload",
    "SettingsService",
    "ShortcutActionInfo",
    "ShortcutSchemeValidation",
    "ShortcutService",
    "StrokePoint",
    "TranslationDomainLoader",
    "UILayoutService",
    "UILayoutSnapshot",
    "UIService",
)
