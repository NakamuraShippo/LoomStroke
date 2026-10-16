"""Persistent authoring profiles, isolated by Preferences endpoint."""

import hashlib
from typing import Any

from PySide6.QtWebEngineCore import QWebEngineProfile

from loomstroke.platform.user_dirs import plugin_data_dir


def author_profile(url: str, parent: Any) -> QWebEngineProfile:
    identity = hashlib.sha256(url.rstrip("/").encode("utf-8")).hexdigest()
    root = plugin_data_dir("comfyui") / "author_browser" / identity
    profile = QWebEngineProfile("comfy-author-" + identity, parent)
    profile.setPersistentStoragePath(str(root / "storage"))
    profile.setCachePath(str(root / "cache"))
    return profile
