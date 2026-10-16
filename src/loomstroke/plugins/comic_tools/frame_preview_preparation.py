"""Prepare detached geometry off the qasync UI loop, then validate on return."""

import asyncio
import logging
import threading

from loomstroke.core.comic_panels.edit_draft import PanelEditDraft
from loomstroke.core.comic_panels.models import PanelFrameError

from .panel_edit_gate import require_editable_parts
from .panel_repair_preview import PanelRepairPreview
from .panel_source_guard import source_guard

logger = logging.getLogger(__name__)


def prepared_repair(state, layout, page_size):
    from .panel_preview_vector_preparation import prepare_preview_vectors  # noqa: PLC0415
    from .panel_raster_content_bounds import state_with_raster_bounds  # noqa: PLC0415

    repair = PanelRepairPreview(state_with_raster_bounds(state), layout, page_size=page_size)
    repair.geometry_command()
    return prepare_preview_vectors(repair)


def prepared_template(state, layout, page_size):
    from .panel_template_plan import PanelTemplatePreview  # noqa: PLC0415

    preview = PanelTemplatePreview(state, layout, page_size=page_size)
    return preview.prepare()


def raise_error(error):
    raise error


class FramePreviewPreparation:
    def __init__(self, workflow):
        self.workflow = workflow
        self.task = None
        self.token = None
        self.auto_commit = False
        self._worker_lock = threading.Lock()

    def cancel(self):
        task, self.task, self.token = self.task, None, None
        self.auto_commit = False
        if task is not None:
            task.cancel()

    def start(self, controls, state, layout, *, factory=None, installer=None, auto_commit=False):
        if state.frames_layer_id:
            require_editable_parts(state, (state.frames_layer_id,))
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            if factory is not None:
                self.workflow.cancel()
                preview = factory(state, layout, self.workflow.tool._page_size())
                return self._install(controls, preview, installer, auto_commit)
            preview = self.workflow.preview_resize(layout, state=state)
            return self._complete(controls, preview, auto_commit)
        self.workflow.cancel()
        self.auto_commit = auto_commit
        page_id = self.workflow.page_id()
        page_size = self.workflow.tool._page_size()
        self.token = token = object()
        self.workflow.draft = PanelEditDraft(
            page_id=page_id, label="Resize panels and repair content"
        )
        self.task = loop.create_task(
            self._prepare(
                controls, state, layout, page_id, page_size, token, factory, installer, auto_commit
            )
        )
        return self.task

    async def _prepare(
        self,
        controls,
        state,
        layout,
        page_id,
        page_size,
        token,
        factory=None,
        installer=None,
        auto_commit=False,
    ):
        try:
            repair = await asyncio.to_thread(
                self._prepare_worker, factory or prepared_repair, state, layout, page_size, token
            )
            if self.token is not token:
                return
            if self.workflow.page_id() != page_id:
                raise PanelFrameError("repair draft belongs to another page")
            guard = source_guard(state, page_size=page_size)
            if not guard.apply(self.workflow.tool._canvas()):
                raise PanelFrameError(guard.last_error)
            self.task = self.token = None
            controls.run(lambda: self._install(controls, repair, installer, auto_commit))
        except asyncio.CancelledError:
            return
        except PanelFrameError as exc:
            self._failed(controls, token, exc)
        except Exception:
            logger.exception("comic geometry preparation failed")
            self._failed(controls, token, PanelFrameError("command was rejected"))

    def _failed(self, controls, token, error):
        if self.token is not token:
            return
        self.task = self.token = None
        self.workflow.cancel()
        controls.run(lambda: True)
        controls.run(lambda: raise_error(error))

    def _install(self, controls, preview, installer, auto_commit):
        result = (installer or self.workflow._install_repair)(preview)
        return self._complete(controls, result, auto_commit)

    def _complete(self, controls, preview, auto_commit):
        if not auto_commit:
            return preview
        try:
            repairs = getattr(controls, "repairs", None)
            if repairs is not None:
                repairs.actions.remember(self.workflow.repair)
            result = self.workflow.confirm()
            controls.table.setRowCount(0)
            self.workflow.tool._notify_panel()
            return result
        except PanelFrameError:
            self.workflow.cancel()
            raise

    def _prepare_worker(self, factory, state, layout, page_size, token):
        # Cancelling an asyncio future cannot interrupt a running Qt/Numba
        # computation. Serialize its large temporary masks and skip queued
        # requests which have already been cancelled or replaced.
        with self._worker_lock:
            if self.token is not token:
                return None
            return factory(state, layout, page_size)
