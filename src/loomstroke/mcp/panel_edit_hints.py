"""Expose explicit allocation choices when a panel split/template is ambiguous."""

from loomstroke.plugins.comic_tools import layout_tree as lt
from loomstroke.plugins.comic_tools.panel_split_plan import split_preview
from loomstroke.plugins.comic_tools.panel_template_plan import PanelTemplatePreview
from loomstroke.plugins.comic_tools.service import ComicPageController


def allocation_hints(canvas, operation):
    if operation["action"] not in {"split", "template"}:
        return {}
    size = canvas.get_canvas_size()
    state = ComicPageController().read_state(canvas, page_size=size)
    if operation["action"] == "split":
        preview = split_preview(
            state,
            operation["panelId"],
            operation["orientation"],
            page_size=size,
            ratio=operation.get("ratio", 0.5),
            count=operation.get("count", 2),
        )
    else:
        preview = PanelTemplatePreview(
            state, lt.layout_from_payload(operation["layout"]), page_size=size
        )
    for item_id, target in operation.get("allocations", {}).items():
        preview.assign(item_id, target)
    return {
        "targetPanels": [
            {"panelId": child.panel_id, "bounds": list(child.rect)} for child in preview.children
        ],
        "contentAllocations": [
            {
                "layerId": item.item_id,
                "proposedPanelId": item.panel_id,
                "reason": item.reason,
                "unresolved": item.unresolved,
            }
            for item in preview.allocations
        ],
        "resolution": "Resubmit allocations mapping layerId to a target panelId "
        "for unresolved items. "
        "Split imagePolicies can explicitly select move, copy, or shared.",
    }
