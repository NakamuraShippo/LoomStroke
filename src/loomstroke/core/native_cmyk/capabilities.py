"""Native colour-mode limits shared by editing, conversion, and presentation."""

from types import SimpleNamespace

from loomstroke.core.command_types import BlendMode
from loomstroke.core.layer_transform_metadata import read_layer_transform


class NativeCmykCapabilities:
    BLEND_MODES = frozenset(mode.value for mode in BlendMode) - {
        "hue",
        "saturation",
        "color",
        "luminosity",
    }

    @classmethod
    def validate_snapshot(cls, snapshot):
        cls.validate_layer(
            SimpleNamespace(
                layer_type=snapshot.layer_type,
                blend_mode=snapshot.blend_mode,
                transform=read_layer_transform(snapshot.metadata, snapshot.position),
                filter_type=snapshot.filter_type,
            )
        )

    @classmethod
    def validate_layer(cls, layer):
        mode = getattr(layer.blend_mode, "value", layer.blend_mode)
        transform = layer.transform
        if (
            layer.layer_type not in ("raster", "mask", "group")
            or mode not in cls.BLEND_MODES
            or transform.scale_x != 1
            or transform.scale_y != 1
            or transform.rotation != 0
            or transform.tx != 0
            or transform.ty != 0
            or getattr(layer, "effect_type", None) is not None
            or getattr(layer, "filter_type", None) is not None
        ):
            raise ValueError(
                "Native CMYK requires raster layers with supported blends "
                "and no live filters or transforms"
            )
