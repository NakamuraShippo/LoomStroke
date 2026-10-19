"""Device-local style pipelines shared by document and filter-prefix projections."""

from threading import RLock
from typing import Any

from .layer_style_kernel import layer_style_kernel


def style_format(op: int, precise_relief: bool) -> str:
    if precise_relief and op in {6, 10}:
        return "r32float"
    if precise_relief and op in {0, 7, 8}:
        return "rgba32float"
    return "r16float" if op in {1, 2, 3, 6, 10} else "rgba16float"


class StylePipelineStore:
    def __init__(self, bundle: Any) -> None:
        self.bundle = bundle
        self.pipelines: dict[tuple[str, int, bool], Any] = {}
        self._layout: Any = None
        self._lock = RLock()

    def compile(self, key: tuple[str, int, bool]) -> Any:
        # Display only calls this after preflight. Explicit synchronous export
        # can share the device and must not race a background compilation.
        pipeline = self.pipelines.get(key)
        if pipeline is not None:
            return pipeline
        with self._lock:
            return self._compile(key)

    def _compile(self, key: tuple[str, int, bool]) -> Any:
        pipeline = self.pipelines.get(key)
        if pipeline is not None:
            return pipeline
        format, op, document = key
        device = self.bundle.device
        module = device.create_shader_module(code=layer_style_kernel(op, document))
        if self._layout is None:
            self._layout = device.create_bind_group_layout(
                entries=[
                    {
                        "binding": i,
                        "visibility": self.bundle.wgpu.ShaderStage.FRAGMENT,
                        **(
                            {"buffer": {"type": "uniform"}}
                            if i == 11
                            else {
                                "texture": {
                                    "sample_type": "unfilterable-float",
                                    "view_dimension": "2d",
                                }
                            }
                        ),
                    }
                    for i in range(15)
                ]
            )
        pipeline = device.create_render_pipeline(
            layout=device.create_pipeline_layout(bind_group_layouts=[self._layout]),
            vertex={"module": module, "entry_point": "vs_main"},
            fragment={"module": module, "entry_point": "fs_main", "targets": [{"format": format}]},
            primitive={"topology": "triangle-list"},
        )
        self.pipelines[key] = pipeline
        return pipeline
