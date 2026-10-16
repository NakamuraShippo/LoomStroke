"""Bounded previous regional uploads let a cancelled preview reuse its coverage."""

from collections import OrderedDict


class MaskVersionCache:
    def __init__(self, budget_bytes=128 * 1024 * 1024):
        self.budget_bytes = budget_bytes
        self.entries = OrderedDict()
        self.bytes = 0

    @staticmethod
    def key(handle, region):
        return (
            handle.layer_id,
            handle.texture_version,
            region,
            handle.source_width,
            handle.source_height,
        )

    def take(self, key):
        handle = self.entries.pop(key, None)
        if handle is not None:
            self.bytes -= handle.width * handle.height
        return handle

    def remember(self, handle, region):
        key = self.key(handle, region)
        previous = self.take(key)
        if previous is not None and previous.texture is not handle.texture:
            previous.texture.destroy()
        self.entries[key] = handle
        self.bytes += handle.width * handle.height
        while self.bytes > self.budget_bytes or len(self.entries) > 128:
            _, expired = self.entries.popitem(last=False)
            self.bytes -= expired.width * expired.height
            expired.texture.destroy()

    def remove_stale(self, active_ids):
        for key in list(self.entries):
            if key[0] not in active_ids:
                self.take(key).texture.destroy()
