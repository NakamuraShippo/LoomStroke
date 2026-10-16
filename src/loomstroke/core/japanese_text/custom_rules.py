"""User-defined prohibitions and character-class spacing, in em units."""

from collections.abc import Mapping
from dataclasses import replace

from .kinsoku_policy import KinsokuPolicy
from .metadata_values import finite_number

CLASSES = ("opening", "closing", "stop", "middle", "japanese", "latin", "space", "other")
PROHIBITIONS = ("line_start_prohibited", "line_end_prohibited", "inseparable", "hanging_chars")


def normalize_rules(raw):
    if not isinstance(raw, Mapping):
        return {}
    result = dict(raw)
    for key in PROHIBITIONS:
        if key in result:
            value = result[key]
            result[key] = "".join(dict.fromkeys(value)) if isinstance(value, str) else ""
    rules = []
    source = raw.get("spacing", [])
    seen = set()
    for entry in source if isinstance(source, (list, tuple)) else []:
        if not isinstance(entry, Mapping):
            continue
        left, right = entry.get("left"), entry.get("right")
        if left not in CLASSES or right not in CLASSES or (left, right) in seen:
            continue
        minimum = finite_number(entry.get("minimum"), 0.0, 0.0, 2.0)
        optimum = finite_number(entry.get("optimum"), minimum, minimum, 2.0)
        maximum = finite_number(entry.get("maximum"), optimum, optimum, 2.0)
        rules.append(
            dict(
                entry,
                left=left,
                right=right,
                minimum=minimum,
                optimum=optimum,
                maximum=maximum,
                priority=int(finite_number(entry.get("priority"), 0, 0, 9)),
            )
        )
        seen.add((left, right))
    if "spacing" in raw:
        result["spacing"] = rules
    return result


def custom_policy(options):
    base = KinsokuPolicy.by_id(options["kinsoku"], hanging=options["hanging"])
    raw = normalize_rules(options.get("custom_rules"))
    changes = {key: frozenset(raw[key]) for key in PROHIBITIONS if key in raw}
    return replace(base, **changes)


def spacing_rules(options):
    return {
        (entry["left"], entry["right"]): entry
        for entry in normalize_rules(options.get("custom_rules")).get("spacing", [])
    }
