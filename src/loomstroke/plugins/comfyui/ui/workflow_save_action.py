"""Save a snapshot of the workflow currently displayed in the ComfyUI panel."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from shiboken6 import isValid

logger = logging.getLogger(__name__)


def save_displayed_workflow(panel: Any) -> None:
    path, current = panel._workflow_file, panel.current_workflow
    if path is None or current is None or panel._workflow_save_task is not None:
        return
    try:
        loop = asyncio.get_running_loop()
        workflow = current.clone()
    except Exception as error:
        _report_failure(panel, error)
        return

    service = panel.service
    panel.save_workflow_button.setEnabled(False)

    async def save() -> None:
        try:
            await service.save_workflow(workflow, path)
        except Exception as error:
            if isValid(panel) and panel._workflow_file == path:
                _report_failure(panel, error)
            else:
                logger.exception("ComfyUI workflow save failed")
        else:
            if (
                isValid(panel)
                and panel._workflow_file == path
                and panel.current_workflow is current
            ):
                panel.status_message.emit(
                    panel._ts("Saved workflow: {name}").format(name=path.name)
                )
        finally:
            if isValid(panel):
                panel._workflow_save_task = None
                panel.save_workflow_button.setEnabled(panel._workflow_file is not None)

    panel._workflow_save_task = loop.create_task(save())


def _report_failure(panel: Any, error: Exception) -> None:
    logger.warning("ComfyUI workflow save failed: %s", type(error).__name__)
    panel.status_message.emit(panel._ts("Failed to save workflow:\n{error}").format(error=error))
