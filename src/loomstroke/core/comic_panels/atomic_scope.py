"""Hold recomposition and layer notifications until a panel command is coherent."""

from contextlib import contextmanager
from dataclasses import dataclass

from loomstroke.core.events import Events


@dataclass
class AtomicPanelScope:
    committed: bool = False
    root: bool = False


@contextmanager
def panel_atomic_scope(canvas):
    scope = AtomicPanelScope()
    root = getattr(canvas, "_panel_edit_events", None) is None
    scope.root = root
    layer_version = canvas._layer_cache_version
    if root:
        canvas._panel_edit_events = []
    canvas._suppress_doc_events = getattr(canvas, "_suppress_doc_events", 0) + 1
    canvas.defer_recompose()
    try:
        yield scope
    finally:
        canvas._suppress_doc_events -= 1
        pending = canvas._panel_edit_events if root else None
        if root:
            canvas._panel_edit_events = None
        canvas.resume_recompose()
        if root and scope.committed:
            for event_name, kwargs in pending:
                canvas._publish_layer_event(event_name, **kwargs)
            if canvas._layer_cache_version != layer_version:
                # Prepared insertions/patches mutate parts directly. Publish once
                # after the entire edit (or Undo) so observers see a coherent tree.
                canvas._publish_layer_event(
                    Events.LAYER_CHANGED, layer=canvas.current_layer(), change_type="panel_edit"
                )
