"""ComfyUI integration panel combining workflow, prompt, and node management."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from loomstroke.platform.async_manager_enhanced import EnhancedAsyncManager
from loomstroke.plugins.common.outbound_policy import ensure_network_services_consent
from loomstroke.ui.panels.base_panel import BasePanel
from loomstroke.ui.panels.node_panel import NodePanel
from loomstroke.ui.panels.prompt_panel import PromptPanel
from loomstroke.ui.panels.workflow_panel import WorkflowPanel

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from loomstroke.ui.typing import ConnectionSignalProvider, Signal1


class ComfyUIPanel(BasePanel):
    """Main ComfyUI integration panel.

    Combines workflow, prompt, and node management in a unified interface.
    Complies with Master.md 400-line limit.
    """

    # Signals
    generation_started = Signal(dict)  # Generation config
    generation_completed = Signal(dict)  # Result data
    workflow_changed = Signal(dict)  # Workflow data

    def __init__(
        self,
        async_manager: EnhancedAsyncManager | None = None,
        parent: Any | None = None,
    ) -> None:
        """Initialize ComfyUI panel.

        Args:
            async_manager: Async manager for ComfyUI communication
            parent: Parent widget
        """
        # Set async_manager before calling super() since _setup_ui() needs it
        self.async_manager = async_manager

        # Sub-panels (initialize before super() since _setup_ui needs them)
        self.workflow_panel: WorkflowPanel | None = None
        self.prompt_panel: PromptPanel | None = None
        self.node_panel: NodePanel | None = None

        # State
        self.current_workflow: dict[str, Any] | None = None
        self.is_generating = False

        # UI elements
        self.tab_widget: QTabWidget | None = None
        self.status_label: QLabel | None = None
        self.generate_btn: QPushButton | None = None
        self._active_workflow_label: str | None = None

        # Initialize BasePanel with required panel_id and title
        # This will call _setup_ui() and _connect_signals()
        super().__init__(panel_id="ui.comfyui", title="ComfyUI", parent=parent)

    def _setup_ui(self) -> None:
        """Setup UI components."""
        layout = QVBoxLayout(self)
        layout.setSpacing(8)

        # Header
        header_layout = QHBoxLayout()

        title_text = self._ts("ComfyUI Integration")
        title = QLabel(title_text)
        self._translation_binder.register(title.setText, title_text)
        title.setStyleSheet("font-weight: bold; font-size: 16px;")
        header_layout.addWidget(title)

        header_layout.addStretch()

        # Main generate button
        self._generate_text = self._ts("Generate")
        self._stop_text = self._ts("Stop")
        self._translation_binder.register(self._set_generate_text, self._generate_text)
        self._translation_binder.register(self._set_stop_text, self._stop_text)
        self.generate_btn = QPushButton(self._generate_text)
        self.generate_btn.setStyleSheet("""
            QPushButton {
                font-weight: bold;
                padding: 8px 16px;
                background-color: #4CAF50;
                color: white;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:disabled {
                background-color: #cccccc;
            }
        """)
        self.generate_btn.clicked.connect(self._on_generate)
        header_layout.addWidget(self.generate_btn)

        layout.addLayout(header_layout)

        # Tab widget for sub-panels
        self.tab_widget = QTabWidget()

        # Create sub-panels
        self._create_workflow_tab()
        self._create_prompt_tab()
        self._create_node_tab()

        layout.addWidget(self.tab_widget)

        # Status bar
        status_layout = QHBoxLayout()

        self.status_label = QLabel(self.tr("Ready"))
        self.status_label.setStyleSheet("color: gray; font-size: 11px;")
        status_layout.addWidget(self.status_label)

        status_layout.addStretch()

        # Quick actions
        self._create_quick_actions(status_layout)

        layout.addLayout(status_layout)

    def _create_workflow_tab(self) -> None:
        """Create workflow management tab."""
        self.workflow_panel = WorkflowPanel(
            cast("ConnectionSignalProvider | None", self.async_manager)
        )
        # Remove set_event_bus call - WorkflowPanel doesn't have this method

        assert self.tab_widget is not None
        workflow_title = self._ts("Workflow")
        self.tab_widget.addTab(self.workflow_panel, workflow_title)
        self._register_tab_title(self.workflow_panel, workflow_title)

        # Connect workflow signals
        self.workflow_panel.workflow_loaded.connect(self._on_workflow_loaded)
        self.workflow_panel.workflow_executed.connect(self._on_workflow_executed)

    def _create_prompt_tab(self) -> None:
        """Create prompt editor tab."""
        self.prompt_panel = PromptPanel()
        # Remove set_event_bus call - PromptPanel doesn't have this method

        assert self.tab_widget is not None
        prompt_title = self._ts("Prompt")
        self.tab_widget.addTab(self.prompt_panel, prompt_title)
        self._register_tab_title(self.prompt_panel, prompt_title)

        # Connect prompt signals
        self.prompt_panel.generate_requested.connect(self._on_prompt_generate)
        self.prompt_panel.prompt_changed.connect(self._on_prompt_changed)

    def _create_node_tab(self) -> None:
        """Create node parameter tab."""
        self.node_panel = NodePanel()
        # Remove set_event_bus call - NodePanel doesn't have this method

        assert self.tab_widget is not None
        nodes_title = self._ts("Nodes")
        self.tab_widget.addTab(self.node_panel, nodes_title)
        self._register_tab_title(self.node_panel, nodes_title)

        # Connect node signals
        self.node_panel.parameters_changed.connect(self._on_node_params_changed)

    def _create_quick_actions(self, layout: QHBoxLayout) -> None:
        """Create quick action buttons."""
        # Quick generate txt2img
        txt2img_text = self._ts("txt2img")
        txt2img_btn = QPushButton(txt2img_text)
        self._translation_binder.register(txt2img_btn.setText, txt2img_text)
        txt2img_btn.setMaximumHeight(25)
        txt2img_btn.clicked.connect(self._quick_txt2img)
        layout.addWidget(txt2img_btn)

        # Quick generate img2img
        img2img_text = self._ts("img2img")
        img2img_btn = QPushButton(img2img_text)
        self._translation_binder.register(img2img_btn.setText, img2img_text)
        img2img_btn.setMaximumHeight(25)
        img2img_btn.clicked.connect(self._quick_img2img)
        layout.addWidget(img2img_btn)

        # Interrupt
        interrupt_text = self._ts("Interrupt")
        interrupt_btn = QPushButton(interrupt_text)
        self._translation_binder.register(interrupt_btn.setText, interrupt_text)
        interrupt_btn.setMaximumHeight(25)
        interrupt_btn.setStyleSheet("QPushButton { color: red; }")
        interrupt_btn.clicked.connect(self._interrupt_generation)
        layout.addWidget(interrupt_btn)

    def _register_tab_title(self, widget: QWidget, title: str) -> None:
        if self.tab_widget is None:
            return
        index = self.tab_widget.indexOf(widget)
        if index < 0:
            return
        self._translation_binder.register(
            lambda text, idx=index: self.tab_widget.setTabText(idx, text),
            title,
        )

    def _set_generate_text(self, text: str) -> None:
        self._generate_text = text

    def _set_stop_text(self, text: str) -> None:
        self._stop_text = text

    def on_language_changed(self, _language: str) -> None:
        self._update_generate_button()

    def _connect_signals(self) -> None:
        """Connect internal signals."""
        if self.async_manager:
            # Connect async manager signals
            self.async_manager.connection_established.connect(self._on_connected)
            self.async_manager.connection_lost.connect(self._on_disconnected)

        # Event bus subscriptions
        if self.event_bus:
            self.event_bus.subscribe("generation.started", self._on_generation_started)
            self.event_bus.subscribe("generation.progress", self._on_generation_progress)
            self.event_bus.subscribe("generation.completed", self._on_generation_completed)

    # Async Operations

    async def async_initialize(self) -> None:
        """Async initialization."""
        await super().async_initialize()  # type: ignore[misc]

        # Initialize sub-panels
        if self.workflow_panel:
            await self.workflow_panel.async_initialize()
        if self.prompt_panel:
            await self.prompt_panel.async_initialize()
        if self.node_panel:
            await self.node_panel.async_initialize()

        logger.info("ComfyUIPanel initialized")

    async def async_cleanup(self) -> None:
        """Async cleanup."""
        # Cleanup sub-panels
        if self.workflow_panel:
            await self.workflow_panel.async_cleanup()
        if self.prompt_panel:
            await self.prompt_panel.async_cleanup()
        if self.node_panel:
            await self.node_panel.async_cleanup()

        await super().async_cleanup()  # type: ignore[misc]

    # Event Handlers

    def _on_workflow_loaded(self, workflow: dict[str, Any]) -> None:
        """Handle workflow loaded."""
        self.current_workflow = workflow
        self._active_workflow_label = self.current_workflow_label()

        # Update node panel
        if self.node_panel:
            self.node_panel.load_workflow(workflow)

        # Enable generate button
        if self.generate_btn is not None:
            self.generate_btn.setEnabled(True)

        # Emit signal
        self.workflow_changed.emit(workflow)

        self._set_status(self.tr("Workflow loaded"))

    def _on_workflow_executed(self, workflow_id: str) -> None:
        """Handle workflow execution."""
        self._set_status(
            self.tr("Executing workflow: {workflow_id}").format(workflow_id=workflow_id)
        )
        self.is_generating = True
        self._update_generate_button()

    def _on_prompt_generate(self, config: dict[str, Any]) -> None:
        """Handle prompt generation request."""
        # Build workflow from prompt
        workflow = self._build_workflow_from_prompt(config)

        # Execute workflow
        if workflow and self.workflow_panel:
            self.workflow_panel.current_workflow = workflow
            self.workflow_panel._on_execute_workflow()

    def _on_prompt_changed(self, positive: str, negative: str) -> None:
        """Handle prompt text change."""
        # Update current workflow if it has text encode nodes
        if self.current_workflow:
            self._update_workflow_prompts(positive, negative)

    def current_workflow_label(self) -> str | None:
        """Return a human-readable name for the active workflow if available."""

        if self.workflow_panel and getattr(self.workflow_panel, "current_workflow_path", None):
            path_obj = self.workflow_panel.current_workflow_path
            if path_obj is not None:
                return path_obj.stem
        if isinstance(self.current_workflow, dict):
            label = self.current_workflow.get("name")
            if isinstance(label, str) and label.strip():
                return label.strip()
        return self._active_workflow_label

    def _on_node_params_changed(self, params: dict[str, dict[str, Any]]) -> None:
        """Handle node parameter changes."""
        # Update workflow with new parameters
        if self.current_workflow:
            for node_id, node_params in params.items():
                if node_id in self.current_workflow.get("nodes", {}):
                    node = self.current_workflow["nodes"][node_id]
                    if "inputs" not in node:
                        node["inputs"] = {}
                    node["inputs"].update(node_params)

            self.workflow_changed.emit(self.current_workflow)

    async def _on_generate(self) -> None:
        """Handle main generate button."""
        if self.is_generating:
            await self._interrupt_generation()
            return

        # Get current configuration
        config = self._build_generation_config()

        # Start generation
        await self._start_generation(config)

    async def _start_generation(self, config: dict[str, Any]) -> None:
        """Start generation process."""
        if not ensure_network_services_consent(self._settings, service_name="ComfyUI"):
            self._set_status(
                self.tr("External service access is disabled. Enable it in Preferences."),
                error=True,
            )
            self.is_generating = False
            self._update_generate_button()
            return

        if not self.async_manager:
            self._set_status(self.tr("No connection to ComfyUI"), error=True)
            return

        try:
            self.is_generating = True
            self._update_generate_button()

            # Emit signal
            self.generation_started.emit(config)

            # Publish to event bus
            if self.event_bus:
                self.event_bus.publish("generation.started", config)

            # Send to ComfyUI
            if self.current_workflow:
                result = await self.async_manager.post(
                    "/prompt", json={"prompt": self.current_workflow}
                )

                if result and "prompt_id" in result:
                    self._set_status(
                        self.tr("Generating: {prompt_id}").format(prompt_id=result["prompt_id"])
                    )
                else:
                    self._set_status(self.tr("Generation failed"), error=True)
                    self.is_generating = False

        except Exception as e:
            logger.error(f"Generation error: {e}")
            self._set_status(
                self.tr("Error: {error}").format(error=e),
                error=True,
            )
            self.is_generating = False

        finally:
            self._update_generate_button()

    async def _interrupt_generation(self) -> None:
        """Interrupt current generation."""
        if not ensure_network_services_consent(self._settings, service_name="ComfyUI"):
            self.is_generating = False
            self._update_generate_button()
            return

        if not self.async_manager:
            return

        try:
            await self.async_manager.post("/interrupt")
            self._set_status(self.tr("Generation interrupted"))
            self.is_generating = False
            self._update_generate_button()

        except Exception as e:
            logger.error(f"Failed to interrupt: {e}")

    def _quick_txt2img(self) -> None:
        """Quick text to image generation."""
        # Switch to prompt tab
        if self.tab_widget is None:
            return
        self.tab_widget.setCurrentWidget(cast(QWidget, self.prompt_panel))

        # Use current prompt settings
        if self.prompt_panel:
            self.prompt_panel._on_generate()

    def _quick_img2img(self) -> None:
        """Quick image to image generation."""
        # This would load img2img workflow
        self._set_status(self.tr("img2img not yet implemented"))

    # Helper Methods

    def _build_generation_config(self) -> dict[str, Any]:
        """Build generation configuration from current state."""
        prompts: dict[str, str] = {
            "positive": "",
            "negative": "",
        }
        config: dict[str, Any] = {
            "workflow": self.current_workflow,
            "prompt": prompts,
            "settings": {},
        }

        prompt_panel = self.prompt_panel
        if prompt_panel:
            if prompt_panel.positive_text is not None:
                prompts["positive"] = prompt_panel.positive_text.toPlainText()
            if prompt_panel.negative_text is not None:
                prompts["negative"] = prompt_panel.negative_text.toPlainText()
            config["settings"] = dict(prompt_panel.generation_settings)

        return config

    def _build_workflow_from_prompt(self, config: dict[str, Any]) -> dict[str, Any] | None:
        """Build basic workflow from prompt configuration."""
        # This would create a simple txt2img workflow
        # For now, return None (would be implemented with workflow templates)
        return None

    def _update_workflow_prompts(self, positive: str, negative: str) -> None:
        """Update workflow with new prompt texts."""
        # Find CLIPTextEncode nodes and update
        # Implementation depends on workflow structure
        pass

    def _update_generate_button(self) -> None:
        """Update generate button state."""
        if self.generate_btn is None:
            return

        if self.is_generating:
            self.generate_btn.setText(self._stop_text)
            self.generate_btn.setStyleSheet("""
                QPushButton {
                    background-color: #f44336;
                    color: white;
                    font-weight: bold;
                    padding: 8px 16px;
                    border-radius: 4px;
                }
            """)
        else:
            self.generate_btn.setText(self._generate_text)
            self.generate_btn.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    font-weight: bold;
                    padding: 8px 16px;
                    border-radius: 4px;
                }
            """)

    def _set_status(self, message: str, error: bool = False) -> None:
        """Set status message."""
        if self.status_label:
            self.status_label.setText(message)
            if error:
                self.status_label.setStyleSheet("color: red; font-size: 11px;")
            else:
                self.status_label.setStyleSheet("color: gray; font-size: 11px;")

    # Event Bus Handlers

    def _on_connected(self) -> None:
        """Handle connection established."""
        if self.generate_btn is not None:
            self.generate_btn.setEnabled(True)
        self._set_status(self.tr("Connected to ComfyUI"))

    def _on_disconnected(self) -> None:
        """Handle connection lost."""
        if self.generate_btn is not None:
            self.generate_btn.setEnabled(False)
        self.is_generating = False
        self._update_generate_button()
        self._set_status(self.tr("Disconnected"), error=True)

    def _on_generation_started(self, data: dict[str, Any]) -> None:
        """Handle generation started event."""
        self.is_generating = True
        self._update_generate_button()

    def _on_generation_progress(self, data: dict[str, Any]) -> None:
        """Handle generation progress event."""
        progress = data.get("progress", 0)
        self._set_status(self.tr("Generating... {progress}%").format(progress=progress))

    def _on_generation_completed(self, data: dict[str, Any]) -> None:
        """Handle generation completed event."""
        self.is_generating = False
        self._update_generate_button()
        self._set_status(self.tr("Generation completed"))

        # Emit signal
        self.generation_completed.emit(data)


if TYPE_CHECKING:  # pragma: no cover - typing aid only
    ComfyUIPanel.generation_started = cast(  # type: ignore[assignment]
        Signal1[dict[str, Any]],
        ComfyUIPanel.generation_started,
    )
    ComfyUIPanel.generation_completed = cast(  # type: ignore[assignment]
        Signal1[dict[str, Any]],
        ComfyUIPanel.generation_completed,
    )
    ComfyUIPanel.workflow_changed = cast(  # type: ignore[assignment]
        Signal1[dict[str, Any]],
        ComfyUIPanel.workflow_changed,
    )
