"""Observe additive tool wiring without starting workers, rendering, or creating services."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from .extension_readiness_rules import RULES, Requirements
from .safety import external_services_allowed
from .types import McpToolSpec


def _services(service: Any, supplied: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if supplied is not None:
        return supplied if isinstance(supplied, Mapping) else {}
    # Callers can supply an inert snapshot explicitly. Never call the normal
    # operation provider here: third-party injections can create services.
    observer = getattr(service, "_editing_services_observer", None)
    if callable(observer):
        try:
            value = observer()
        except Exception:
            return {}
        return value if isinstance(value, Mapping) else {}
    return {}


def _dependency(
    service: Any, services: Mapping[str, Any], key: str, document: Mapping[str, Any]
) -> str:
    if "|" in key:
        states = [_dependency(service, services, item, document) for item in key.split("|")]
        return next(
            (state for state in ("connected", "attached_lazy") if state in states), "missing"
        )
    local = _local_dependencies(service, services, document)
    if key in local:
        return local[key]
    if services.get(key) is not None:
        return "connected"
    return "attached_lazy" if callable(services.get(key + "_factory")) else "missing"


def _sam_runtime(service: Any) -> str:
    if not callable(getattr(getattr(service, "sam3_provider", None), "refine", None)):
        return "missing"
    cached = getattr(getattr(service, "_sam3_surface", None), "_readiness", None)
    state = getattr(cached, "state", None)
    return (
        "connected"
        if state == "ready"
        else "blocked" if state in {"blocked", "unavailable"} else "unprobed"
    )


def _connected(present: bool) -> str:
    return "connected" if present else "missing"


def _local_dependencies(
    service: Any, services: Mapping[str, Any], document: Mapping[str, Any]
) -> dict[str, str]:
    host = getattr(service, "host_api", None)
    pages = getattr(service, "page_service", None)
    settings = _settings(service)
    manager = services.get("tool_manager") or getattr(host, "_tool_manager", None)
    return {
        "page_service": _connected(pages is not None),
        "document_workflow": _connected(document.get("available") is True),
        "host_color": _connected(callable(getattr(host, "get_color_snapshot", None))),
        "tool_manager": (
            "connected"
            if manager is not None
            else (
                "attached_lazy"
                if callable(getattr(service, "tool_manager_provider", None))
                else "missing"
            )
        ),
        "sam_assets": _connected(
            getattr(getattr(service, "sam3_provider", None), "asset_store", None) is not None
        ),
        "sam_refine_runtime": _sam_runtime(service),
        "book_metadata_port": _connected(
            callable(getattr(pages, "_book_metadata_apply_transition", None))
        ),
        "masters_metadata_port": _connected(
            callable(getattr(pages, "_masters_metadata_apply_transition", None))
        ),
        "writable_settings": _connected(
            isinstance(settings, dict) or callable(getattr(settings, "set", None))
        ),
    }


def _settings(service: Any) -> Any:
    return getattr(getattr(service, "safety", None), "settings", getattr(service, "settings", None))


def _block(code: str, message: str) -> dict[str, str]:
    return {"code": code, "message": message}


def _confirmation_operation(name: str) -> str:
    if name in {"loomstroke_submit_llm", "loomstroke_regenerate_gallery_record"}:
        return "loomstroke_submit_generation"
    return name


def _variant(
    service: Any,
    name: str,
    rule: Requirements,
    services: Mapping[str, Any],
    document: Mapping[str, Any],
) -> dict[str, Any]:
    dependencies = [
        {"service": key, "state": _dependency(service, services, key, document)}
        for key in rule.dependencies
    ]
    blocks = [
        _block("service_unavailable", f"Required service '{item['service']}' is not attached.")
        for item in dependencies
        if item["state"] in {"missing", "blocked"}
    ]
    notes = list(rule.notes)
    incomplete = rule.conditional or any(
        item["state"] in {"attached_lazy", "unprobed"} for item in dependencies
    )
    if any(item["state"] == "attached_lazy" for item in dependencies):
        notes.append(
            "A factory is attached but its service has not been created; "
            "readiness does not invoke it."
        )
    if any(item["state"] == "unprobed" for item in dependencies):
        notes.append(
            "No current ready runtime observation is cached; "
            "use the explicit runtime/readiness tool."
        )
    if rule.canvas and (
        getattr(service, "canvas_service", None) is None or document.get("present") is not True
    ):
        blocks.append(_block("document_unavailable", "No editable document is open."))
    safety = getattr(service, "safety", None)
    if rule.roots and not getattr(safety, "roots", ()):
        blocks.append(_block("roots_required", "Configure MCP roots for this file operation."))
    if rule.external and not external_services_allowed(_settings(service)):
        blocks.append(
            _block(
                "external_services_disabled",
                "External services are disabled by the privacy setting.",
            )
        )
    requires_confirmation = getattr(safety, "requires_confirmation", None)
    if (
        rule.mutation
        and callable(requires_confirmation)
        and requires_confirmation(_confirmation_operation(name))
        and not callable(getattr(safety, "confirmation_callback", None))
    ):
        blocks.append(_block("confirmation_unavailable", "A confirmation callback is required."))
    state = "blocked" if blocks else "partial" if incomplete else "ready"
    return {
        "ready": state == "ready",
        "available": state != "blocked",
        "state": state,
        "dependencies": dependencies,
        "blockedReasons": blocks,
        "notes": notes,
        "requiresCanvas": rule.canvas,
        "requiresRoots": rule.roots,
        "fileRead": rule.file_read,
        "fileWrite": rule.file_write,
        "externalService": rule.external,
        "currentDocumentAffects": rule.mutation and rule.canvas,
    }


def extension_tool_readiness(
    service: Any, specs: Iterable[McpToolSpec], *, services: Mapping[str, Any] | None = None
) -> dict[str, dict[str, Any]]:
    """Return metadata keyed by tool name from an existing service snapshot.

    ``ready`` means the declared connections/guards are satisfied, rather than
    that a selected input, model, remote endpoint, encoder, or file was probed.
    Callers should pass their inert ``get_services(service)`` mapping explicitly.
    Lazy services remain partial until attached; this helper never runs factories.
    """
    from .editing_readiness import document_state  # noqa: PLC0415

    observed = _services(service, services)
    document = document_state(service)
    result: dict[str, dict[str, Any]] = {}
    for spec in specs:
        rules = RULES.get(spec.name)
        if rules is None:
            result[spec.name] = {
                "tool": spec.name,
                "classified": False,
                "ready": False,
                "available": False,
                "state": "blocked",
                "blockedReasons": [
                    _block(
                        "readiness_unclassified",
                        "This tool has no explicit extension readiness contract.",
                    )
                ],
                "variants": {},
            }
            continue
        variants = {
            key: _variant(service, spec.name, rule, observed, document)
            for key, rule in rules.items()
        }
        entries = list(variants.values())
        states = {entry["state"] for entry in entries}
        state = (
            "ready" if states == {"ready"} else "blocked" if states == {"blocked"} else "partial"
        )
        dependencies = {
            item["service"]: item for entry in entries for item in entry["dependencies"]
        }
        blocks = {
            f"{item['code']}:{item['message']}": item
            for entry in entries
            for item in entry["blockedReasons"]
        }
        result[spec.name] = {
            "tool": spec.name,
            "classified": True,
            "ready": state == "ready",
            "available": state != "blocked",
            "state": state,
            "readOnly": spec.read_only,
            "requiresCanvas": all(entry["requiresCanvas"] for entry in entries),
            "conditionalCanvas": len({entry["requiresCanvas"] for entry in entries}) > 1,
            "requiresRoots": all(entry["requiresRoots"] for entry in entries),
            "conditionalRoots": len({entry["requiresRoots"] for entry in entries}) > 1,
            "fileRead": any(entry["fileRead"] for entry in entries),
            "fileWrite": any(entry["fileWrite"] for entry in entries),
            "externalService": any(entry["externalService"] for entry in entries),
            "currentDocumentAffects": any(entry["currentDocumentAffects"] for entry in entries),
            "dependencies": list(dependencies.values()),
            "blockedReasons": list(blocks.values()),
            "notes": list(dict.fromkeys(note for entry in entries for note in entry["notes"])),
            "actionNotes": {key: entry["notes"] for key, entry in variants.items()},
            "variants": variants,
        }
    return result
