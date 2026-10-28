"""Non-destructive measurement tool plugin."""

from .tool import MeasurementTool


def create_plugin() -> MeasurementTool:
    return MeasurementTool()
