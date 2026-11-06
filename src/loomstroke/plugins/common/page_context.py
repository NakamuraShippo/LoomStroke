"""Page identity for asynchronous tool work, including small HostAPI test doubles."""


def page_context(host):
    pages = getattr(host, "pages", None)
    active = getattr(pages, "get_active_page_id", None)
    return (id(pages), active()) if callable(active) else None
