"""Private job ownership for ComfyUI model output application."""

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from loomstroke.plugins.ai_effect.gateway_protocol import JobResult


@dataclass(frozen=True)
class _ModelApplicationScope:
    target_is_current: Callable[[], bool]
    apply_to_canvas: bool
    defer_mixed: bool


_model_application_scope: ContextVar[_ModelApplicationScope | None] = ContextVar(
    "comfyui_model_application_scope", default=None
)


@contextmanager
def _scope_model_application(
    target_is_current: Callable[[], bool],
    *,
    apply_to_canvas: bool = True,
    defer_mixed: bool = False,
) -> Iterator[None]:
    token = _model_application_scope.set(
        _ModelApplicationScope(target_is_current, apply_to_canvas, defer_mixed)
    )
    try:
        yield
    finally:
        _model_application_scope.reset(token)


@dataclass
class _ModelApplicationResult(JobResult):
    expected_models: int = 0
    applied_models: int = 0
    unavailable_images: int = 0
    # None keeps the legacy application contract; an empty tuple rejects the batch.
    prepared_models: tuple[dict[str, Any], ...] | None = None
