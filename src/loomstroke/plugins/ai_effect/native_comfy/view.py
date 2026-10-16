"""Isolated Chromium view with optional author persistence and an async JS bridge."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any
from urllib.parse import urlsplit

from PySide6.QtCore import QUrl
from PySide6.QtWebEngineCore import (
    QWebEnginePage,
    QWebEngineProfile,
    QWebEngineScript,
    QWebEngineUrlRequestInterceptor,
)
from PySide6.QtWebEngineWidgets import QWebEngineView

from loomstroke.plugins.common.outbound_policy import is_external_services_allowed

from .browser_profile import author_profile
from .browser_scripts import FRONTEND, HIERARCHY, PARAMETERS
from .values import validate_bridge_numbers

logger = logging.getLogger(__name__)


def _script_frames(stack: str) -> str:
    def script_url(match: Any) -> str:
        try:
            url = urlsplit(match.group())
            return f"{url.scheme}://{url.netloc.rsplit('@', 1)[-1]}{url.path}"
        except ValueError:
            return "<script>"

    frames = [line for line in stack.splitlines() if line.lstrip().startswith("at ")][:16]
    return re.sub(r"https?://[^\s)]+", script_url, "\n".join(frames))


class ProbeRequests(QWebEngineUrlRequestInterceptor):
    """Avoid modifying the user's server-side workflows/settings from the web UI."""

    def __init__(self, origin: str, settings: Any, parent: Any) -> None:
        super().__init__(parent)
        self.origin = QUrl(origin)
        self.settings = settings
        self.blocked: list[str] = []
        self.allow_generation = False

    def interceptRequest(self, info: Any) -> None:  # noqa: N802
        url = info.requestUrl()
        if url.scheme() in {"data", "blob", "about", "qrc"}:
            return
        websocket_scheme = "wss" if self.origin.scheme() == "https" else "ws"
        same_origin = url.scheme() in {self.origin.scheme(), websocket_scheme} and (
            url.host(),
            url.port(),
        ) == (self.origin.host(), self.origin.port())
        read_only = bytes(info.requestMethod()) in {b"GET", b"HEAD", b"OPTIONS"}
        blocked = (
            not is_external_services_allowed(self.settings)
            or not same_origin
            or not (
                read_only
                or (
                    bytes(info.requestMethod()) == b"POST"
                    and (
                        url.path() in {"/upload/image", "/api/upload/image"}
                        or (
                            self.allow_generation
                            and url.path() in {"/prompt", "/api/prompt", "/free", "/api/free"}
                        )
                    )
                )
            )
        )
        if blocked:
            self.blocked = [*self.blocked, f"{bytes(info.requestMethod()).decode()} {url.path()}"][
                -30:
            ]
            info.block(True)


class ProbePage(QWebEnginePage):
    def __init__(self, profile: QWebEngineProfile, parent: Any) -> None:
        super().__init__(profile, parent)
        self.errors: list[str] = []
        self.closing_probe = False

    def javaScriptConfirm(self, origin: QUrl, message: str) -> bool:  # noqa: N802
        # Only suppress unload confirmation after our disposable window has closed.
        if self.closing_probe:
            return True
        return super().javaScriptConfirm(origin, message)

    def javaScriptConsoleMessage(
        self, level: Any, message: str, line: int, source: str
    ) -> None:  # noqa: N802
        if level == QWebEnginePage.JavaScriptConsoleMessageLevel.ErrorMessageLevel:
            self.errors = [*self.errors, message[:500]][-30:]


class NativeComfyView(QWebEngineView):
    def __init__(
        self, url: str, settings: Any, parent: Any = None, *, persistent: bool = False
    ) -> None:
        super().__init__(parent)
        self.profile = author_profile(url, self) if persistent else QWebEngineProfile(self)
        self.interceptor = ProbeRequests(url, settings, self.profile)
        self.profile.setUrlRequestInterceptor(self.interceptor)
        self.probe_page = ProbePage(self.profile, self)
        self.setPage(self.probe_page)
        self.url = url
        script = QWebEngineScript()
        script.setName("loomstroke-probe-session-settings")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setRunsOnSubFrames(False)
        script.setSourceCode(r"""
          (() => {
            performance.setResourceTimingBufferSize(10000);
            const realFetch = window.fetch.bind(window);
            // This disposable session opens a supplied workflow, not the first-run gallery.
            const settings = {'Comfy.TutorialCompleted': true};
            window.fetch = async (input, init = {}) => {
              const url = new URL(typeof input === 'string' ? input : input.url, location.href);
              const match = url.pathname.match(/^\/(?:api\/)?settings(?:\/(.*))?$/);
              if (url.origin === location.origin && match) {
                const method = init.method ?? input?.method ?? 'GET';
                if (method === 'POST') {
                  const body = JSON.parse(init.body ?? '{}');
                  if (match[1]) settings[decodeURIComponent(match[1])] = body;
                  else Object.assign(settings, body);
                }
                const value = match[1] ? settings[decodeURIComponent(match[1])] ?? null : settings;
                return new Response(JSON.stringify(value),
                  {status:200, headers:{'Content-Type':'application/json'}});
              }
              return realFetch(input, init);
            };
          })();
        """)
        self.page().scripts().insert(script)

    async def evaluate(self, code: str, timeout: float = 30, *, operation: str = "evaluate") -> Any:
        """Qt runJavaScript doesn't await promises; marshal via a unique request slot."""
        key = "probe_" + uuid.uuid4().hex
        encoded = json.dumps(key)
        self.page().runJavaScript(
            f"Promise.resolve().then(() => ({code})).then("
            f"value => window[{encoded}] = JSON.stringify({{ok:true,value}}),"
            f"error => window[{encoded}] = JSON.stringify({{ok:false,error:String(error),"
            "stack:typeof error?.stack === 'string' ? error.stack : ''}))"
        )
        try:
            async with asyncio.timeout(timeout):
                while True:
                    future = asyncio.get_running_loop().create_future()

                    def receive(value: Any, result: Any = future) -> None:
                        if not result.done():
                            result.set_result(value)

                    self.page().runJavaScript(f"window[{encoded}] ?? null", receive)
                    raw = await future
                    if raw:
                        reply = json.loads(raw)
                        if not reply["ok"]:
                            frames = _script_frames(reply.get("stack", ""))
                            if frames:
                                logger.warning(
                                    "Native ComfyUI JavaScript failure (%s):\n%s", operation, frames
                                )
                            raise RuntimeError(reply["error"])
                        return reply.get("value")
                    await asyncio.sleep(0.05)
        finally:
            self.page().runJavaScript(f"delete window[{encoded}]")

    async def open_frontend(self) -> None:
        failed = False

        def finished(ok: bool) -> None:
            nonlocal failed
            failed = not ok

        self.loadFinished.connect(finished)
        try:
            async with asyncio.timeout(20):
                await self._prepare_frontend(lambda: failed)
        finally:
            self.loadFinished.disconnect(finished)

    async def _prepare_frontend(self, failed: Any) -> None:
        self.setUrl(QUrl(self.url))
        while True:
            if failed():
                raise RuntimeError("ComfyUI page could not be loaded. Check the server URL.")
            try:
                ready = await self.evaluate(
                    "Boolean(window.app?.canvas?.canvas && "
                    "window.LiteGraph?.registered_node_types?.KSampler)",
                    timeout=2,
                )
                if ready:
                    break
            except TimeoutError:
                pass
            await asyncio.sleep(0.5)
        # The frontend restores its initial empty workflow after publishing window.app.
        await asyncio.sleep(2)
        for code in (HIERARCHY, FRONTEND, PARAMETERS):
            await self.evaluate(code)

    async def call(self, method: str, argument: Any = None) -> Any:
        if method not in {
            "focus",
            "describe",
            "draft",
            "snapshot",
            "load",
            "loadApi",
            "sample",
            "setMode",
        }:
            raise ValueError("Unsupported probe operation")
        validate_bridge_numbers(argument)
        return await self.evaluate(
            f"window.loomstrokeProbe.{method}({json.dumps(argument)})", operation=method
        )
