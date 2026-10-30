"""Source-preserving bindings for small widgets without a retranslation owner."""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

from PySide6.QtCore import QEvent, QObject, QSignalBlocker
from PySide6.QtWidgets import QComboBox, QWidget

from .translations import TranslationBinder, translate_with_fallback

WidgetT = TypeVar("WidgetT", bound=QWidget)


class _WidgetTranslations(QObject):
    def __init__(self, widget: QWidget) -> None:
        super().__init__(widget)
        self.bindings: dict[str, tuple[TranslationBinder, Callable[[], str], list[str]]] = {}
        widget.installEventFilter(self)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.LanguageChange:
            for attribute, (binder, getter, last) in tuple(self.bindings.items()):
                if getter() == last[0]:
                    binder.retranslate()
                else:
                    # Another owner replaced this property with a new message.
                    del self.bindings[attribute]
        return super().eventFilter(watched, event)


def bind_widget_text(
    widget: WidgetT,
    source: str,
    *,
    domain: str,
    attribute: str = "text",
    **values: object,
) -> WidgetT:
    """Bind one display property, replacing any earlier source for that property.

    Values are formatted after translation and are never used as translation keys.
    The widget owns the event filter, so destroying it releases the bindings.
    """
    owner = getattr(widget, "_literal_translations", None)
    if owner is None:
        owner = _WidgetTranslations(widget)
        widget._literal_translations = owner
    setter = getattr(widget, "set" + attribute[0].upper() + attribute[1:])
    getter = getattr(widget, attribute)
    last = [""]

    def apply(text: str) -> None:
        last[0] = text.format(**values) if values else text
        setter(last[0])

    binder = TranslationBinder(lambda text: translate_with_fallback(domain, text))
    binder.register(apply, source)
    owner.bindings[attribute] = (binder, getter, last)
    return widget


def bind_combo_texts(combo: QComboBox, sources: list[str], *, domain: str) -> None:
    """Translate existing items while preserving their data and suppressing edit signals."""
    owner = getattr(combo, "_literal_translations", None)
    if owner is None:
        owner = _WidgetTranslations(combo)
        combo._literal_translations = owner

    def getter() -> str:
        return "\0".join(combo.itemText(index) for index in range(len(sources)))

    last = [getter()]
    binder = TranslationBinder(lambda text: translate_with_fallback(domain, text))

    def apply(index: int, text: str) -> None:
        with QSignalBlocker(combo):
            combo.setItemText(index, text)
        last[0] = getter()

    for index, source in enumerate(sources):
        binder.register(lambda text, index=index: apply(index, text), source)
    owner.bindings["items"] = (binder, getter, last)
