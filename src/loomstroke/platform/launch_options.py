"""Session-only launch policy shared by startup, plugins and UI.

Do not persist this policy: omitting the flag must restore the normal app.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

_AI_PLUGIN_PACKAGES = frozenset(
    {
        "ai_effect",
        "sam3_ai_select",
        "comfyui",
        "llm_gateway",
        "gptimage2",
        "nanobanana",
        "prompt_editor",
        "three_d_generation",
    }
)
AI_PREFERENCES_CATEGORIES = frozenset({"AI Effect", "SAM 3.1", "LoomStroke MCP"})


def ai_features_enabled(argv: Sequence[str] | None = None) -> bool:
    """Whether this process permits AI features (``--no-ai`` disables them).

    Leave Qt arguments and file arguments intact. A flag after ``--`` is a
    positional argument, not a launch option.
    """
    arguments = list(sys.argv[1:] if argv is None else argv)
    if "--" in arguments:
        arguments = arguments[: arguments.index("--")]
    return "--no-ai" not in arguments


def is_ai_plugin_module(module: str) -> bool:
    """Identify built-in AI contributions before importing their factories."""
    prefix = "loomstroke.plugins."
    return module.startswith(prefix) and module[len(prefix) :].split(".")[0] in _AI_PLUGIN_PACKAGES


def enabled_plugin_modules(modules: Sequence[str]) -> list[str]:
    """Keep the existing catalog order while excluding AI in drawing mode."""
    return [
        module for module in modules if ai_features_enabled() or not is_ai_plugin_module(module)
    ]
