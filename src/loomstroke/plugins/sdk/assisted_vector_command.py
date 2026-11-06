"""One existing vector history entry for an original and its symmetry copies."""

from loomstroke.core.command_types import CompositeCommand

from ..vector_tools.common import VectorCreateCommand
from .assist_vector_objects import object_copies
from .drawing_assist import get_drawing_assist_controller


class AssistedVectorCreateCommand(VectorCreateCommand):
    def __init__(self, source, objects):
        first = VectorCreateCommand(
            objects[0],
            layer_id=source.layer_id,
            merge_key=source.merge_key,
            created_layer_id=source._created_layer_id,
        )
        target = source.layer_id or source._created_layer_id
        super().__init__(
            objects[0],
            layer_id=source.layer_id,
            merge_key=source.merge_key,
            created_layer_id=source._created_layer_id,
        )
        self._group = CompositeCommand(
            [first] + [VectorCreateCommand(obj, layer_id=target) for obj in objects[1:]]
        )

    def apply(self, canvas):
        ok = self._group.apply(canvas)
        primary = self._group.commands[0]
        self._insert_layer_id = primary._insert_layer_id
        self._insert_index = primary._insert_index
        self.layer_id = primary.layer_id
        return ok

    def revert(self, canvas):
        return self._group.revert(canvas)


def assisted_vector_command(command, session):
    if isinstance(command, AssistedVectorCreateCommand) or not isinstance(
        command, VectorCreateCommand
    ):
        return command
    objects = object_copies(command.vector_object, session)
    if len(objects) == 1 and objects[0] is command.vector_object:
        return command
    return AssistedVectorCreateCommand(command, objects)


def assisted_create_for_host(command, host):
    controller = get_drawing_assist_controller(host)
    begin = getattr(controller, "begin_session", None)
    if not callable(begin):
        return command
    session = begin("shape_manager")
    result = assisted_vector_command(command, session)
    controller.clear_stroke_feedback()
    return result
