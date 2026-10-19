"""Prepare cold style programs without waiting on the Qt event-loop thread."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from typing import Any

from loomstroke.core.appearance import AppearanceRegistry, LayerStyleStack
from loomstroke.core.appearance.live_styles import layer_styles
from loomstroke.core.appearance.relief_style_parameters import IDENTITY_PROFILE

from ..native_cmyk_completion import NativeCmykCompletion
from .layer_style_evaluator import evaluate_styles
from .style_backdrop import needs_backdrop
from .style_pipeline_store import style_format


def pipeline_keys(scene: Any) -> set[tuple[str, int, bool]]:
    """Plan with the same evaluator as rendering; no GPU/Qt objects enter the worker."""
    keys: set[tuple[str, int, bool]] = set()
    registry = AppearanceRegistry(include_adjustments=False)
    for layer in scene.flat_layers:
        stack = layer_styles(layer)
        styles = [s for s in stack.render_order() if registry.get("style", s.kind)]
        if not layer.visible or not styles:
            continue
        precise = any(
            s.kind in {"three_d", "bevel_emboss"}
            and s.params.get("profile", IDENTITY_PROFILE) != IDENTITY_PROFILE
            for s in styles
        )
        document = needs_backdrop(layer)

        def render(
            src: Any,
            op: int,
            *args: Any,
            _precise: bool = precise,
            _document: bool = document,
            **kw: Any,
        ) -> Any:
            keys.add((style_format(op, _precise), op, _document and op in {4, 9, 11, 12, 13, 14}))
            return src

        def dilate(src: Any, radius: int, **kw: Any) -> Any:
            if radius:
                render(src, 1)
            return render(src, 2)

        base = object()
        render(base, 0)
        render(base, 6)
        render(base, 13 if document else 0)
        evaluate_styles(
            stack,
            registry,
            base,
            base,
            base,
            render=render,
            dilate=dilate,
            release=lambda src: None,
            convert_color=lambda color: tuple(c / 255 for c in color[:3]),
            extract_alpha=lambda src: render(src, 6),
            project=(lambda src: render(src, 12)) if document else None,
        )
    return keys


def without_styles(scene: Any) -> Any:
    """A temporary GPU display snapshot; editable and saved stacks stay untouched."""
    flat: list[Any] = []

    def node(layer: Any) -> Any:
        result = replace(layer, style_stack=LayerStyleStack(), children=[])
        flat.append(result)
        result.children = [node(child) for child in layer.children]
        return result

    layers = tuple(node(layer) for layer in scene.layers)
    return replace(scene, layers=layers, flat_layers=tuple(flat))


class StylePipelinePreparation:
    def __init__(self, store: Any, repaint: Any) -> None:
        self.store = store
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="style-pipelines")
        self._completion = NativeCmykCompletion(repaint)
        self._futures: dict[Any, Any] = {}
        self._closed = False
        self.pending = False

    def ready(self, scene: Any) -> bool:
        keys = pipeline_keys(scene)
        # Observe errors before treating a completed pipeline as usable.
        for key in keys:
            future = self._futures.get(key)
            if future is not None and future.done():
                future.result()
        missing = keys - self.store.pipelines.keys() - self._futures.keys()
        if missing:
            future = self._executor.submit(self._compile, sorted(missing))
            self._futures.update((key, future) for key in missing)
            future.add_done_callback(self._completion.notify)
            self.pending = True
            return False
        self.pending = any(not self._futures[key].done() for key in keys if key in self._futures)
        return not self.pending

    def _compile(self, keys: list[Any]) -> None:
        for key in keys:
            if self._closed:
                return
            self.store.compile(key)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._completion.close()
        self._executor.shutdown(wait=False, cancel_futures=True)
