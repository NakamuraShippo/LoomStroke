"""Settings transaction owner for TextTool property edits."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from loomstroke.plugins.sdk.tool import PropertySpec, PropertyType

from .common import TextToolSettings


class TextToolSettingsUI:
    """Own debounced property-edit transaction state for ``TextTool``."""

    PROP_DEBOUNCE_MS = 500

    def __init__(
        self,
        *,
        begin_transaction: Callable[[], None],
        end_transaction: Callable[[], None],
    ) -> None:
        self._begin_transaction = begin_transaction
        self._end_transaction = end_transaction
        self.prop_debounce_timer: Any | None = None
        self.prop_txn_active = False

    @staticmethod
    def build_property_specs(
        settings: TextToolSettings,
        *,
        forced_vertical: bool,
        translate: Callable[[str], Any],
    ) -> list[PropertySpec]:
        """Build stable TextTool metadata properties from the current settings."""
        properties = [
            PropertySpec(
                key="word_wrap",
                display_name=translate("Word Wrap"),
                type=PropertyType.BOOL,
                default_value=settings.word_wrap,
                is_common=True,
            ),
            PropertySpec(
                key="font_size_pt",
                display_name=translate("Font Size (pt)"),
                type=PropertyType.FLOAT,
                min_value=1.0,
                max_value=512.0,
                step=1.0,
                default_value=settings.font_size_pt,
                is_common=True,
            ),
        ]
        if forced_vertical:
            properties.append(
                PropertySpec(
                    key="vertical_flow",
                    display_name=translate("Paragraph Flow"),
                    type=PropertyType.ENUM,
                    enum_items=["left", "right"],
                    default_value=settings.vertical_flow,
                )
            )
            properties.append(
                PropertySpec(
                    key="rotate_half_width",
                    display_name=translate("Rotate Latin/Digits"),
                    type=PropertyType.BOOL,
                    default_value=settings.rotate_half_width,
                )
            )
        properties.extend(
            [
                PropertySpec(
                    key="line_height",
                    display_name=translate("Line Height"),
                    type=PropertyType.FLOAT,
                    min_value=0.5,
                    max_value=4.0,
                    step=0.1,
                    default_value=settings.line_height,
                ),
                PropertySpec(
                    key="tracking",
                    display_name=translate("Tracking"),
                    type=PropertyType.FLOAT,
                    min_value=-64.0,
                    max_value=128.0,
                    step=0.5,
                    default_value=settings.tracking,
                ),
                PropertySpec(
                    key="padding_top",
                    display_name=translate("Padding Top"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=256.0,
                    step=1.0,
                    default_value=settings.padding_top,
                ),
                PropertySpec(
                    key="padding_right",
                    display_name=translate("Padding Right"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=256.0,
                    step=1.0,
                    default_value=settings.padding_right,
                ),
                PropertySpec(
                    key="padding_bottom",
                    display_name=translate("Padding Bottom"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=256.0,
                    step=1.0,
                    default_value=settings.padding_bottom,
                ),
                PropertySpec(
                    key="padding_left",
                    display_name=translate("Padding Left"),
                    type=PropertyType.FLOAT,
                    min_value=0.0,
                    max_value=256.0,
                    step=1.0,
                    default_value=settings.padding_left,
                ),
            ]
        )
        return properties

    def ensure_prop_transaction(self) -> None:
        """Start or extend a debounced property-change transaction."""
        from PySide6.QtCore import QTimer  # noqa: PLC0415

        if not self.prop_txn_active:
            self._begin_transaction()
            self.prop_txn_active = True

        timer = self.prop_debounce_timer
        if timer is None:
            timer = QTimer()
            timer.setSingleShot(True)
            timer.timeout.connect(self.flush_prop_transaction)
            self.prop_debounce_timer = timer

        timer.start(self.PROP_DEBOUNCE_MS)

    def flush_prop_transaction(self) -> None:
        """End the debounced property-change transaction."""
        if self.prop_txn_active:
            self.prop_txn_active = False
            self._end_transaction()
