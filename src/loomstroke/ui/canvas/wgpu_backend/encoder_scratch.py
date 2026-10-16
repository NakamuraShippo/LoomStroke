"""Lease completed group scratch after its last read in an ordered encoder."""


class EncoderScratchTargets:
    """Only compositor allocations participate; cached/foreign targets stay owned."""

    def __init__(self):
        self._known = {}
        self._free = {}

    def register(self, target):
        self._known[id(target)] = target
        return target

    def retire(self, targets, *, keep=()):
        protected = {id(target) for target in keep if target is not None}
        for identity in protected:
            self._free.pop(identity, None)
        for target in targets:
            identity = id(target)
            if identity in self._known and identity not in protected:
                self._free[identity] = target

    def take(self, width, height):
        for identity, target in self._free.items():
            if target.width == width and target.height == height:
                self._free.pop(identity)
                return target
        return None
