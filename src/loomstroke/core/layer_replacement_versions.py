"""Keep texture generations distinct when page snapshots reuse layer identifiers."""


def _walk(layers):
    for layer in layers:
        yield layer
        yield from _walk(getattr(layer, "children", None) or [])


def advance_replacement_versions(previous, incoming):
    versions = {
        str(layer.id): int(getattr(layer, "texture_version", 0)) for layer in _walk(previous)
    }
    for layer in _walk(incoming):
        layer.texture_version = (
            max(int(getattr(layer, "texture_version", 0)), versions.get(str(layer.id), 0)) + 1
        )
