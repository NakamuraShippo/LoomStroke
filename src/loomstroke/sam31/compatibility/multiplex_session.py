"""Shim: base predictor forwards a kwarg the multiplex init_state rejects.

``sam3/model/sam3_base_predictor.py`` unconditionally forwards
``offload_state_to_cpu`` to ``model.init_state``; the multiplex tracking
model's ``init_state`` does not accept it, so every ``start_session``
crashes with a TypeError (regression on upstream main). The shim wraps
``predictor.model.init_state`` on the instance and drops kwargs the target
signature does not accept.
"""

from __future__ import annotations

import inspect
from typing import Any

from .registry import CompatibilityStatus, ShimResult

SHIM_NAME = "multiplex_session"
_MARKER = "_loomstroke_sam31_session_shim"


def apply(predictor: Any) -> ShimResult:
    try:
        model = getattr(predictor, "model", None)
        if model is None or not hasattr(model, "init_state"):
            return ShimResult(
                SHIM_NAME,
                CompatibilityStatus.UNSUPPORTED_VERSION,
                "predictor.model has no init_state",
            )
        existing = getattr(model, _MARKER, None)
        if existing is not None:
            return ShimResult(SHIM_NAME, existing, "already evaluated")

        original = model.init_state
        signature = inspect.signature(original)
        parameters = signature.parameters
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD for parameter in parameters.values()
        )
        if accepts_kwargs or "offload_state_to_cpu" in parameters:
            status = CompatibilityStatus.NATIVE
            setattr(model, _MARKER, status)
            return ShimResult(SHIM_NAME, status, "init_state accepts the forwarded kwargs")

        accepted = frozenset(parameters)

        def _tolerant_init_state(**kwargs: Any) -> Any:
            return original(**{key: value for key, value in kwargs.items() if key in accepted})

        model.init_state = _tolerant_init_state
        status = CompatibilityStatus.SHIM_APPLIED
        setattr(model, _MARKER, status)
        return ShimResult(SHIM_NAME, status, "init_state drops kwargs outside its signature")
    except Exception as exc:  # noqa: BLE001 - fail closed with diagnostics
        return ShimResult(SHIM_NAME, CompatibilityStatus.FAILED, f"{type(exc).__name__}: {exc}")


__all__ = ["SHIM_NAME", "apply"]
