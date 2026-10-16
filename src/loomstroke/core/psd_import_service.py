"""PSD decoding and command publication without UI or save-format changes."""

from pathlib import Path

from loomstroke.io.psd_document_reader import PsdDocumentReader

from .psd_import_command import PsdImportCommand


class PsdImportService:
    def __init__(self, canvas, file_io):
        self._canvas, self._file_io = canvas, file_io

    def import_layers(self, path: Path) -> list[str] | None:
        document = PsdDocumentReader(self._file_io._validate_import_image).read(path)
        command = PsdImportCommand(document)
        return command.created_ids if self._canvas.submit(command) else None
