"""
Preview panel compatibility wrapper.

This file maintains backwards compatibility while the actual implementation
has been split into View/Presenter pattern per Master.md A3 specifications.
"""

# Re-export from the new module structure
from .preview import PreviewGraphicsView, PreviewPanel, PreviewPresenter, PreviewView

# Maintain backwards compatibility for direct imports
__all__ = [
    "PreviewGraphicsView",
    "PreviewPanel",
    "PreviewPresenter",
    "PreviewView",
]
