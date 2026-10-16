"""Text-only compatibility ports for document vector replacement."""

from .document_vector_objects import replace_vector_objects, vector_entries


def text_entries(pages, *, page_ids=None):
    return {
        key: value
        for key, value in vector_entries(pages, page_ids=page_ids).items()
        if isinstance((getattr(value[2], "meta", None) or {}).get("text_tool"), dict)
    }


def replace_text_objects(pages, replacements, *, allow_locked=False):
    return replace_vector_objects(pages, replacements, allow_locked=allow_locked)
