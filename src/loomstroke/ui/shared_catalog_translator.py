"""Compiled shared text for native Qt calls made by inherited or plugin widgets."""

from __future__ import annotations

from contextvars import ContextVar

from PySide6.QtCore import QCoreApplication, QTranslator

_SHARED_LOOKUP = ContextVar("loomstroke_shared_translation_lookup", default=True)


def translate_exact_context(context: str, source: str) -> str:
    """Let runtime helpers try their domain fallback before the shared catalog."""
    token = _SHARED_LOOKUP.set(False)
    try:
        return QCoreApplication.translate(context, source)
    finally:
        _SHARED_LOOKUP.reset(token)


class SharedCatalogTranslator(QTranslator):
    """Keep exact Qt contexts authoritative, then try the common source catalog."""

    # PySide's stub omits the null QString accepted by this Qt virtual method.
    def translate(  # type: ignore[override]
        self, context: str, sourceText: str, disambiguation: str | None = None, n: int = -1
    ) -> str | None:
        text = super().translate(context, sourceText, disambiguation, n)
        if text:
            return text
        if context != "__shared__" and _SHARED_LOOKUP.get():
            text = super().translate("__shared__", sourceText, None, n)
            if text:
                return text
        # A null QString lets Qt consult the next translator or retain the source.
        # Returning an empty Python string would instead blank the UI text.
        return None
