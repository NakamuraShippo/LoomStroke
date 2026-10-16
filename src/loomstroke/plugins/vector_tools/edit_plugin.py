from ..sdk.base import PluginMetadata, PluginType
from .edit_tool import VectorPathEditTool, _ts


class VectorPathEditPlugin:
    def __init__(self) -> None:
        self.metadata = PluginMetadata(
            id="vector_path_edit_tool",
            name=_ts("Vector Path Edit Tool"),
            version="0.1.0",
            description=_ts("Edit anchors/handles of vector paths"),
            author="LoomStroke Team",
            types=[PluginType.TOOL],
        )
        self.tool = VectorPathEditTool()

    def __getattr__(self, name: str):
        return getattr(self.tool, name)

    def activate(self, host_api) -> None:
        self.tool.activate(host_api)

    def deactivate(self) -> None:
        self.tool.deactivate()
