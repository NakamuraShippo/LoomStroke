"""Opt-in launch of an existing local ComfyUI installation."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from loomstroke.platform.user_dirs import plugin_data_dir
from loomstroke.plugins.common.outbound_policy import is_external_services_allowed
from loomstroke.ui.translations import translate_with_fallback

from .server_registry import configured_servers_from_settings, get_registry
from .session_target import confirm

logger = logging.getLogger(__name__)
FOLDER_KEY = "comfyui.local_folder"
AUTO_START_KEY = "comfyui.auto_start"
STOP_ON_EXIT_KEY = "comfyui.stop_on_exit"
STARTUP_STATES: dict[str, tuple[str, str]] = {}


def launch_command(folder: str, url: str) -> tuple[Path, list[str]]:
    root = Path(folder).expanduser().resolve()
    if not (root / "main.py").is_file() and (root / "ComfyUI" / "main.py").is_file():
        root /= "ComfyUI"
    if not (root / "main.py").is_file():
        raise ValueError("Select a ComfyUI folder containing main.py.")
    endpoint = urlsplit(url)
    if (
        endpoint.scheme != "http"
        or endpoint.hostname not in {"127.0.0.1", "localhost", "::1"}
        or endpoint.path not in {"", "/"}
        or endpoint.query
        or endpoint.fragment
        or endpoint.username
        or endpoint.password
    ):
        raise ValueError("Automatic startup requires a local HTTP URL without a path.")
    candidates = [root.parent / "python_embeded" / "python.exe"]
    for name in (".venv", "venv"):
        candidates.extend([root / name / "Scripts" / "python.exe", root / name / "bin" / "python"])
    python = next((path for path in candidates if path.is_file()), None)
    if python is None:
        raise ValueError("No embedded Python or ComfyUI virtual environment was found.")
    host = "127.0.0.1" if endpoint.hostname == "localhost" else endpoint.hostname
    args = [
        str(python),
        "-s",
        str(root / "main.py"),
        "--listen",
        str(host),
        "--port",
        str(endpoint.port or 80),
        "--disable-auto-launch",
    ]
    if python == candidates[0]:
        args.append("--windows-standalone-build")
    return root, args


async def server_ready(session: aiohttp.ClientSession, url: str) -> bool:
    try:
        async with session.get(url.rstrip("/") + "/system_stats") as response:
            if response.status == 200:
                data = await response.json()
                if isinstance(data, dict) and "system" in data and "devices" in data:
                    return True
            raise ValueError("The local URL is occupied by a service other than ComfyUI.")
    except (aiohttp.ClientConnectorError, aiohttp.ConnectionTimeoutError):
        return False


def spawn(root: Path, args: list[str]) -> subprocess.Popen:
    log_path = plugin_data_dir("comfyui") / "startup.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as log:
        return subprocess.Popen(  # noqa: S603 - configured local installation, no shell
            args,
            cwd=root,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )


async def start(settings: Any, notify: Any, on_spawn: Any = None) -> None:
    if not settings.get(AUTO_START_KEY, False) or not is_external_services_allowed(settings):
        return
    url = ""
    try:
        url = configured_servers_from_settings(settings)[0].url
        if urlsplit(url).hostname not in {"127.0.0.1", "localhost", "::1"}:
            notify(
                "Remote ComfyUI uses the URL saved in Preferences; automatic startup is local only."
            )
            return
        folder = str(settings.get(FOLDER_KEY, "") or "").strip()
        if not folder:
            raise ValueError("Select a ComfyUI folder containing main.py.")
        root, args = await asyncio.to_thread(launch_command, folder, url)
        timeout = aiohttp.ClientTimeout(total=5, sock_connect=1, sock_read=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if await server_ready(session, url):
                confirm(get_registry(), url)
                STARTUP_STATES.pop(url, None)
                notify("ComfyUI is already running.")
                return
            if not is_external_services_allowed(settings):
                return
            process = spawn(root, args)
            if on_spawn is not None:
                on_spawn(process)
            STARTUP_STATES[url] = ("Starting", "")
            notify("Starting ComfyUI…")
            for _ in range(180):
                await asyncio.sleep(1)
                if process.poll() is not None:
                    raise RuntimeError(
                        "ComfyUI exited during startup. See the ComfyUI startup log."
                    )
                if not is_external_services_allowed(settings):
                    return
                if await server_ready(session, url):
                    confirm(get_registry(), url)
                    STARTUP_STATES.pop(url, None)
                    notify("ComfyUI is ready.")
                    return
            raise TimeoutError("ComfyUI startup timed out. See the ComfyUI startup log.")
    except Exception as exc:
        STARTUP_STATES[url] = ("Startup failed", str(exc) or type(exc).__name__)
        logger.exception("ComfyUI automatic startup failed")
        notify("ComfyUI startup failed: %s", str(exc) or type(exc).__name__)


def schedule(app: Any) -> None:
    settings = getattr(app, "settings", None)
    if settings is None or not settings.get(AUTO_START_KEY, False):
        return

    def notify(source: str, detail: str | None = None) -> None:
        text = translate_with_fallback("NativeComfyUI", source)
        if detail is not None:
            text %= translate_with_fallback("NativeComfyUI", detail)
        logger.info("%s", text)
        if app.main_window:
            app.main_window.statusBar().showMessage(text, 15000)

    def own_process(process: subprocess.Popen) -> None:
        app._comfyui_owned_process = process

    app._comfyui_startup_task = asyncio.create_task(start(settings, notify, own_process))


def stop_process(process: subprocess.Popen) -> None:
    """Stop only our retained child handle; never discover processes by port or name."""
    if process.poll() is not None:
        return
    try:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        logger.info("Stopped ComfyUI started by this LoomStroke instance")
    except (OSError, subprocess.TimeoutExpired):
        logger.exception("Could not stop ComfyUI started by this LoomStroke instance")


async def cancel_watch(app: Any) -> None:
    task = getattr(app, "_comfyui_startup_task", None)
    if task is not None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    stop_owned(app)


def stop_owned(app: Any) -> None:
    """Bounded synchronous teardown also works after the Qt loop has stopped."""
    settings = getattr(app, "settings", None)
    process = getattr(app, "_comfyui_owned_process", None)
    if process is not None and settings is not None and settings.get(STOP_ON_EXIT_KEY, False):
        stop_process(process)
