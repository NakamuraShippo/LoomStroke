"""User macro library: ``<user root>/actions/<macro_id>.json`` (CT-20 §10)."""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import ClassVar

from .macro_definition import MacroDefinition
from .macro_format_error import MacroFormatError

logger = logging.getLogger(__name__)

__all__ = ["MacroStore"]


class MacroStore:
    DIR_NAME: ClassVar[str] = "actions"
    MAX_FILE_BYTES: ClassVar[int] = 2 * 1024 * 1024
    _UNSAFE: ClassVar[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9_.-]+")

    def __init__(self, user_root: Path) -> None:
        self._root = Path(user_root) / self.DIR_NAME

    @property
    def root(self) -> Path:
        return self._root

    def path_for(self, macro_id: str) -> Path:
        safe = self._UNSAFE.sub("_", macro_id).strip("._") or "macro"
        return self._root / f"{safe[:80]}.json"

    def save(self, definition: MacroDefinition) -> Path:
        self._root.mkdir(parents=True, exist_ok=True)
        path = self.path_for(definition.macro_id)
        payload = json.dumps(definition.to_dict(), ensure_ascii=False, indent=2, sort_keys=True)
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(payload + "\n", encoding="utf-8")
        temporary.replace(path)
        return path

    def load(self, macro_id: str) -> MacroDefinition | None:
        path = self.path_for(macro_id)
        return self.read(path) if path.is_file() else None

    def delete(self, macro_id: str) -> bool:
        path = self.path_for(macro_id)
        if not path.is_file():
            return False
        path.unlink()
        return True

    def list_definitions(self) -> tuple[MacroDefinition, ...]:
        if not self._root.is_dir():
            return ()
        found: list[MacroDefinition] = []
        for path in sorted(self._root.glob("*.json")):
            definition = self.read(path)
            if definition is not None:
                found.append(definition)
        return tuple(sorted(found, key=lambda d: (d.name.lower(), d.macro_id)))

    def read(self, path: Path) -> MacroDefinition | None:
        """Strictly parse one JSON file; malformed / forbidden payloads yield ``None``."""
        try:
            if path.stat().st_size > self.MAX_FILE_BYTES:
                logger.warning("macro file too large: %s", path.name)
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            return MacroDefinition.from_dict(data)
        except (OSError, ValueError, MacroFormatError) as error:
            logger.warning("macro file rejected: %s (%s)", path.name, error)
            return None

    def export_json(self, definition: MacroDefinition, destination: Path) -> Path:
        destination = Path(destination)
        if destination.exists():
            raise FileExistsError(str(destination.name))
        destination.write_text(
            json.dumps(definition.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return destination

    def import_json(self, source: Path) -> MacroDefinition | None:
        definition = self.read(Path(source))
        if definition is None:
            return None
        self.save(definition)
        return definition
