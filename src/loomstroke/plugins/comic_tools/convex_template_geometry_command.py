"""Complete topology creation inside the same atomic template transaction."""

from loomstroke.core.command_types import Command

from .panel_convex_plan import convex_layout_command
from .panel_template_geometry_state import template_geometry_state


class ConvexTemplateGeometryCommand(Command):
    vector_object = None
    _layer_id = "__loomstroke_comic_tools__"

    def __init__(self, source, layout, page_size):
        super().__init__()
        self.source, self.layout, self.page_size = source, layout, page_size
        self.command = None
        self.last_error = ""

    def apply(self, canvas):
        if self.command is None:
            from .service import ComicPageController  # noqa: PLC0415

            state = ComicPageController(page_key="template-geometry").read_state(
                canvas, page_size=self.page_size
            )
            self.command = convex_layout_command(
                template_geometry_state(state, self.source, self.layout, self.page_size),
                self.layout,
                page_size=self.page_size,
                label="Apply inclined template geometry",
            )
            self._allocate_sources(state)
        self._executed = self.command.apply(canvas)
        self.last_error = self.command.last_error
        return self._executed

    def prepare(self, state):
        """Compile from projected slots; the outer template guards the real source."""
        self.command = convex_layout_command(
            template_geometry_state(state, self.source, self.layout, self.page_size),
            self.layout,
            page_size=self.page_size,
            label="Apply inclined template geometry",
            guarded=False,
        )
        self._allocate_sources(state)

    def _allocate_sources(self, projected):
        from .panel_template_outer_sources import allocate_template_outer_sources  # noqa: PLC0415

        allocate_template_outer_sources(
            self.source, projected, self.layout, self.command, self.page_size
        )

    def revert(self, canvas):
        if not self._executed:
            return False
        result = self.command.revert(canvas)
        self._executed = not result
        return result
