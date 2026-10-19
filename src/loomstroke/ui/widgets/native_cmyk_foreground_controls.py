"""Explicit ink input, with a separate switch for RGB colour-picker conversion."""

from PySide6.QtWidgets import QCheckBox, QDoubleSpinBox, QGridLayout, QLabel, QWidget

from loomstroke.core.native_cmyk.color_descriptor import from_ink_percentages, without_inks
from loomstroke.ui.translations import TranslationBinder, translate_with_fallback


class NativeCmykForegroundControls(QWidget):
    def __init__(self, service_provider, parent=None, *, host_provider=None):
        super().__init__(parent)
        self._provider = service_provider
        self._host_provider = host_provider
        self._refreshing = False
        self._translations = TranslationBinder(
            lambda text: translate_with_fallback("NativeCmyk", text)
        )
        layout = QGridLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.enabled = QCheckBox(self)
        self._translations.register(self.enabled.setText, "Use CMYK ink values")
        layout.addWidget(self.enabled, 0, 0, 1, 4)
        self.values = []
        for index, channel in enumerate("CMYK"):
            spin = QDoubleSpinBox(self)
            spin.setRange(0, 100)
            spin.setDecimals(2)
            spin.setSuffix(" %")
            spin.setValue(100 if channel == "K" else 0)
            spin.valueChanged.connect(self._changed)
            layout.addWidget(QLabel(channel, self), 1 + index // 2, 2 * (index % 2))
            layout.addWidget(spin, 1 + index // 2, 2 * (index % 2) + 1)
            self.values.append(spin)
        self.enabled.toggled.connect(self._changed)
        self.refresh()

    def _service(self):
        channel_service = self._provider()
        return channel_service._canvas.native_cmyk() if channel_service is not None else None

    def _changed(self, *_args):
        if self._refreshing:
            return
        service = self._service()
        if service is not None:
            if not self.enabled.isChecked() and self.sender() is not self.enabled:
                self.enabled.setChecked(True)
            percentages = (
                tuple(spin.value() for spin in self.values) if self.enabled.isChecked() else None
            )
            host = self._host_provider() if self._host_provider is not None else None
            setter = getattr(host, "set_active_color", None)
            if callable(setter):
                current = host.get_active_color()
                color = (
                    from_ink_percentages(percentages, service.profile(), current)
                    if percentages is not None
                    else without_inks(current)
                )
                setter(color, source="channels:foreground")
            else:
                service.set_foreground(percentages)

    def refresh(self):
        service = self._service()
        self.setVisible(service is not None and service.profile() is not None)
        self._translations.retranslate()
        self._refreshing = True
        try:
            percentages = service.foreground_percentages if service is not None else None
            self.enabled.setChecked(percentages is not None)
            if percentages is not None:
                for spin, value in zip(self.values, percentages, strict=True):
                    spin.setValue(value)
        finally:
            self._refreshing = False
