"""Commit normal single-self results without replacing compatibility callbacks."""

from __future__ import annotations

import logging
from inspect import getattr_static
from pathlib import Path
from typing import Any

from .app_ai_effect_gallery import _AIEffectGalleryAdapter
from .app_ai_effect_generation_target import _normal_generation_target_is_current
from .app_ai_effect_output_result import AIEffectOutputApplication
from .core.ai_effect_result_command import _ApplyAIEffectResultCommand
from .core.canvas_history_clear_continuation import _stored_state
from .core.generation_record import GenerationRecord
from .core.layer_command_commit_context import _LayerCommandContext
from .plugins.ai_effect.errors import AIError, ErrorClass
from .plugins.ai_effect.gateway_protocol import OutputImage

logger = logging.getLogger(__name__)


def _cache_store_is_current(app: Any, proof: tuple[Any, Any, Path | None]) -> bool:
    store, base, resolved = proof
    if (
        _stored_state(app).get("_generation_history") is not store
        or _stored_state(store).get("_base") is not base
    ):
        return False
    try:
        return resolved is None or (isinstance(base, Path) and base.resolve() == resolved)
    except (OSError, RuntimeError, ValueError):
        return False


def _eligible_route(app: Any, target: Any) -> tuple[Any, Any, tuple[tuple[str, Any], ...]] | None:
    if target is None:
        return None
    # App imports the coordinator, so consult its completed definition only
    # when handling a result; never infer defaults from names or current classes.
    from .app import _DEFAULT_AI_EFFECT_OUTPUT_APPLICATION  # noqa: PLC0415
    from .core.canvas_service_simple import SimpleCanvasService  # noqa: PLC0415
    from .core.canvas_service_simple_command_api import (  # noqa: PLC0415
        _DEFAULT_RESOURCE_METADATA_HOOKS,
    )
    from .core.canvas_service_simple_filter_api import (  # noqa: PLC0415
        _DEFAULT_AI_RESULT_HOOKS,
    )

    if (
        getattr_static(app, "_apply_ai_effect_outputs", None)
        is not _DEFAULT_AI_EFFECT_OUTPUT_APPLICATION
    ):
        return None
    canvas = _stored_state(app).get("_canvas_service")
    if not isinstance(canvas, SimpleCanvasService):
        return None
    hooks = {"submit": _DEFAULT_RESOURCE_METADATA_HOOKS["submit"], **_DEFAULT_AI_RESULT_HOOKS}
    if any(getattr_static(canvas, name, None) is not original for name, original in hooks.items()):
        return None
    return canvas, _DEFAULT_AI_EFFECT_OUTPUT_APPLICATION, tuple(hooks.items())


class _NormalResultAttempt:
    """One synchronous handoff; no owner or pixel references survive an await."""

    def __init__(
        self,
        app: Any,
        canvas: Any,
        layer: Any,
        state: Any,
        target: Any,
        *,
        callback: Any,
        hooks: tuple[tuple[str, Any], ...],
        cache_store: tuple[Any, Any, Path | None] | None = None,
    ) -> None:
        self.app, self.canvas, self.layer, self.state, self.target = (
            app,
            canvas,
            layer,
            state,
            target,
        )
        self.pages = _stored_state(app).get("_page_service")
        self.scope = _LayerCommandContext(canvas, (layer,))
        self.callback, self.hooks = callback, hooks
        self.cache_store = cache_store

    def current(self) -> bool:
        app_state = _stored_state(self.app)
        return (
            (self.cache_store is None or _cache_store_is_current(self.app, self.cache_store))
            and app_state.get("_canvas_service") is self.canvas
            and app_state.get("_page_service") is self.pages
            and self.scope.current()
            and self.layer.ai_effect is self.state
            and self.layer.layer_type == "ai_effect"
            and getattr_static(self.app, "_apply_ai_effect_outputs", None) is self.callback
            and all(
                getattr_static(self.canvas, name, None) is original for name, original in self.hooks
            )
        )

    def before_commit(self) -> bool:
        return (
            self.current()
            and _normal_generation_target_is_current(self.target, self.app)
            and self.current()
        )


def _single_output_mode(state: Any, image: Any) -> str:
    internal_id = getattr(image, "internal_id", "") or ""
    override = getattr(state, "output_mode_override", None)
    if isinstance(override, str) and override in ("self", "new_layer_above"):
        return override
    snapshot = getattr(state, "preset_snapshot", {}) or {}
    manifest = snapshot.get("manifest", {}) if isinstance(snapshot, dict) else {}
    outputs = manifest.get("outputs", []) if isinstance(manifest, dict) else []
    mode = "self"
    for entry in outputs:
        if not isinstance(entry, dict):
            continue
        identifier = entry.get("internal_id")
        if isinstance(identifier, str) and identifier and identifier == internal_id:
            value = entry.get("mode")
            mode = (
                value if isinstance(value, str) and value in ("self", "new_layer_above") else "self"
            )
    return mode


def _notify_application(
    attempt: _NormalResultAttempt,
    application: AIEffectOutputApplication,
    *,
    show_banner: bool = True,
) -> None:
    if not attempt.current():
        return
    try:
        if application.complete:
            if show_banner:
                attempt.app._maybe_show_first_gen_banner()
        else:
            payload: dict[str, Any] = {}
            application.update_payload(payload)
            error = payload["error"]
            attempt.app._notify_ai_effect_error(
                AIError(
                    ErrorClass.BACKEND,
                    title=error["title"],
                    detail=error["detail"],
                    raw={"code": error["code"]},
                )
            )
    except Exception as exc:
        # The model/history decision is final even if a UI observer fails.
        logger.warning("Normal AI result notification failed (%s)", type(exc).__name__)


def _commit_result_png(
    attempt: _NormalResultAttempt, png: Any, *, show_banner: bool = True
) -> bool:
    application = AIEffectOutputApplication(expected_count=1, failed_count=1)
    if isinstance(png, (bytes, bytearray)) and png:
        command = _ApplyAIEffectResultCommand(attempt.layer.id, bytes(png))
        if not attempt.before_commit():
            return False
        application.failed_count = 0
        application.attempt(attempt.canvas.submit, command)
    elif not attempt.before_commit():
        return False
    if not show_banner and not application.complete:
        _notify_cached_image_error(attempt, missing=False, preparation=False)
    else:
        _notify_application(attempt, application, show_banner=show_banner)
    return application.complete


def _try_commit_normal_single_self(  # noqa: PLR0911 - separate unhandled, stale and refused results
    app: Any, layer_id: str, state: Any, outcome: Any, target: Any
) -> bool | None:
    """None preserves the old route; True/False never request a direct retry."""
    route = _eligible_route(app, target)
    if route is None:
        return None
    canvas, callback, hooks = route
    attempt = None
    try:
        if not _normal_generation_target_is_current(target, app):
            return False
        layer = canvas._find_layer_by_id_tree(layer_id)
        if layer is None:
            return False
        attempt = _NormalResultAttempt(
            app, canvas, layer, state, target, callback=callback, hooks=hooks
        )
        result = getattr(outcome, "result", None)
        images = getattr(result, "output_images", None) or []
        models = getattr(result, "output_models", None) or []
        if not attempt.before_commit():
            return False
        if models or (type(images) is not list and type(images) is not tuple) or len(images) != 1:
            return None
        image = images[0]
        mode = _single_output_mode(state, image)
        if not attempt.before_commit():
            return False
        if mode != "self":
            return None
        png = getattr(image, "png_bytes", None)
        return _commit_result_png(attempt, png)
    except Exception as exc:
        logger.warning("Normal AI result preparation failed (%s)", type(exc).__name__)
        if attempt is not None:
            _notify_application(
                attempt, AIEffectOutputApplication(expected_count=1, failed_count=1)
            )
        return False


def _notify_cached_image_error(
    attempt: _NormalResultAttempt, missing: bool, *, preparation: bool = True
) -> None:
    current = attempt.before_commit if preparation else attempt.current
    if not current():
        return
    try:
        if preparation:
            title = attempt.app._ts("Image file not found" if missing else "No image available")
        else:
            title = attempt.app._ts("Generated outputs could not be fully applied")
        if current():
            attempt.app._notify_ai_effect_error(AIError(ErrorClass.BACKEND, title=title))
    except Exception as exc:
        logger.warning("Cached AI result notification failed (%s)", type(exc).__name__)


def _apply_cached_generation_output(
    app: Any, layer_id: str, state: Any, outcome: Any, target: Any
) -> None:
    """Reuse one saved self image, without Gateway calls or a legacy retry."""
    route = _eligible_route(app, target)
    if route is None:
        return
    canvas, callback, hooks = route
    attempt = None
    try:
        store = _stored_state(app).get("_generation_history")
        base = _stored_state(store).get("_base")
        cache_store = (store, base, base.resolve() if type(base) is type(Path()) else None)
        if not _normal_generation_target_is_current(target, app):
            return
        layer = canvas._find_layer_by_id_tree(layer_id)
        if layer is None:
            return
        attempt = _NormalResultAttempt(
            app,
            canvas,
            layer,
            state,
            target,
            callback=callback,
            hooks=hooks,
            cache_store=cache_store,
        )
        record = getattr(outcome, "cached_record", None)
        if type(record) is not GenerationRecord:
            raise ValueError("Cached result has no record")
        images = record.output_images
        if type(images) is not list:
            raise ValueError("Invalid cached output metadata")
        # The Store does not retain model outputs. This path reuses images only.
        if len(images) != 1:
            return
        image = images[0]
        if type(image) is not dict or type(image.get("internal_id")) is not str:
            raise ValueError("Invalid cached image identifier")
        mode = _single_output_mode(state, OutputImage(image["internal_id"], b"", 0, 0))
        if not attempt.before_commit() or mode != "self":
            return
        cached_image = _AIEffectGalleryAdapter(store)._read_cached_image(record, outcome.param_hash)
        if not attempt.before_commit():
            return
        _commit_result_png(attempt, cached_image.png_bytes, show_banner=False)
    except Exception as exc:
        logger.warning("Cached AI image preparation failed (%s)", type(exc).__name__)
        if attempt is not None:
            _notify_cached_image_error(attempt, isinstance(exc, FileNotFoundError))


def _apply_normal_generation_outputs(
    app: Any, layer_id: str, state: Any, outcome: Any, target: Any
) -> None:
    if _try_commit_normal_single_self(app, layer_id, state, outcome, target) is not None:
        return
    try:
        app._apply_ai_effect_outputs(layer_id, state, outcome)
    except Exception:
        logger.exception("AI Effect outputs application failed for %s", layer_id)
    app._maybe_show_first_gen_banner()
