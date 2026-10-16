"""Explicit user-default import/export for document speaker styles."""

import json

from PySide6.QtCore import QIODevice, QSaveFile

from loomstroke.core.japanese_text.speaker_styles import validate_registry
from loomstroke.platform.user_dirs import plugin_data_dir


def defaults_path():
    return plugin_data_dir("comic_tools") / "speaker_styles" / "defaults.json"


def export_defaults(registry, path=None):
    validate_registry(registry)
    path = path or defaults_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(registry, ensure_ascii=False, allow_nan=False, indent=2).encode("utf-8")
    output = QSaveFile(str(path))
    if not output.open(QIODevice.OpenModeFlag.WriteOnly):
        raise OSError(output.errorString())
    if output.write(data) != len(data) or not output.commit():
        output.cancelWriting()
        raise OSError(output.errorString())


def import_defaults(path=None):
    path = path or defaults_path()
    if path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("The speaker style library is too large.")
    registry = json.loads(path.read_text(encoding="utf-8"))
    validate_registry(registry)
    return registry
