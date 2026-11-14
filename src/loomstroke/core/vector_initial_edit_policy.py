"""Use the existing vector/story ancestor-lock policy before an initial edit."""

from .japanese_text.linked_story import _locked


def allows_initial_vector_edit(canvas, command) -> bool:
    from ..plugins.vector_tools.common import (  # noqa: PLC0415
        VectorCreateCommand,
        VectorReplaceCommand,
    )
    from .commands_text_outline import ConvertTextToOutlinesCommand  # noqa: PLC0415

    if isinstance(command, ConvertTextToOutlinesCommand):
        command = command._inner
    # Comic and other composite commands also use the vector submit route,
    # but their layer marker is owned by their own target validation.
    if not isinstance(command, (VectorCreateCommand, VectorReplaceCommand)):
        return True
    layer_id = getattr(command, "layer_id", None) or getattr(command, "_layer_id", None)
    if layer_id is not None:
        layer = canvas._get_layer_by_id(layer_id)
        if layer is None:
            return False
    elif getattr(command, "target_id", None) is not None:
        layer = next(
            (
                candidate
                for candidate in canvas._flatten_layers()
                if any(obj.id == command.target_id for obj in candidate.vector_objects)
            ),
            None,
        )
    else:
        layer = canvas._get_active_layer()
    # A raster target creates a separate vector layer. Its pixel lock does
    # not prohibit adding that new layer.
    return layer is None or layer.layer_type != "vector" or not _locked(canvas, layer)
