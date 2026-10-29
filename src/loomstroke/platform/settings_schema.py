"""
Default settings schema and values.

Per Master.md PR-D specifications, defines all default
configuration values for the application.
"""

from __future__ import annotations

from collections.abc import Mapping, MutableMapping, Sequence
from typing import TypeAlias, TypedDict

SettingsPrimitive: TypeAlias = bool | int | float | str | None
SettingsValue: TypeAlias = (
    SettingsPrimitive | Sequence["SettingsValue"] | Mapping[str, "SettingsValue"]
)
SettingsScope: TypeAlias = Mapping[str, SettingsValue]
MutableSettingsScope: TypeAlias = MutableMapping[str, SettingsValue]


class ConnectionSettings(TypedDict):
    base_url: str
    ws_url: str
    verify_tls: bool
    timeout_s: int
    max_retries: int
    backoff_factor: float


class CanvasSettings(TypedDict):
    width: int
    height: int
    dpi: int
    tile_size: int
    max_layers: int
    use_memmap_threshold: int


# Default connection settings
DEFAULT_COMFYUI_HOST = "127.0.0.1"
DEFAULT_COMFYUI_PORT = 8188
DEFAULT_COMFYUI_URL = f"http://{DEFAULT_COMFYUI_HOST}:{DEFAULT_COMFYUI_PORT}"
DEFAULT_COMFYUI_WS = f"ws://{DEFAULT_COMFYUI_HOST}:{DEFAULT_COMFYUI_PORT}"

MCP_LIVE_HTTP_ENABLED_KEY = "mcp/live_http/enabled"
MCP_LIVE_HTTP_START_ON_LAUNCH_KEY = "mcp/live_http/start_on_launch"
MCP_LIVE_HTTP_PORT_KEY = "mcp/live_http/port"
MCP_LIVE_HTTP_ALLOWED_ORIGINS_KEY = "mcp/live_http/allowed_origins"
MCP_LIVE_HTTP_ROOTS_KEY = "mcp/live_http/roots"
MCP_LIVE_HTTP_SESSION_TOKEN_KEY = "mcp/live_http/session_token"
MCP_CONFIRMATION_POLICY_KEY = "mcp/confirmation_policy"
MCP_CONFIRMATION_POLICY_ALWAYS_ALLOW = "always_allow"
MCP_CONFIRMATION_POLICY_EXTERNAL_ONLY = "external_only"
MCP_CONFIRMATION_POLICY_ALWAYS_ASK = "always_ask"
MCP_CONFIRMATION_POLICY_VALUES = (
    MCP_CONFIRMATION_POLICY_ALWAYS_ALLOW,
    MCP_CONFIRMATION_POLICY_EXTERNAL_ONLY,
    MCP_CONFIRMATION_POLICY_ALWAYS_ASK,
)
# Opt-in: with always_allow, also skip PCT hard confirmations (changesets etc.).
MCP_SKIP_HARD_CONFIRMATIONS_KEY = "mcp/skip_hard_confirmations"
DEFAULT_MCP_LIVE_HTTP_PORT = 37645

# AI Select runtime (SAM 3.1 backend; "fake" is a developer/testing flag)
AI_SELECT_RUNTIME_MODE_KEY = "ai_select/runtime/mode"
AI_SELECT_FAKE_CANDIDATE_COUNT_KEY = "ai_select/runtime/fake_candidate_count"
AI_SELECT_ASSETS_MAX_CACHE_MB_KEY = "ai_select/assets/max_cache_mb"

# SAM 3.1 runtime (one-click setup; worker running != model loaded)
SAM31_ROOT_DIR_KEY = "sam31/root_dir"
SAM31_RUNTIME_DEVICE_KEY = "sam31/runtime/device"
SAM31_SERVER_AUTO_START_KEY = "sam31/server/auto_start"
SAM31_RUNTIME_REQUEST_TIMEOUT_S_KEY = "sam31/runtime/request_timeout_s"
DEFAULT_SAM31_RUNTIME_DEVICE = "auto"
DEFAULT_SAM31_RUNTIME_REQUEST_TIMEOUT_S = 120
AI_SELECT_RUNTIME_MODE_SAM31 = "sam31"
AI_SELECT_RUNTIME_MODE_FAKE = "fake"
AI_SELECT_RUNTIME_MODE_VALUES = (
    AI_SELECT_RUNTIME_MODE_SAM31,
    AI_SELECT_RUNTIME_MODE_FAKE,
)
DEFAULT_AI_SELECT_RUNTIME_MODE = AI_SELECT_RUNTIME_MODE_SAM31
DEFAULT_AI_SELECT_FAKE_CANDIDATE_COUNT = 2
DEFAULT_AI_SELECT_ASSETS_MAX_CACHE_MB = 2048

# Default settings structure
DEFAULT_SETTINGS: dict[str, SettingsValue] = {
    # Connection settings (per PR-D spec)
    "connection/base_url": DEFAULT_COMFYUI_URL,
    "connection/ws_url": DEFAULT_COMFYUI_WS,
    "connection/verify_tls": True,  # PR-D spec default
    "connection/timeout_s": 15,  # PR-D spec default
    "connection/max_retries": 3,
    "connection/backoff_factor": 2.0,
    # Canvas settings
    "canvas/width": 832,
    "canvas/height": 1216,
    "canvas/dpi": 96,
    "canvas/tile_size": 256,
    "canvas/max_layers": 100,
    "canvas/use_memmap_threshold": 10_000_000,
    # Tool settings
    "tools/default": "basic_pen",
    "tools/basic_pen/size": 10.0,
    "tools/basic_pen/opacity": 1.0,
    "tools/basic_pen/hardness": 0.8,
    "tools/basic_eraser/size": 20.0,
    "tools/basic_eraser/opacity": 1.0,
    "tools/basic_fill_bucket/color_tolerance": 8,
    "tools/basic_fill_bucket/gap_close_px": 0,
    "tools/basic_fill_bucket/fill_target": "active",
    # Color management defaults
    "color/state": {
        "current": {"rgba": [0.0, 0.0, 0.0, 1.0], "blend_mode": "normal", "meta": None},
        "previous": None,
        "history": [],
        "max_history": 50,
    },
    # Render settings (per PR-D spec)
    "render/tile_size": 256,
    "render/cache_size": 100,
    # Privacy/consent (default OFF for external distribution)
    "privacy/allow_external_services": False,
    # GPT Image 2 credential source. Off keeps the dedicated
    # (gptimage2, api) key path; on reuses LLM Gateway's OpenAI key
    # (llm_gateway, openai) without copying the credential value.
    "gptimage2/use_openai_api_key": False,
    # LoomStroke MCP live HTTP (local only, explicit opt-in)
    MCP_LIVE_HTTP_ENABLED_KEY: False,
    MCP_LIVE_HTTP_START_ON_LAUNCH_KEY: False,
    MCP_LIVE_HTTP_PORT_KEY: DEFAULT_MCP_LIVE_HTTP_PORT,
    MCP_LIVE_HTTP_ALLOWED_ORIGINS_KEY: [],
    MCP_LIVE_HTTP_ROOTS_KEY: [],
    MCP_LIVE_HTTP_SESSION_TOKEN_KEY: "",
    MCP_CONFIRMATION_POLICY_KEY: MCP_CONFIRMATION_POLICY_ALWAYS_ASK,
    MCP_SKIP_HARD_CONFIRMATIONS_KEY: False,
    # AI Select runtime (SAM 3.1 is the only production backend; fail-closed
    # until the one-click Setup produced a ready sam31 root)
    AI_SELECT_RUNTIME_MODE_KEY: DEFAULT_AI_SELECT_RUNTIME_MODE,
    AI_SELECT_FAKE_CANDIDATE_COUNT_KEY: DEFAULT_AI_SELECT_FAKE_CANDIDATE_COUNT,
    AI_SELECT_ASSETS_MAX_CACHE_MB_KEY: DEFAULT_AI_SELECT_ASSETS_MAX_CACHE_MB,
    # SAM 3.1 runtime
    SAM31_ROOT_DIR_KEY: "",
    SAM31_RUNTIME_DEVICE_KEY: DEFAULT_SAM31_RUNTIME_DEVICE,
    SAM31_SERVER_AUTO_START_KEY: False,
    SAM31_RUNTIME_REQUEST_TIMEOUT_S_KEY: DEFAULT_SAM31_RUNTIME_REQUEST_TIMEOUT_S,
    # UI settings (per PR-D spec)
    "ui/theme": "dark",
    "ui/language": "system",
    "ui/show_toolbar": True,
    "ui/show_statusbar": True,
    "ui/dock_positions": {},
    "ui/show_experimental_menus": False,
    "ui/menus/view/enabled": True,
    "ui/menus/layer/enabled": True,
    "ui/menus/filter/enabled": True,
    "ui/menus/tools/enabled": False,
    "ui/menus/ai/enabled": False,
    "ui/menus/plugins/enabled": False,
    # View / rulers / guides (Photoshop-style)
    "view/rulers_visible": False,
    "view/grid_visible": False,
    "view/grid_size": 32,
    "view/grid_snap": False,
    "view/grid_kind": "square",
    "view/grid_y_size": 32,
    "view/grid_major_every": 5,
    "view/grid_follow_ruler_origin": False,
    "view/grid_rotation": 0.0,
    "view/grid_polar_divisions": 12,
    "view/grid_baseline_spacing": 20.0,
    "view/grid_baseline_start": 0.0,
    "view/grid_baseline_unit": "px",
    "view/composition_overlay_kinds": [],
    "view/composition_overlay_reference": "canvas",
    "view/composition_overlay_safe_percent": 10.0,
    "view/composition_overlay_offsets": {},
    "view/composition_overlay_rotation": 0.0,
    "view/ruler_unit": "px",
    "view/ruler_thickness_px": 18,
    "view/guides_color": "#00B7FF",
    "view/guides_visible": True,
    "view/guides_locked": False,
    "view/spread_binding_overlay": False,
    "view/trim_preview": False,
    "view/canvas_edge_snap": True,
    "view/canvas_center_snap": False,
    "view/guides_opacity": 0.8,
    "view/guides_hit_slop_px": 6,
    "view/guides_snap_threshold_px": 8,
    # Per-page zoom/pan (CT-06 B4-c6).  Off by default: keeping one view
    # across pages is the behaviour every existing document was drawn with.
    "view/per_page_zoom_enabled": False,
    # Input settings
    "input/pan_gesture": "space_left",
    "input/context_menu_gesture": "right_click",
    "input/brush_size_key_acceleration": "standard",
    # StartPage settings
    "plugins/ui_startpage/last_canvas": {
        "width_px": 832,
        "height_px": 1216,
        "dpi": 72,
        "background": {"kind": "solid", "color_rgba": [255, 255, 255, 255]},
        "auto_fix_multiple_of_64": True,
    },
    "plugins/ui_startpage/recent_files": [],
    "plugins/ui_startpage/recent_files_max": 10,
    # Performance settings
    # Spread viewport: how sharply the facing page is composited (CT-06
    # B4-c5).  Full is the default because a spread is read as one picture;
    # the lower steps trade that for GPU memory and composite cost.
    "performance/spread_inactive_page_quality": "full",
    "performance/use_numba": True,
    "performance/max_undo_steps": 50,
    "performance/recompose_fps": 60,
    "performance/effect_cache_limit_mb": 512,
    # Screen-Space Vector Overlay (SSO Phase 1) — spec:
    # docs/dev/plan/future/Feature/resolution_independent_vector/01_design_spec.md §9.2
    # Beta 期は opt-in 配信 (§9.1) のためデフォルト False。
    "performance/vector_overlay_enabled": False,
    "performance/vector_overlay_max_dim": 4096,
    # Direct WGPU vector rendering (retained-mode) — spec:
    # docs/dev/plan/future/vector/LOOMSTROKE_WGPU_RETAINED_VECTOR_RENDERING_DESIGN_SPEC.md §27
    # V8 (2026-07-15 実機 QA 通過) でデフォルト ON。問題時はこのキーを False に
    # すると次フレームから従来の raster 経路へ戻る (document 不変)。
    # 環境変数 LOOMSTROKE_DIRECT_VECTOR_RENDERING=1 は settings と OR で強制有効化。
    "performance/direct_vector_rendering_enabled": True,
    # View 解像度合成 — spec:
    # docs/dev/plan/future/vector/LOOMSTROKE_WGPU_VIEW_RESOLUTION_COMPOSITING_SPEC.md
    # 全ベクター/テキストレイヤーを任意ズームでシャープに表示する。C3 の実測
    # (4K 30 レイヤー合成 3-5ms) を経て既定 ON。既存ストアは
    # _migrate_view_compositing_default で一度だけ昇格される。
    # 環境変数 LOOMSTROKE_VIEW_COMPOSITING=1 でも強制有効化できる。
    "performance/view_resolution_compositing_enabled": True,
    # PD-3 計測 (Phase 1b): SSO の _render_layer_to_overlay / render_preview の
    # 所要時間を記録し 100 サンプル毎に DEBUG ログで p50/p95 を出力。有効化中
    # は time.perf_counter 呼出分の僅かなオーバーヘッドあり。dogfood のみ。
    "performance/vector_overlay_debug_timing": False,
    # File I/O settings
    "io/autosave_enabled": True,
    "io/autosave_interval": 300,  # seconds
    "io/compression_quality": 95,
    "io/default_format": "png",
    "io/layout_directory": "",
    # ComfyUI server URL registry. Local URL may be blank; runtime uses
    # DEFAULT_COMFYUI_URL. ``external_url`` is legacy first-external
    # compatibility, while ``external_servers`` stores the editable list.
    "comfyui.local_url": "",
    "comfyui.local_folder": "",
    "comfyui.auto_start": False,
    "comfyui.show_console": False,
    "comfyui.stop_on_exit": False,
    "comfyui.external_url": "",
    "comfyui.external_servers": [],
}

__all__ = [
    "AI_SELECT_ASSETS_MAX_CACHE_MB_KEY",
    "AI_SELECT_FAKE_CANDIDATE_COUNT_KEY",
    "AI_SELECT_RUNTIME_MODE_FAKE",
    "AI_SELECT_RUNTIME_MODE_KEY",
    "AI_SELECT_RUNTIME_MODE_SAM31",
    "AI_SELECT_RUNTIME_MODE_VALUES",
    "DEFAULT_AI_SELECT_ASSETS_MAX_CACHE_MB",
    "DEFAULT_AI_SELECT_FAKE_CANDIDATE_COUNT",
    "DEFAULT_AI_SELECT_RUNTIME_MODE",
    "DEFAULT_COMFYUI_HOST",
    "DEFAULT_COMFYUI_PORT",
    "DEFAULT_COMFYUI_URL",
    "DEFAULT_COMFYUI_WS",
    "DEFAULT_MCP_LIVE_HTTP_PORT",
    "DEFAULT_SETTINGS",
    "MCP_CONFIRMATION_POLICY_ALWAYS_ALLOW",
    "MCP_CONFIRMATION_POLICY_ALWAYS_ASK",
    "MCP_CONFIRMATION_POLICY_EXTERNAL_ONLY",
    "MCP_CONFIRMATION_POLICY_KEY",
    "MCP_CONFIRMATION_POLICY_VALUES",
    "MCP_LIVE_HTTP_ALLOWED_ORIGINS_KEY",
    "MCP_LIVE_HTTP_ENABLED_KEY",
    "MCP_LIVE_HTTP_PORT_KEY",
    "MCP_LIVE_HTTP_ROOTS_KEY",
    "MCP_LIVE_HTTP_SESSION_TOKEN_KEY",
    "MCP_LIVE_HTTP_START_ON_LAUNCH_KEY",
    "MCP_SKIP_HARD_CONFIRMATIONS_KEY",
    "SAM31_ROOT_DIR_KEY",
    "SAM31_RUNTIME_DEVICE_KEY",
    "SAM31_RUNTIME_REQUEST_TIMEOUT_S_KEY",
    "SAM31_SERVER_AUTO_START_KEY",
    "CanvasSettings",
    "ConnectionSettings",
    "MutableSettingsScope",
    "SettingsPrimitive",
    "SettingsScope",
    "SettingsValue",
]
