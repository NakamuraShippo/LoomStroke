"""Shared helpers for JSON schema validation."""

from __future__ import annotations

import argparse
import importlib
import json
import logging
from collections.abc import Iterable, Sequence
from functools import lru_cache
from importlib import resources
from typing import Any, Protocol, cast, runtime_checkable

__all__ = [
    "JsonSchemaValidationError",
    "SchemaValidator",
    "ValidatorFactory",
    "ValidatorImportError",
    "get_validator_factory",
    "import_validator_factory",
    "load_manifest_schema",
    "resolve_validator_factory",
    "run_manifest_schema_cli",
    "sample_manifest",
]

logger = logging.getLogger(__name__)

_MANIFEST_SCHEMA_RESOURCE = "lstroke_manifest_schema_v1.json"
_SCHEMA_PACKAGE = "loomstroke.io"
_DEFAULT_VALIDATOR_NAME = "jsonschema.Draft202012Validator"

_VALIDATOR_ALIASES: dict[str, str] = {
    "draft202012": _DEFAULT_VALIDATOR_NAME,
    "draft201909": "jsonschema.Draft201909Validator",
    "draft7": "jsonschema.Draft7Validator",
    "draft6": "jsonschema.Draft6Validator",
    "draft4": "jsonschema.Draft4Validator",
}


class SchemaValidator(Protocol):
    """Protocol representing the subset of jsonschema validator behaviour we rely on."""

    def validate(self, instance: Any) -> None: ...

    def iter_errors(self, instance: Any) -> Iterable[Any]: ...


@runtime_checkable
class SupportsSchemaCheck(Protocol):
    """Protocol for factories that expose check_schema."""

    def check_schema(self, schema: dict[str, Any]) -> None: ...


class ValidatorFactory(Protocol):
    """Protocol describing callables that create schema validators."""

    def __call__(self, schema: dict[str, Any]) -> SchemaValidator: ...


JsonSchemaValidationError: type[Exception]
_DRAFT_VALIDATOR_FACTORY: ValidatorFactory | None


class ValidatorImportError(RuntimeError):
    """Raised when an explicit validator factory cannot be imported."""


try:  # pragma: no cover - optional dependency
    _jsonschema_module = importlib.import_module("jsonschema")
except ImportError:  # pragma: no cover - executed when jsonschema is absent
    _DRAFT_VALIDATOR_FACTORY = None

    class _FallbackJsonSchemaValidationError(Exception):
        """Fallback validation error raised when jsonschema is unavailable."""

        pass

    JsonSchemaValidationError = _FallbackJsonSchemaValidationError
else:  # pragma: no cover - thin wrapper, exercised via integration tests
    _DRAFT_VALIDATOR_FACTORY = cast(
        ValidatorFactory | None,
        getattr(_jsonschema_module, "Draft202012Validator", None),
    )
    JsonSchemaValidationError = cast(
        type[Exception],
        getattr(_jsonschema_module, "ValidationError", Exception),
    )


def get_validator_factory() -> ValidatorFactory | None:
    """Return the configured jsonschema validator factory if available."""

    return _DRAFT_VALIDATOR_FACTORY


def list_validator_aliases() -> dict[str, str]:
    """Return a copy of the known validator alias mappings."""

    return dict(_VALIDATOR_ALIASES)


def import_validator_factory(dotted_path: str) -> ValidatorFactory:
    """Import a validator factory class/function from a dotted path."""

    module_name, _, attr = dotted_path.rpartition(".")
    if not module_name or not attr:
        raise ValidatorImportError(
            f"validator target '{dotted_path}' must be in 'module.attr' format"
        )

    try:
        module = importlib.import_module(module_name)
    except ImportError as exc:  # pragma: no cover - importlib handles messaging
        raise ValidatorImportError(f"failed to import module '{module_name}': {exc}") from exc

    try:
        factory = getattr(module, attr)
    except AttributeError as exc:
        raise ValidatorImportError(f"module '{module_name}' has no attribute '{attr}'") from exc

    if not callable(factory):
        raise ValidatorImportError(
            f"'{dotted_path}' is not callable; validator factories must be callables"
        )

    return cast(ValidatorFactory, factory)


def resolve_validator_factory(target: str | None) -> tuple[ValidatorFactory | None, str]:
    """Resolve a validator factory from an optional alias/dotted path."""

    if target is None:
        factory = get_validator_factory()
        descriptor = _DEFAULT_VALIDATOR_NAME if factory else "jsonschema unavailable"
        return factory, descriptor

    normalized = _VALIDATOR_ALIASES.get(target.lower(), target)
    factory = import_validator_factory(normalized)
    return factory, normalized


@lru_cache(maxsize=1)
def load_manifest_schema() -> dict[str, Any]:
    """Load the JSON schema used to validate manifest files."""

    try:
        schema_resource = resources.files(_SCHEMA_PACKAGE) / _MANIFEST_SCHEMA_RESOURCE
    except ModuleNotFoundError as exc:  # pragma: no cover - package not installed
        logger.warning("Manifest schema resource unavailable: %s", exc)
        raise FileNotFoundError(
            f"package {_SCHEMA_PACKAGE!r} is not importable; install the project first"
        ) from exc

    with schema_resource.open("r", encoding="utf-8") as handle:
        return cast(dict[str, Any], json.load(handle))


def sample_manifest() -> dict[str, Any]:
    """Return a minimal manifest instance used for validation sanity checks."""

    return {
        "format": "LoomStrokeDocument",
        "format_version": "1.0.0",
        "app_version": "0.0.0",
        "canvas": {
            "width": 1280,
            "height": 720,
            "background_color": [0, 0, 0, 255],
            "dpi": [72.0, 72.0],
        },
        "layers": [
            {
                "id": "layer-1",
                "name": "Layer 1",
                "visible": True,
                "opacity": 1.0,
                "blend_mode": "normal",
                "locked": False,
                "image": "layers/layer_000.png",
                "thumbnail": "thumbnails/thumb_000.png",
                "metadata": {},
            }
        ],
        "metadata": {},
    }


def _maybe_check_schema(factory: ValidatorFactory, schema: dict[str, Any]) -> None:
    """Invoke check_schema when the underlying factory supports it."""

    if isinstance(factory, SupportsSchemaCheck):  # pragma: no cover - optional branch
        factory.check_schema(schema)


def _load_schema_for_cli() -> dict[str, Any] | None:
    try:
        return load_manifest_schema()
    except FileNotFoundError as exc:
        print(f"Manifest schema file missing: {exc}")
        return None
    except json.JSONDecodeError as exc:
        print(f"Manifest schema is invalid JSON: {exc}")
        return None
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Manifest schema could not be loaded: {exc}")
        return None


def _validate_schema_for_cli(factory: ValidatorFactory, schema: dict[str, Any]) -> bool:
    try:
        _maybe_check_schema(factory, schema)
        validator = factory(schema)
        validator.validate(sample_manifest())
    except JsonSchemaValidationError as exc:
        print(f"Manifest schema validation failed: {exc}")
        return False
    except Exception as exc:  # pragma: no cover - defensive
        print(f"Manifest schema validation failed: {exc}")
        return False

    return True


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate the LoomStroke .lstroke manifest schema.",
    )
    parser.add_argument(
        "-v",
        "--validator",
        help=(
            "Optional validator to import. Accepts an alias (e.g. 'draft202012') "
            "or a dotted path to a jsonschema Draft class."
        ),
    )
    parser.add_argument(
        "--list-validators",
        action="store_true",
        help="List known validator aliases and exit.",
    )
    return parser


def _print_validator_aliases() -> None:
    print("Known validator aliases:")
    for alias, target in sorted(list_validator_aliases().items()):
        print(f"  {alias:>11} -> {target}")


def run_manifest_schema_cli(argv: Sequence[str] | None = None) -> int:
    """Entry point used by scripts/CI to validate the manifest schema."""

    parser = _build_arg_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.list_validators:
        _print_validator_aliases()
        return 0

    try:
        factory, descriptor = resolve_validator_factory(args.validator)
    except ValidatorImportError as exc:
        print(f"Failed to import validator '{args.validator}': {exc}")
        return 1

    if factory is None:
        print("jsonschema not available; skipping manifest validation.")
        return 0

    schema = _load_schema_for_cli()
    if schema is None:
        return 1

    if not _validate_schema_for_cli(factory, schema):
        return 1

    print(f"Manifest schema validation succeeded using {descriptor}.")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI passthrough
    import sys

    sys.exit(run_manifest_schema_cli())
