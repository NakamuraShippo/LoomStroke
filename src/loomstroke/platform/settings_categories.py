"""Settings category definitions for LoomStroke."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ColorMode(str, Enum):
    """Color mode enumeration."""

    RGB = "rgb"
    SRGB = "srgb"
    LAB = "lab"
    HSV = "hsv"


class BlendMode(str, Enum):
    """Blend mode enumeration."""

    NORMAL = "normal"
    MULTIPLY = "multiply"
    SCREEN = "screen"
    OVERLAY = "overlay"
    SOFT_LIGHT = "soft_light"
    HARD_LIGHT = "hard_light"
    COLOR_DODGE = "color_dodge"
    COLOR_BURN = "color_burn"


class SamplerType(str, Enum):
    """Sampler type for AI generation."""

    EULER = "euler"
    EULER_A = "euler_a"
    DDIM = "ddim"
    DPM_2 = "dpm_2"
    DPM_2_A = "dpm_2_a"
    LMS = "lms"
    DPM_FAST = "dpm_fast"
    DPM_ADAPTIVE = "dpm_adaptive"


@dataclass
class ConnectionSettings:
    """Connection settings for ComfyUI server.

    Handles WebSocket and HTTP connection configuration.
    """

    # Server configuration
    server_url: str = "ws://127.0.0.1:8188"
    # 既定値は settings_schema 側に保持するため、ここでのハードコードは撤去
    http_url: str = ""
    client_id: str | None = None

    # Timeouts (seconds)
    connection_timeout: int = 10
    request_timeout: int = 30
    upload_timeout: int = 300

    # Retry configuration
    max_retries: int = 3
    retry_delay: float = 1.0
    retry_backoff: float = 2.0
    max_retry_delay: float = 30.0

    # Security
    verify_tls: bool = True
    tls_cert_path: str | None = None
    use_auth: bool = False
    auth_token: str | None = None  # Will be encrypted

    # Connection behavior
    auto_reconnect: bool = True
    reconnect_interval: float = 5.0
    heartbeat_interval: float = 30.0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        # Remove sensitive data
        if "auth_token" in data:
            data["auth_token"] = None  # Don't serialize directly
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConnectionSettings:
        """Create from dictionary."""
        return cls(**data)

    def validate(self) -> list[str]:
        """Validate settings and return list of errors."""
        errors = []

        # Validate URLs
        if not self.server_url.startswith(("ws://", "wss://")):
            errors.append("server_url must start with ws:// or wss://")
        if not self.http_url.startswith(("http://", "https://")):
            errors.append("http_url must start with http:// or https://")

        # Validate timeouts
        if self.connection_timeout <= 0:
            errors.append("connection_timeout must be positive")
        if self.request_timeout <= 0:
            errors.append("request_timeout must be positive")

        # Validate retry settings
        if self.max_retries < 0:
            errors.append("max_retries cannot be negative")
        if self.retry_delay <= 0:
            errors.append("retry_delay must be positive")

        return errors


@dataclass
class DrawingSettings:
    """Drawing and canvas settings."""

    # Canvas dimensions
    canvas_width: int = 1920
    canvas_height: int = 1080
    canvas_dpi: int = 96

    # Canvas appearance
    background_color: str = "#FFFFFF"
    checker_pattern: bool = False
    checker_size: int = 16
    checker_color1: str = "#FFFFFF"
    checker_color2: str = "#E0E0E0"

    # Grid settings
    grid_visible: bool = False
    grid_size: int = 32
    grid_color: str = "#CCCCCC"
    grid_opacity: float = 0.5
    grid_snap: bool = False

    # Rendering
    antialiasing: bool = True
    use_opengl: bool = False
    pixel_perfect: bool = False
    color_mode: ColorMode = ColorMode.SRGB

    # Performance
    tile_size: int = 512
    cache_size_mb: int = 512
    undo_levels: int = 100

    # Viewport
    default_zoom: float = 1.0
    min_zoom: float = 0.01
    max_zoom: float = 32.0
    zoom_step: float = 0.1
    smooth_zoom: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        data = asdict(self)
        # Convert enums
        data["color_mode"] = self.color_mode.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DrawingSettings:
        """Create from dictionary."""
        # Convert enums back
        if "color_mode" in data:
            data["color_mode"] = ColorMode(data["color_mode"])
        return cls(**data)


@dataclass
class ToolDefaults:
    """Default settings for tools."""

    # Pen tool
    pen_size: float = 2.0
    pen_opacity: float = 1.0
    pen_hardness: float = 0.8
    pen_flow: float = 1.0
    pen_color: str = "#000000"
    pen_blend_mode: BlendMode = BlendMode.NORMAL
    pen_pressure_size: bool = True
    pen_pressure_opacity: bool = True

    # Eraser tool
    eraser_size: float = 10.0
    eraser_opacity: float = 1.0
    eraser_hardness: float = 0.5
    eraser_mode: str = "normal"  # normal, soft, hard

    # Selection tool
    selection_mode: str = "rectangular"  # rectangular, elliptical, lasso, magic
    selection_feather: float = 0.0
    selection_antialiasing: bool = True
    selection_add_mode: bool = False

    # Fill tool
    fill_tolerance: int = 32
    fill_sample_merged: bool = False
    fill_antialiasing: bool = True

    # Transform tool
    transform_interpolation: str = "bilinear"  # nearest, bilinear, bicubic
    transform_maintain_aspect: bool = True

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        data = asdict(self)
        # Convert enums
        data["pen_blend_mode"] = self.pen_blend_mode.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolDefaults:
        """Create from dictionary."""
        if "pen_blend_mode" in data:
            data["pen_blend_mode"] = BlendMode(data["pen_blend_mode"])
        return cls(**data)


@dataclass
class GenerationSettings:
    """AI generation default settings."""

    # Basic settings
    default_steps: int = 20
    default_cfg_scale: float = 7.0
    default_width: int = 512
    default_height: int = 512
    default_batch_size: int = 1

    # Sampler settings
    default_sampler: SamplerType = SamplerType.EULER
    default_scheduler: str = "normal"
    default_denoise: float = 1.0
    default_seed: int = -1

    # Model preferences
    preferred_checkpoint: str | None = None
    preferred_vae: str | None = None
    preferred_lora: list[str] = field(default_factory=list)

    # Prompt defaults
    default_positive_prompt: str = ""
    default_negative_prompt: str = ""
    prompt_templates: list[dict[str, str]] = field(default_factory=list)

    # Performance
    enable_preview: bool = True
    preview_interval: int = 5
    auto_queue: bool = False
    queue_batch_size: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        data = asdict(self)
        data["default_sampler"] = self.default_sampler.value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GenerationSettings:
        """Create from dictionary."""
        if "default_sampler" in data:
            data["default_sampler"] = SamplerType(data["default_sampler"])
        return cls(**data)


@dataclass
class UISettings:
    """UI and window settings."""

    # Theme
    theme: str = "dark"
    accent_color: str = "#0078D4"
    font_family: str = "Segoe UI"
    font_size: int = 10

    # Window
    save_window_geometry: bool = True
    save_window_state: bool = True
    save_panel_layout: bool = True
    restore_on_startup: bool = True

    # Panels
    show_tool_panel: bool = True
    show_layers_panel: bool = True
    show_properties_panel: bool = True
    show_comfyui_panel: bool = True
    panel_opacity: float = 1.0

    # Behavior
    confirm_on_close: bool = True
    auto_save_interval: int = 300  # seconds
    show_tooltips: bool = True
    show_status_bar: bool = True

    # Recent files
    max_recent_files: int = 10
    max_recent_workflows: int = 10

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UISettings:
        """Create from dictionary."""
        return cls(**data)


@dataclass
class PerformanceSettings:
    """Performance and optimization settings."""

    # Memory management
    max_memory_usage_mb: int = 2048
    cache_enabled: bool = True
    cache_size_mb: int = 512

    # Threading
    use_multithreading: bool = True
    worker_threads: int = 4

    # GPU
    gpu_device: int = 0

    # Optimization
    lazy_loading: bool = True
    progressive_rendering: bool = True
    lod_enabled: bool = True

    # Limits
    max_canvas_size: int = 16384
    max_layer_count: int = 1000
    max_history_size: int = 100

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PerformanceSettings:
        """Create from dictionary."""
        return cls(**data)


@dataclass
class AllSettings:
    """Container for all settings categories."""

    connection: ConnectionSettings = field(default_factory=ConnectionSettings)
    drawing: DrawingSettings = field(default_factory=DrawingSettings)
    tools: ToolDefaults = field(default_factory=ToolDefaults)
    generation: GenerationSettings = field(default_factory=GenerationSettings)
    ui: UISettings = field(default_factory=UISettings)
    performance: PerformanceSettings = field(default_factory=PerformanceSettings)

    def to_dict(self) -> dict[str, Any]:
        """Convert all settings to dictionary."""
        return {
            "connection": self.connection.to_dict(),
            "drawing": self.drawing.to_dict(),
            "tools": self.tools.to_dict(),
            "generation": self.generation.to_dict(),
            "ui": self.ui.to_dict(),
            "performance": self.performance.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AllSettings:
        """Create from dictionary."""
        return cls(
            connection=ConnectionSettings.from_dict(data.get("connection", {})),
            drawing=DrawingSettings.from_dict(data.get("drawing", {})),
            tools=ToolDefaults.from_dict(data.get("tools", {})),
            generation=GenerationSettings.from_dict(data.get("generation", {})),
            ui=UISettings.from_dict(data.get("ui", {})),
            performance=PerformanceSettings.from_dict(data.get("performance", {})),
        )

    def validate_all(self) -> dict[str, list[str]]:
        """Validate all settings and return errors by category."""
        errors = {}

        # Validate connection settings
        conn_errors = self.connection.validate()
        if conn_errors:
            errors["connection"] = conn_errors

        # Add more validation as needed

        return errors
