"""AI Effect Layer — Qt UI subpackage.

Widgets here import PySide6 at module scope. The parent
``loomstroke.plugins.ai_effect`` package intentionally does NOT re-export this
subpackage so non-Qt consumers (data-model-only tests, CLI tooling) keep
working without Qt available.
"""
