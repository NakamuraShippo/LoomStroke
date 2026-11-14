"""Commit ordinary RGB results without replacing compatibility callbacks."""

from __future__ import annotations

import logging
from inspect import getattr_static
from pathlib import Path
from typing import Any

from .app_ai_effect_gallery import _AIEffectGalleryAdapter
from .app_ai_effect_generation_target import _normal_generation_target_is_current
from .app_ai_effect_output_names import output_layer_names
from .app_ai_effect_output_result import AIEffectOutputApplication, observed_output_application
from .core.ai_effect_output_layer_command import (
    _CreateAIEffectOutputLayersCommand,
    _NewLayerOutput,
    _standard_output_canvas,
)
from .core.ai_effect_result_command import _ApplyAIEffectResultCommand
from .core.canvas_history_clear_continuation import _stored_state
from .core.generation_record import GenerationRecord
from .core.layer_command_commit_context import _LayerCommandContext
from .plugins.ai_effect.errors import AIError, ErrorClass
from .plugins.ai_effect.gateway_protocol import OutputImage
from .plugins.ai_effect.gateways.comfyui_model_application import _ModelApplicationResult

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


def _can_commit_mixed_outputs(app: Any, target: Any) -> bool:
    route = _eligible_route(app, target)
    return route is not None and _standard_output_canvas(route[0])


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
) -> None:
    try:
        if not attempt.current():
            return
        if application.complete:
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


def _commit_result_png(attempt: _NormalResultAttempt, png: Any) -> AIEffectOutputApplication | None:
    application = AIEffectOutputApplication(expected_count=1, failed_count=1)
    if isinstance(png, (bytes, bytearray)) and png:
        command = _ApplyAIEffectResultCommand(attempt.layer.id, bytes(png))
        if not attempt.before_commit():
            return None
        application.failed_count = 0
        application.attempt(attempt.canvas.submit, command)
    elif not attempt.before_commit():
        return None
    return application


def _prepare_output_command(
    attempt: _NormalResultAttempt,
    images: Any,
    *,
    self_index: int | None,
    output_state: Any,
    models: tuple[dict[str, Any], ...] = (),
) -> _CreateAIEffectOutputLayersCommand | None:
    names = output_layer_names(attempt.canvas, attempt.layer.id, state=output_state)
    if names is None:
        return None
    outputs = []
    self_png = None
    for index, image in enumerate(images):
        png = getattr(image, "png_bytes", None)
        if not isinstance(png, (bytes, bytearray)) or not png:
            return None
        if index == self_index:
            self_png = bytes(png)
        else:
            slot = getattr(image, "internal_id", "") or ""
            outputs.append(_NewLayerOutput(names(slot), slot or None, bytes(png)))
    if not attempt.before_commit():
        return None
    return _CreateAIEffectOutputLayersCommand(
        attempt.layer.id, tuple(outputs), self_png=self_png, model_layers=models
    )


def _commit_output_layers(
    attempt: _NormalResultAttempt, images: Any, *, self_index: int | None, output_state: Any
) -> AIEffectOutputApplication:
    expected = len(images)
    application = AIEffectOutputApplication(expected_count=expected, failed_count=expected)
    command = _prepare_output_command(
        attempt, images, self_index=self_index, output_state=output_state
    )
    if command is not None:
        application.failed_count = 0
        if application.attempt(attempt.canvas.submit, command):
            application.applied_count = expected
        else:
            application.failed_count = expected
    return application


def _submit_mixed_outputs(
    attempt: _NormalResultAttempt, result: _ModelApplicationResult, output_state: Any
) -> bool:
    images = result.output_images
    models = result.prepared_models
    if (
        not images
        or not models
        or len(models) != result.expected_models
        or result.unavailable_images
        or not _standard_output_canvas(attempt.canvas)
    ):
        return False
    modes = tuple(_single_output_mode(output_state, image) for image in images)
    if modes.count("self") > 1 and getattr(output_state, "output_mode_override", None) != "self":
        return False
    command = _prepare_output_command(
        attempt,
        images,
        self_index=modes.index("self") if "self" in modes else None,
        output_state=output_state,
        models=models,
    )
    return command is not None and bool(attempt.canvas.submit(command))


def _commit_mixed_result(
    app: Any,
    layer_id: str,
    state: Any,
    result: _ModelApplicationResult,
    target: Any,
    output_state: Any,
    *,
    report_via_mcp: bool,
) -> AIEffectOutputApplication:
    expected = len(result.output_images) + result.unavailable_images + result.expected_models
    application = AIEffectOutputApplication(expected_count=expected, failed_count=expected)
    route = _eligible_route(app, target)
    if route is None or not _normal_generation_target_is_current(target, app):
        return application
    canvas, callback, hooks = route
    layer = canvas._find_layer_by_id_tree(layer_id)
    if layer is None:
        return application
    attempt = _NormalResultAttempt(
        app, canvas, layer, state, target, callback=callback, hooks=hooks
    )
    try:
        if attempt.before_commit() and _submit_mixed_outputs(attempt, result, output_state):
            application.applied_count = expected
            application.failed_count = 0
    except Exception as exc:
        logger.warning("Mixed AI output preparation failed (%s)", type(exc).__name__)
    if not report_via_mcp:
        _notify_application(attempt, application)
    return application


def _try_commit_normal_result(  # noqa: PLR0911 - separate unhandled, stale and refused results
    app: Any,
    layer_id: str,
    state: Any,
    outcome: Any,
    target: Any,
    *,
    output_state: Any = None,
    report_via_mcp: bool = False,
) -> AIEffectOutputApplication | None:
    """Return the observed result, or leave an unsupported route to its existing callback."""
    result = getattr(outcome, "result", None)
    route = _eligible_route(app, target)
    if route is None:
        return None
    canvas, callback, hooks = route
    attempt = None
    try:
        layer = (
            canvas._find_layer_by_id_tree(layer_id)
            if _normal_generation_target_is_current(target, app)
            else None
        )
        if layer is None:
            return observed_output_application(False, outcome)
        attempt = _NormalResultAttempt(
            app, canvas, layer, state, target, callback=callback, hooks=hooks
        )
        images = getattr(result, "output_images", None) or []
        models = getattr(result, "output_models", None) or []
        if not attempt.before_commit():
            return observed_output_application(False, outcome)
        if (
            type(images) not in (list, tuple)
            or not images
            or (models and not isinstance(result, _ModelApplicationResult))
        ):
            return None
        output_state = state if output_state is None else output_state
        modes = tuple(_single_output_mode(output_state, image) for image in images)
        if not attempt.before_commit():
            return observed_output_application(False, outcome)
        # The explicit self override sends everything after the first image
        # to sibling layers. Other multiple-self manifests retain their route.
        if (
            modes.count("self") > 1
            and getattr(output_state, "output_mode_override", None) != "self"
        ):
            return None
        single_self = modes == ("self",)
        if (report_via_mcp or not single_self) and not _standard_output_canvas(canvas):
            return None
        if single_self:
            application = _commit_result_png(attempt, getattr(images[0], "png_bytes", None))
            if application is None:
                return observed_output_application(False, outcome)
        else:
            self_index = modes.index("self") if "self" in modes else None
            application = _commit_output_layers(
                attempt, images, self_index=self_index, output_state=output_state
            )
    except Exception as exc:
        logger.warning("Normal AI result preparation failed (%s)", type(exc).__name__)
        application = observed_output_application(False, outcome)
    if attempt is not None and not report_via_mcp:
        application.include_model_outputs(result)
        _notify_application(attempt, application)
    return application


def _try_commit_generation_result(
    app: Any,
    layer_id: str,
    state: Any,
    outcome: Any,
    target: Any,
    *,
    output_state: Any = None,
    report_via_mcp: bool = False,
) -> AIEffectOutputApplication | None:
    result = getattr(outcome, "result", None)
    if isinstance(result, _ModelApplicationResult) and result.prepared_models is not None:
        return _commit_mixed_result(
            app,
            layer_id,
            state,
            result,
            target,
            state if output_state is None else output_state,
            report_via_mcp=report_via_mcp,
        )
    return _try_commit_normal_result(
        app,
        layer_id,
        state,
        outcome,
        target,
        output_state=output_state,
        report_via_mcp=report_via_mcp,
    )


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
        application = _commit_result_png(attempt, cached_image.png_bytes)
        if application is not None and not application.complete:
            _notify_cached_image_error(attempt, missing=False, preparation=False)
    except Exception as exc:
        logger.warning("Cached AI image preparation failed (%s)", type(exc).__name__)
        if attempt is not None:
            _notify_cached_image_error(attempt, isinstance(exc, FileNotFoundError))


def _apply_normal_generation_outputs(
    app: Any, layer_id: str, state: Any, outcome: Any, target: Any
) -> str:
    application = _try_commit_generation_result(app, layer_id, state, outcome, target)
    if application is not None:
        result = getattr(outcome, "result", None)
        if (
            isinstance(result, _ModelApplicationResult)
            and result.prepared_models is not None
            and not application.complete
        ):
            return "error"
        return "success"
    try:
        app._apply_ai_effect_outputs(layer_id, state, outcome)
    except Exception:
        logger.exception("AI Effect outputs application failed for %s", layer_id)
    app._maybe_show_first_gen_banner()
    return "success"
