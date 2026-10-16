"""Bounded immutable blend bindings for repeated source/target configurations."""

from collections import OrderedDict


class BlendBindingCache:
    def __init__(self, *, limit=512):
        self.limit = limit
        self.entries = OrderedDict()

    def get_or_create(self, device, usage, layout, sampler, views, data):
        # Keep views alive with each entry, so their identities cannot be reused
        # for different GPU resources while the key remains in the cache.
        key = tuple(id(view) for view in views), data
        existing = self.entries.get(key)
        if existing is not None:
            self.entries.move_to_end(key)
            return existing[2]
        uniform = device.create_buffer_with_data(data=data, usage=usage)
        try:
            group = device.create_bind_group(
                layout=layout,
                entries=[
                    {"binding": 0, "resource": sampler},
                    {"binding": 1, "resource": views[0]},
                    {"binding": 2, "resource": views[1]},
                    {"binding": 3, "resource": {"buffer": uniform, "offset": 0, "size": len(data)}},
                    {"binding": 4, "resource": views[2]},
                ],
            )
        except Exception:
            uniform.destroy()
            raise
        self.entries[key] = views, uniform, group
        while len(self.entries) > self.limit:
            # Drop cache ownership without explicitly destroying buffers that
            # may already be referenced by this frame's unsubmitted encoder.
            # WGPU bindings/encoders retain their own native resource references.
            self.entries.popitem(last=False)
        return group

    def release(self):
        for _views, uniform, _group in self.entries.values():
            uniform.destroy()
        self.entries.clear()
