"""Separator choice for the Prompt Editor live prompt: New line / , / , + space / Custom."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtWidgets import QButtonGroup, QHBoxLayout, QLabel, QLineEdit, QToolButton, QWidget

# Raw separator text as stored in the field ("\\n" is the escaped new line).
_PRESETS = (("newline", "\\n"), ("comma", ","), ("comma_space", ", "))
_BUTTON_STYLE = (
    "QToolButton { border: 0; border-radius: 4px; padding: 2px 8px; }"
    "QToolButton:checked { background: #8b5cf6; color: #ffffff; }"
)


def preset_for(raw: str) -> str:
    """Preset key for a stored separator; an empty field means a new line."""
    if raw == "":
        return "newline"
    for key, value in _PRESETS:
        if raw == value:
            return key
    return "custom"


class SeparatorSelector(QWidget):
    """Segmented buttons over the existing separator field, which stays the source of truth.

    Preset buttons write their raw text into ``edit``; Custom keeps whatever the
    user types there and shows the field. The field's own signals still drive
    the panel's preview and dirty state.
    """

    def __init__(
        self,
        parent: QWidget,
        edit: QLineEdit,
        *,
        texts: dict[str, str],
        register: Callable[[Callable[[str], None], str], None],
    ) -> None:
        super().__init__(parent)
        self._edit = edit
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        label = QLabel(self)
        register(label.setText, texts["separator"])
        layout.addWidget(label)

        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self.buttons: dict[str, QToolButton] = {}
        # U+2423 shows the space of ", " so the two comma buttons differ.
        captions = {"newline": None, "comma": ",", "comma_space": ",\u2423", "custom": None}
        for key in ("newline", "comma", "comma_space", "custom"):
            button = QToolButton(self)
            button.setCheckable(True)
            button.setStyleSheet(_BUTTON_STYLE)
            if captions[key] is None:
                register(button.setText, texts[key])
            else:
                button.setText(captions[key])
                register(button.setToolTip, texts[key])
            register(button.setAccessibleName, texts[key])
            button.clicked.connect(lambda _checked=False, k=key: self._choose(k))
            self._group.addButton(button)
            self.buttons[key] = button
            layout.addWidget(button)

        edit.setParent(self)
        layout.addWidget(edit)
        edit.textChanged.connect(self._sync)
        self._custom = False
        self._sync(edit.text())

    def refresh(self) -> None:
        """Re-read the field after the panel set it with signals blocked (load / new)."""
        self._custom = False
        self._sync(self._edit.text())

    def _choose(self, key: str) -> None:
        self._custom = key == "custom"
        if not self._custom:
            self._edit.setText(dict(_PRESETS)[key])
        self._sync(self._edit.text())
        if self._custom:
            self._edit.setFocus()
            self._edit.selectAll()

    def _sync(self, raw: str) -> None:
        key = "custom" if self._custom else preset_for(raw)
        if key == "custom":
            self._custom = True
        self.buttons[key].setChecked(True)
        self._edit.setVisible(key == "custom")
