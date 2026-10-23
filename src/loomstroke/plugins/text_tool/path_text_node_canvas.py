"""A private vector-edit view; only converted text replacements reach the host."""

from loomstroke.plugins.vector_tools.common import VectorReplaceCommand

from .path_text_node_geometry import node_object, replace_nodes


class PathTextNodeCanvas:
    def __init__(self, owner, hit):
        self.owner = owner
        self.object_id, self.layer_id = hit.obj.id, hit.layer_id
        self._vector_preview = None
        self._vector_preview_layer_id = None
        self._vector_selection = {
            "id": self.object_id,
            "layer_id": self.layer_id,
            "path": 0,
            "anchor": 0,
        }

    def hit(self):
        return self.owner.controller.tool._find_text_object_by_id(
            self.object_id, layer_id=self.layer_id
        )

    def get_vector_objects(self):
        hit = self.hit()
        return [(self.layer_id, node_object(hit.obj))] if hit is not None else []

    def notify_vector_preview_changed(self):
        hit = self.hit()
        tool = self.owner.controller.tool
        if hit is None or not tool._edit_lifecycle.can_edit_layer(self.layer_id):
            self.clear_preview()
            return
        updated = (
            replace_nodes(hit.obj, self._vector_preview)
            if self._vector_preview is not None
            else None
        )
        canvas = tool.host_api.canvas
        if updated is None:
            self.clear_preview()
        else:
            canvas._vector_preview = updated
            canvas._vector_preview_layer_id = self.layer_id
            canvas.notify_vector_preview_changed()

    def clear_preview(self):
        canvas = self.owner.controller.tool.host_api.canvas
        if getattr(getattr(canvas, "_vector_preview", None), "id", None) == self.object_id:
            canvas._vector_preview = canvas._vector_preview_layer_id = None
            canvas.notify_vector_preview_changed()

    def submit(self, command, *, merge_key=None):
        del merge_key
        tool = self.owner.controller.tool
        hit = self.hit()
        if (
            hit is None
            or not isinstance(command, VectorReplaceCommand)
            or command.target_id != self.object_id
            or not tool._edit_lifecycle.can_edit_layer(self.layer_id)
        ):
            return False
        updated = replace_nodes(hit.obj, command.new_object)
        if updated is None:
            return False
        if updated != hit.obj:
            return bool(
                tool.host_api.canvas.submit(
                    VectorReplaceCommand(self.object_id, updated, layer_id=self.layer_id),
                    merge_key=None,
                )
            )
        return True
