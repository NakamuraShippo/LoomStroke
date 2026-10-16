"""ComfyUI workflow tab and handoff to the existing preset editor."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PySide6.QtWidgets import QComboBox, QFileDialog, QHBoxLayout

from loomstroke.plugins.comfyui.server_registry import get_registry
from loomstroke.plugins.comfyui.session_target import active_url, confirm

from .editor_handoff import apply_snapshot
from .memory import FREE_MEMORY
from .panel import NativePanel, tr


class ComfyAuthorPanel(NativePanel):
    def __init__(self, manager: Any) -> None:
        super().__init__(manager)
        self.manager = manager
        self._source_workflow: Any = None
        self._preserve_editor = False
        self._server_views: dict[str, tuple[Any, Any, bool]] = {}
        self._active_server_id: str | None = None
        self.server_combo = QComboBox(self)
        self.server_combo.activated.connect(self.change_server)
        self.body.insertWidget(0, self.server_combo)
        self.refresh_servers()
        bar = QHBoxLayout()
        for text, action in (
            ("Open ComfyUI", self.resume_previous),
            ("Load workflow…", self.load_file),
            ("Apply changes to Editor", self.configure),
            ("Release VRAM", self.release_memory),
        ):
            button = self.button(text)
            button.clicked.connect(lambda _checked=False, action=action: self.run(action()))
            bar.addWidget(button)
        self.body.insertLayout(1, bar)
        self.status.setText(
            tr("Edit a workflow here, then configure its exposed parameters and outputs.")
        )

    def refresh_servers(self) -> None:
        selected = self.server_combo.currentData() or self._active_server_id or "local"
        self.server_combo.blockSignals(True)
        try:
            self.server_combo.clear()
            for server in get_registry().list_all():
                self.server_combo.addItem(f"{server.name} — {server.url}", server.id)
            self.server_combo.setCurrentIndex(max(0, self.server_combo.findData(selected)))
        finally:
            self.server_combo.blockSignals(False)

    def showEvent(self, event: Any) -> None:  # noqa: N802
        self.refresh_servers()
        super().showEvent(event)

    def change_server(self, _index: int) -> None:
        if self._busy:
            self.server_combo.setCurrentIndex(self.server_combo.findData(self._active_server_id))
            return
        self.run(self.switch_server())

    async def switch_server(self) -> None:
        previous = self._active_server_id
        try:
            if self.view is not None:
                try:
                    idle = await self.view.evaluate(
                        "import('./scripts/api.js').then(async ({api}) => {"
                        "let r; try { r = await api.fetchApi('/queue', "
                        "{signal: AbortSignal.timeout(3000)}); } catch { return true; }"
                        "if (!r.ok) return true;"
                        "const q = await r.json();"
                        "return Array.isArray(q.queue_running) && Array.isArray(q.queue_pending) "
                        "&& !q.queue_running.length && !q.queue_pending.length; })",
                        timeout=5,
                    )
                except TimeoutError:
                    # An unreachable previous server must not prevent manual switching.
                    idle = True
                if not idle:
                    raise ValueError(
                        tr("Wait until the generation queue is empty to switch servers.")
                    )
            await self.ready(explicit=True)
            confirm(get_registry(), self.view.url, explicit=True)
        except BaseException:
            self._active_server_id = previous
            self.server_combo.setCurrentIndex(self.server_combo.findData(previous))
            raise

    async def connect_server(self, server: Any) -> Any:
        url = server.url.rstrip("/")
        if self.view is not None and self.view.url.rstrip("/") != url:
            self._server_views[self.view.url.rstrip("/")] = (
                self.view,
                self._source_workflow,
                self._preserve_editor,
            )
            self.view.hide()
            self.body.removeWidget(self.view)
            self.view = None
        if self.view is None:
            cached = self._server_views.pop(url, None)
            if cached is not None:
                self.view, self._source_workflow, self._preserve_editor = cached
                self.body.addWidget(self.view, 1)
                self.view.show()
            else:
                self._source_workflow = self.manager.editor().current_workflow()
                self._preserve_editor = False
        view = await self.connect_view(server.url, generation=True)
        self._active_server_id = server.id
        return view

    def dispose(self) -> None:
        cached = list(self._server_views.values())
        self._server_views.clear()
        super().dispose()
        for view, _source, _preserve in cached:
            self.view = view
            self.dispose_view()

    async def ready(self, *, explicit: bool = False) -> Any:
        editor = self.manager._ensure_editor()
        if editor is None:
            raise ValueError(tr("Effect editor is unavailable."))
        registry = get_registry()
        self.refresh_servers()
        server_id = self.server_combo.currentData() or "local"
        server = registry.get(server_id)
        if not explicit and active_url(registry):
            server = registry.get_by_url(active_url(registry))
            if server is not None:
                self.server_combo.setCurrentIndex(self.server_combo.findData(server.id))
        if server is None:
            raise ValueError(tr("Select a ComfyUI server in the Editor tab."))
        view = await self.connect_server(server)
        viewport = await view.evaluate(
            "({scale: app.canvas.ds.scale, offset: Array.from(app.canvas.ds.offset)})"
        )
        await view.call("setMode", "workflow")
        await view.evaluate(
            "(() => { const v = " + json.dumps(viewport) + "; "
            "app.canvas.ds.scale = v.scale; app.canvas.ds.offset[0] = v.offset[0]; "
            "app.canvas.ds.offset[1] = v.offset[1]; "
            "app.canvas.setDirty(true, true); })()"
        )
        return view

    async def resume_previous(self) -> None:
        # The persistent frontend restores its drafts/tabs. Do not overwrite
        # them with the Editor's last imported workflow when reopening.
        already_open = self.view is not None
        await self.ready()
        if not already_open:
            self._source_workflow = self.manager.editor().current_workflow()
            self._preserve_editor = False

    async def release_memory(self) -> None:
        if self.view is None:
            raise ValueError(tr("Open ComfyUI before releasing VRAM."))
        result = await self.view.evaluate(FREE_MEMORY)
        if result == "busy":
            self.status.setText(tr("Wait until the generation queue is empty to release VRAM."))
        elif result == "requested":
            self.status.setText(
                tr("VRAM release requested. Models will reload on the next generation.")
            )
        else:
            raise ValueError(tr("Unexpected response to the VRAM release request."))

    async def open_current(self) -> None:
        editor = self.manager._ensure_editor()
        self.refresh_servers()
        self.server_combo.setCurrentIndex(
            self.server_combo.findData(editor._current_target_server_id() or "local")
        )
        view = await self.ready()
        editor = self.manager.editor()
        native = getattr(editor, "native_workflow", None)
        workflow = native or editor.current_workflow()
        if getattr(editor.current_gateway(), "gateway_id", None) != "comfyui":
            workflow = None
        if workflow:
            await view.call("load" if native else "loadApi", workflow)
        self._source_workflow = editor.current_workflow()
        self._preserve_editor = bool(workflow)

    async def load_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Load workflow…"), "", "JSON (*.json)")
        if not path:
            return
        workflow = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        if not isinstance(workflow, dict):
            raise ValueError(tr("Workflow must be a JSON object."))
        view = await self.ready()
        await view.call("load" if isinstance(workflow.get("nodes"), list) else "loadApi", workflow)
        self._source_workflow = self.manager.editor().current_workflow()
        self._preserve_editor = False

    async def configure(self) -> None:
        if self.view is None:
            raise ValueError(tr("Open or load a workflow first."))
        snapshot = await self.view.call("snapshot")
        editor = self.manager.open_editor()
        if editor.current_workflow() is not self._source_workflow:
            raise ValueError(tr("The Editor workflow changed. Reopen it in the ComfyUI tab."))
        apply_snapshot(editor, snapshot, preserve=self._preserve_editor)
        editor._refresh_server_combo()
        index = editor._server_combo.findData(self._active_server_id)
        if index >= 0:
            editor._server_combo.setCurrentIndex(index)
        self._source_workflow = editor.current_workflow()
        self._preserve_editor = True
        self.status.setText(
            tr(
                "Workflow transferred. Select parameters and outputs in the Editor tab, "
                "then register."
            )
        )
