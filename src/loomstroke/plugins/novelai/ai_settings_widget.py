"""NovelAI sampling controls and advanced image settings."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QRegularExpression, Qt
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)


class AISettingsWidget(QGroupBox):
    """Show NovelAI's basic sliders, seed/sampler, and advanced controls."""

    SAMPLERS = (
        ("Euler", "k_euler"),
        ("DPM++ 2S Ancestral", "k_dpmpp_2s_ancestral"),
        ("DPM++ 2M SDE", "k_dpmpp_2m_sde"),
        ("DPM++ 2M", "k_dpmpp_2m"),
        ("DPM++ SDE", "k_dpmpp_sde"),
        ("Euler Ancestral", "k_euler_ancestral"),
    )
    NOISE_SCHEDULES = ("karras", "exponential", "polyexponential")

    def __init__(  # noqa: PLR0915
        self, translate: Callable[[str], str], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self._tr = translate
        self.setObjectName("novelaiAISettings")
        layout = QVBoxLayout(self)
        reset_row = QHBoxLayout()
        reset_row.addStretch()
        self.reset_button = QPushButton("\u21ba", self)
        self.reset_button.setFixedWidth(28)
        self.reset_button.clicked.connect(self.reset)
        reset_row.addWidget(self.reset_button)
        self.collapse_button = QPushButton("\u25be", self)
        self.collapse_button.setFixedWidth(28)
        self.collapse_button.setCheckable(True)
        self.collapse_button.setChecked(True)
        reset_row.addWidget(self.collapse_button)
        layout.addLayout(reset_row)
        self.settings_content = QWidget(self)
        layout.addWidget(self.settings_content)
        self.collapse_button.toggled.connect(self.settings_content.setVisible)
        layout = QVBoxLayout(self.settings_content)
        layout.setContentsMargins(0, 0, 0, 0)

        self.steps_label = QLabel(self)
        layout.addWidget(self.steps_label)
        self.steps = QSpinBox(self)
        self.steps.setRange(1, 50)
        self.steps.setValue(20)
        self.steps_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.steps_slider.setRange(1, 50)
        self.steps_slider.setValue(20)
        self.steps.valueChanged.connect(self.steps_slider.setValue)
        self.steps_slider.valueChanged.connect(self.steps.setValue)
        steps_row = QHBoxLayout()
        steps_row.addWidget(self.steps)
        steps_row.addWidget(self.steps_slider, 1)
        layout.addLayout(steps_row)

        self.scale_label = QLabel(self)
        layout.addWidget(self.scale_label)
        self.scale = QDoubleSpinBox(self)
        self.scale.setRange(0, 30)
        self.scale.setSingleStep(0.5)
        self.scale.setValue(5.0)
        self.scale_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.scale_slider.setRange(0, 300)
        self.scale_slider.setValue(50)
        self.scale.valueChanged.connect(lambda value: self.scale_slider.setValue(round(value * 10)))
        self.scale_slider.valueChanged.connect(lambda value: self.scale.setValue(value / 10))
        scale_row = QHBoxLayout()
        scale_row.addWidget(self.scale)
        scale_row.addWidget(self.scale_slider, 1)
        layout.addLayout(scale_row)

        seed_row = QHBoxLayout()
        self.seed_label = QLabel(self)
        layout.addWidget(self.seed_label)
        self.seed = QLineEdit(self)
        self.seed.setValidator(QRegularExpressionValidator(QRegularExpression(r"\d{0,10}"), self))
        seed_row.addWidget(self.seed, 1)
        self.random_seed_button = QPushButton(self)
        seed_row.addWidget(self.random_seed_button)
        self.reuse_seed_button = QPushButton(self)
        self.reuse_seed_button.setEnabled(False)
        seed_row.addWidget(self.reuse_seed_button)
        layout.addLayout(seed_row)

        self.advanced_button = QPushButton(self)
        self.advanced_button.setCheckable(True)
        layout.addWidget(self.advanced_button)
        self.advanced = QWidget(self)
        advanced_layout = QVBoxLayout(self.advanced)
        advanced_layout.setContentsMargins(0, 0, 0, 0)
        self.sampler_label = QLabel(self.advanced)
        advanced_layout.addWidget(self.sampler_label)
        self.sampler = QComboBox(self.advanced)
        for label, value in self.SAMPLERS:
            self.sampler.addItem(label, value)
        self.sampler.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.sampler.setCurrentIndex(self.sampler.findData("k_euler_ancestral"))
        advanced_layout.addWidget(self.sampler)
        self.variety = QPushButton(self.advanced)
        self.variety.setCheckable(True)
        self.variety.setEnabled(False)
        advanced_layout.addWidget(self.variety)
        self.cfg_rescale_label = QLabel(self.advanced)
        advanced_layout.addWidget(self.cfg_rescale_label)
        self.cfg_rescale = QDoubleSpinBox(self.advanced)
        self.cfg_rescale.setRange(0, 1)
        self.cfg_rescale.setSingleStep(0.05)
        self.cfg_rescale_slider = QSlider(Qt.Orientation.Horizontal, self.advanced)
        self.cfg_rescale_slider.setRange(0, 100)
        self.cfg_rescale.valueChanged.connect(
            lambda value: self.cfg_rescale_slider.setValue(round(value * 100))
        )
        self.cfg_rescale_slider.valueChanged.connect(
            lambda value: self.cfg_rescale.setValue(value / 100)
        )
        rescale_row = QHBoxLayout()
        rescale_row.addWidget(self.cfg_rescale)
        rescale_row.addWidget(self.cfg_rescale_slider, 1)
        advanced_layout.addLayout(rescale_row)
        self.noise_schedule_label = QLabel(self.advanced)
        advanced_layout.addWidget(self.noise_schedule_label)
        self.noise_schedule = QComboBox(self.advanced)
        for value in self.NOISE_SCHEDULES:
            self.noise_schedule.addItem(value, value)
        advanced_layout.addWidget(self.noise_schedule)
        self.advanced.setVisible(False)
        self.advanced_button.toggled.connect(self.advanced.setVisible)
        layout.addWidget(self.advanced)
        self.advanced_button.setChecked(False)
        self.retranslate()

    def seed_value(self) -> int | None:
        raw = self.seed.text().strip()
        if not raw:
            return None
        value = int(raw)
        if value > 4_294_967_295:
            raise ValueError(self._tr("Seed must be between 0 and 4294967295"))
        return value

    def reset(self) -> None:
        self.steps.setValue(20)
        self.scale.setValue(5.0)
        self.seed.clear()
        self.sampler.setCurrentIndex(self.sampler.findData("k_euler_ancestral"))
        self.cfg_rescale.setValue(0)
        self.noise_schedule.setCurrentIndex(self.noise_schedule.findData("karras"))
        self.variety.setChecked(False)

    def set_variety_available(self, available: bool) -> None:
        self.variety.setEnabled(available)

    def retranslate(self) -> None:
        self.setTitle(self._tr("AI Settings"))
        self.reset_button.setToolTip(self._tr("Reset"))
        self.reset_button.setAccessibleName(self._tr("Reset"))
        self.collapse_button.setToolTip(self._tr("Collapse AI Settings"))
        self.collapse_button.setAccessibleName(self._tr("Collapse AI Settings"))
        self.steps_label.setText(self._tr("Steps"))
        self.scale_label.setText(self._tr("Prompt Guidance"))
        self.variety.setText(self._tr("Variety"))
        self.seed_label.setText(self._tr("Seed"))
        self.seed.setPlaceholderText(self._tr("Random"))
        self.random_seed_button.setText(self._tr("Randomize"))
        self.reuse_seed_button.setText(self._tr("Reuse last seed"))
        self.sampler_label.setText(self._tr("Sampler"))
        self.advanced_button.setText(self._tr("Advanced Settings"))
        self.cfg_rescale_label.setText(self._tr("Prompt Guidance Rescale"))
        self.noise_schedule_label.setText(self._tr("Noise Schedule"))
        for index, (label, _value) in enumerate(self.SAMPLERS):
            self.sampler.setItemText(index, self._tr(label))
        for index, value in enumerate(self.NOISE_SCHEDULES):
            self.noise_schedule.setItemText(index, self._tr(value))
