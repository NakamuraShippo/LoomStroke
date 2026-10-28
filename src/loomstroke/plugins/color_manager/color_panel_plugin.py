"""Color panel plugin integrating the shared color state UI."""

from __future__ import annotations

import asyncio
import logging
import uuid

from PySide6.QtWidgets import QWidget

from loomstroke.core.color_state import ColorDescriptor, ColorStateSnapshot
from loomstroke.core.events import Events
from loomstroke.plugins.sdk.base import PluginMetadata, PluginType
from loomstroke.plugins.sdk.ui import PanelDescriptor, UIPanelPlugin
from loomstroke.ui.translations import translated_literal_domain

from .color_context_controller import ColorContextController
from .color_models import DEFAULT_PALETTE_ID, ColorPalette, PaletteSwatch
from .palette_feedback import (
    PaletteTaskTarget,
    dismiss_palette_feedback,
    install_palette_feedback,
    run_palette_task,
)
from .palette_repository import ColorPaletteRepository
from .widgets import ColorPanel

logger = logging.getLogger(__name__)


def _ts(text: str) -> str:
    return translated_literal_domain("ColorPanelPlugin", text, use_fallback=True)


class ColorPanelPlugin(UIPanelPlugin):
    """UI panel that exposes the color wheel and palette manager."""

    metadata = PluginMetadata(
        id="color_panel",
        name=_ts("Color Manager"),
        version="1.0.0",
        description=_ts("Color picker and palette management panel"),
        author="LoomStroke Team",
        types=[PluginType.UI_PANEL],
    )

    def __init__(self) -> None:
        super().__init__(self.metadata)
        self._host = None
        self._panel: ColorPanel | None = None
        self._repository: ColorPaletteRepository | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._color_subscription: object | None = None
        self._color_context: ColorContextController | None = None
        self._palette_tasks: set[asyncio.Task] = set()

    def descriptor(self) -> PanelDescriptor:
        return PanelDescriptor(
            id="color_manager_panel",
            display_name=_ts("Color"),
            category="tools",
            icon_svg=self._icon_svg(),
            default_position="right",
            default_size=(320, 520),
            resizable=True,
        )

    def activate(self, host_api) -> None:  # type: ignore[override]  # plugin API signature divergence
        super().activate(host_api)
        self.host_api = host_api
        self._enabled = True
        self._host = host_api
        self._repository = ColorPaletteRepository(host_api.settings)
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            self._loop = None
        self._subscribe_color()

    def deactivate(self) -> None:
        dismiss_palette_feedback(self._panel)
        for task in tuple(self._palette_tasks):
            task.cancel()
        if self._color_context is not None:
            self._color_context.dispose()
            self._color_context = None
        self._unsubscribe_color()
        self._panel = None
        self._repository = None
        self._host = None
        self.host_api = None
        self._enabled = False
        super().deactivate()

    def create_widget(self, parent: QWidget | None = None) -> QWidget:
        if self._panel is None:
            self._panel = ColorPanel(parent)
            install_palette_feedback(self._panel)
            self._panel.colorCommitted.connect(self._on_color_committed)
            self._panel.colorSelected.connect(self._on_color_preview)
            self._panel.paletteRequested.connect(self._on_palette_request)

            self._panel.paletteSwatchRemoved.connect(self._on_swatch_removed)

            if self._host and hasattr(self._host, "get_color_snapshot"):
                try:
                    snapshot: ColorStateSnapshot = self._host.get_color_snapshot()  # type: ignore[attr-defined]  # host_api duck-typed
                    self._panel.set_current_color(snapshot)
                except Exception as exc:
                    logger.debug("Unable to initialize color snapshot: %s", exc)
            self._schedule_task(self._load_palettes())
            if self._host:
                self._color_context = ColorContextController(self._panel, self._host)
        return self._panel

    # --- Event handlers -----------------------------------------------------

    def _on_color_preview(self, descriptor: ColorDescriptor) -> None:
        self._update_host_color(descriptor, source="color_panel:preview")

    def _on_color_committed(self, descriptor: ColorDescriptor) -> None:
        self._update_host_color(descriptor, source="color_panel")

    def _on_palette_request(self, action: str, descriptor: ColorDescriptor) -> None:
        if action == "add":
            self._schedule_task(self._add_swatch(descriptor))

    def _on_swatch_removed(self, swatch_id: str) -> None:
        self._schedule_task(self._remove_swatch(swatch_id))

    def _handle_color_snapshot(self, snapshot: ColorStateSnapshot) -> None:
        if self._panel:
            self._panel.set_current_color(snapshot)

    def _update_host_color(self, descriptor: ColorDescriptor, *, source: str) -> None:
        if not self._host:
            return
        try:
            current = getattr(self._host, "get_active_color", lambda: None)()
        except Exception:
            current = None
        if current == descriptor:
            return
        try:
            self._host.set_active_color(descriptor, source=source)
        except Exception as exc:
            logger.error("Failed to set active color: %s", exc)

    # --- Repository interactions -------------------------------------------

    async def _load_palettes(self) -> None:
        target = PaletteTaskTarget.capture(self)
        repository = target.repository
        if repository is None:
            return
        await repository.load()
        if not target.is_current():
            return
        palettes = await repository.list_palettes()
        if not target.is_current():
            return
        if not palettes:
            palettes = [self._default_palette()]
        target.panel.set_palettes(palettes)

    async def _add_swatch(self, descriptor: ColorDescriptor) -> None:
        target = PaletteTaskTarget.capture(self)
        repository = target.repository
        if repository is None:
            return
        await repository.load()
        if not target.is_current():
            return
        palettes = await repository.list_palettes()
        if not target.is_current():
            return
        palette = palettes[0] if palettes else self._default_palette()
        swatch = PaletteSwatch(
            id=f"swatch_{uuid.uuid4().hex[:8]}",
            label=self._hex_from_descriptor(descriptor),
            color=descriptor,
        )
        palette.add_swatch(swatch)
        await repository.upsert_palette(palette)
        if not target.is_current():
            return
        updated = await repository.list_palettes()
        if target.is_current():
            target.panel.set_palettes(updated)

    async def _remove_swatch(self, swatch_id: str) -> None:
        target = PaletteTaskTarget.capture(self)
        repository = target.repository
        if repository is None:
            return
        await repository.load()
        if not target.is_current():
            return
        palettes = await repository.list_palettes()
        if not target.is_current():
            return
        if not palettes:
            return
        palette = palettes[0]
        try:
            palette.remove_swatch(swatch_id)
        except KeyError:
            return
        await repository.upsert_palette(palette)
        if not target.is_current():
            return
        updated = await repository.list_palettes()
        if target.is_current():
            target.panel.set_palettes(updated)

    # --- Color subscriptions -------------------------------------------------

    def _subscribe_color(self) -> None:
        if not self._host:
            return
        subscriber = getattr(self._host, "subscribe_color_changed", None)
        if callable(subscriber):
            try:
                self._color_subscription = subscriber(self._handle_color_snapshot)
            except Exception as exc:
                logger.debug("Failed to subscribe to color changes: %s", exc)

    def _unsubscribe_color(self) -> None:
        if not self._host or self._color_subscription is None:
            return
        try:
            self._host.events.off(Events.COLOR_CHANGED, self._color_subscription)
        except Exception as exc:
            logger.debug("Failed to unsubscribe color events: %s", exc)
        finally:
            self._color_subscription = None

    # --- Helpers ------------------------------------------------------------

    def _schedule_task(self, coro):
        target = PaletteTaskTarget.capture(self)
        install_palette_feedback(target.panel)
        try:
            loop = self._loop or asyncio.get_event_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            task = loop.create_task(run_palette_task(target, coro))
            self._palette_tasks.add(task)

            def finished(completed):
                self._palette_tasks.discard(completed)
                coro.close()

            task.add_done_callback(finished)
            return task
        else:
            return asyncio.run(run_palette_task(target, coro))

    def _default_palette(self) -> ColorPalette:
        palette = ColorPalette(id=DEFAULT_PALETTE_ID, name="Default")
        for label, rgba in [
            ("#000000", (0.0, 0.0, 0.0, 1.0)),
            ("#FFFFFF", (1.0, 1.0, 1.0, 1.0)),
            ("#FF0000", (1.0, 0.0, 0.0, 1.0)),
            ("#00FF00", (0.0, 1.0, 0.0, 1.0)),
            ("#0000FF", (0.0, 0.0, 1.0, 1.0)),
        ]:
            palette.add_swatch(
                PaletteSwatch(
                    id=f"default_{label[1:]}".lower(),
                    label=label,
                    color=ColorDescriptor(rgba=rgba),
                )
            )
        return palette

    def _hex_from_descriptor(self, descriptor: ColorDescriptor) -> str:
        r, g, b, _ = descriptor.rgba
        return f"#{int(r * 255):02X}{int(g * 255):02X}{int(b * 255):02X}"

    def _icon_svg(self) -> str:
        return """
        <svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg">
            <circle cx="12" cy="12" r="10" fill="#4F46E5"/>
            <path d="M6 12a6 6 0 1112 0 6 6 0 11-12 0z" fill="#fff" opacity="0.2"/>
        </svg>
        """


__all__ = ["ColorPanelPlugin"]


def create_plugin() -> ColorPanelPlugin:
    """Factory for plugin registry."""
    return ColorPanelPlugin()


__all__ = ["ColorPanelPlugin", "create_plugin"]
