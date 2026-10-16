"""
Plugin validation and compatibility checking.

Per Master.md PR#11 specifications, provides plugin verification.
"""

from __future__ import annotations

import base64
import hashlib
import json
import keyword
import logging
import tomllib
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from enum import Enum
from pathlib import Path
from typing import Any

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
except Exception:
    Ed25519PublicKey = None
    load_pem_public_key = None

from packaging import version
from packaging.specifiers import SpecifierSet

logger = logging.getLogger(__name__)


class ValidationResult(Enum):
    """Plugin validation result status."""

    VALID = "valid"
    INCOMPATIBLE_VERSION = "incompatible_version"
    INVALID_MANIFEST = "invalid_manifest"
    MISSING_DEPENDENCY = "missing_dependency"
    PERMISSION_DENIED = "permission_denied"
    SIGNATURE_INVALID = "signature_invalid"
    UNTRUSTED = "untrusted"


@dataclass
class PluginManifest:
    """Parsed plugin manifest data."""

    # Plugin metadata
    id: str
    name: str
    version: str
    description: str
    author: str
    license: str

    edition: str = "free"
    sku: str | None = None
    requires_entitlement: bool = False
    trial_days: int | None = None
    # Compatibility
    engine_version: str = ">=1.0.0"
    python_version: str = ">=3.11"

    # Entry point
    module: str | None = None
    factory: str = "create_plugin"

    # Contributions
    types: list[str] = dataclass_field(default_factory=list)

    # Permissions
    file_system: bool = False
    network: bool = False
    gpu: bool = False
    network_domains: list[str] = dataclass_field(default_factory=list)

    # Optional
    signature: str | None = None
    checksum: str | None = None

    @property
    def is_paid(self) -> bool:
        """Return True when this manifest represents a paid plugin."""
        return self.edition.lower() != "free" or bool(self.requires_entitlement)

    @classmethod
    def from_toml(cls, path: Path) -> PluginManifest:
        """
        Load manifest from plugin.toml file.

        Args:
            path: Path to plugin.toml

        Returns:
            Parsed manifest

        Raises:
            ValueError: Invalid manifest format
        """
        try:
            with open(path, "rb") as f:
                data = tomllib.load(f)

            plugin_data = data.get("plugin", {})
            compat_data = data.get("compat", {})
            entry_data = data.get("entry", {})
            contrib_data = data.get("contributes", {})
            perm_data = data.get("permissions", {})

            return cls(
                id=plugin_data["id"],
                name=plugin_data["name"],
                version=plugin_data["version"],
                description=plugin_data.get("description", ""),
                author=plugin_data.get("author", "Unknown"),
                license=plugin_data.get("license", "Unknown"),
                edition=plugin_data.get("edition", "free"),
                sku=plugin_data.get("sku"),
                requires_entitlement=bool(plugin_data.get("requires_entitlement", False)),
                trial_days=plugin_data.get("trial_days"),
                engine_version=compat_data.get("engine", ">=1.0.0"),
                python_version=compat_data.get("python", ">=3.11"),
                module=entry_data["module"],
                factory=entry_data["factory"],
                types=contrib_data.get("types", []),
                file_system=perm_data.get("file_system", False),
                network=perm_data.get("network", False),
                gpu=perm_data.get("gpu", False),
                network_domains=perm_data.get("network_domains", []),
                signature=plugin_data.get("signature"),
                checksum=plugin_data.get("checksum"),
            )
        except Exception as e:
            raise ValueError(f"Invalid manifest: {e}")  # noqa: B904


class PluginValidator:
    """
    Plugin validation and compatibility checker.

    Validates plugin manifests, checks compatibility,
    and verifies permissions.
    """

    def __init__(self, host_version: str = "1.0.0", trusted_keys: list[str] | None = None):
        """
        Initialize validator.

        Args:
            host_version: Current host/engine version
            trusted_keys: List of trusted signing keys
        """
        self.host_version = version.parse(host_version)
        self.trusted_keys = trusted_keys or []
        self._trusted_public_keys: dict[str, bytes] = {}
        self._legacy_trusted_key_ids: set[str] = set()
        self._load_trusted_keys(self.trusted_keys)

    def _load_trusted_keys(self, trusted_keys: list[str]) -> None:
        """
        Load trusted public keys from trusted key definitions.

        Expected format:
            key_id:key_material

        Legacy `key_id` only entries are kept for compatibility, but paid plugin
        signature validation requires explicit cryptographic key material.
        """
        for entry in trusted_keys:
            if not entry:
                continue

            if ":" not in entry:
                key_id = entry.strip()
                if key_id:
                    self._legacy_trusted_key_ids.add(key_id)
                continue

            key_id, key_material = entry.split(":", 1)
            key_id = key_id.strip()
            if not key_id:
                continue

            material_bytes = self._resolve_public_key_material(key_material.strip())
            if not material_bytes:
                logger.warning("Invalid trusted key definition for '%s'", key_id)
                continue

            self._trusted_public_keys[key_id] = material_bytes

    def validate_plugin(
        self,
        plugin_path: Path,
        *,
        check_signature: bool = True,
        require_signature: bool = False,
    ) -> tuple[ValidationResult, str | None]:
        """
        Validate a plugin completely.

        Args:
            plugin_path: Path to plugin directory
            check_signature: If False, skip signature verification
            require_signature: If True, unsigned plugins are rejected

        Returns:
            Tuple of (result, error_message)
        """
        # Check manifest exists
        manifest_path = plugin_path / "plugin.toml"
        if not manifest_path.exists():
            return ValidationResult.INVALID_MANIFEST, "Missing plugin.toml"

        # Parse manifest
        try:
            manifest = PluginManifest.from_toml(manifest_path)
        except ValueError as e:
            return ValidationResult.INVALID_MANIFEST, str(e)

        # Validate manifest
        result, msg = self.validate_manifest(manifest)
        if result != ValidationResult.VALID:
            return result, msg

        # Check compatibility
        result, msg = self.check_compatibility(manifest)
        if result != ValidationResult.VALID:
            return result, msg

        # Verify signature
        if require_signature and not manifest.signature:
            return ValidationResult.SIGNATURE_INVALID, "External plugin requires signature"

        if check_signature and manifest.signature:
            result, msg = self.verify_signature(plugin_path, manifest)
            if result != ValidationResult.VALID:
                return result, msg

        # Verify checksum if present
        if manifest.checksum:
            result, msg = self.verify_checksum(plugin_path, manifest)
            if result != ValidationResult.VALID:
                return result, msg

        return ValidationResult.VALID, None

    def validate_manifest(self, manifest: PluginManifest) -> tuple[ValidationResult, str | None]:
        """
        Validate manifest structure and required fields.

        Args:
            manifest: Plugin manifest to validate

        Returns:
            Tuple of (result, error_message)
        """
        # Check required fields
        required_fields = ["id", "name", "version", "module", "factory"]
        for field in required_fields:
            if not getattr(manifest, field, None):
                return ValidationResult.INVALID_MANIFEST, f"Missing required field: {field}"

        if manifest.edition.lower() not in {
            "free",
            "basic",
            "standard",
            "premium",
            "pro",
            "enterprise",
        }:
            return ValidationResult.INVALID_MANIFEST, (
                f"Invalid plugin edition: {manifest.edition} "
                "(must be free/basic/standard/premium/pro/enterprise)"
            )

        if not manifest.factory or not manifest.factory.isidentifier():
            return ValidationResult.INVALID_MANIFEST, (
                f"Invalid plugin factory name: {manifest.factory}"
            )

        if manifest.requires_entitlement and manifest.edition.lower() == "free":
            return ValidationResult.INVALID_MANIFEST, (
                "requires_entitlement is true but edition is free"
            )

        if manifest.requires_entitlement and not manifest.sku:
            return ValidationResult.INVALID_MANIFEST, (
                "Paid plugin requires sku when entitlement is required"
            )

        if manifest.is_paid and not manifest.signature:
            return ValidationResult.SIGNATURE_INVALID, ("Paid plugin requires signature")

        # Validate ID format (reverse domain notation)
        if not self._validate_plugin_id(manifest.id):
            return ValidationResult.INVALID_MANIFEST, f"Invalid plugin ID format: {manifest.id}"

        # Validate version format
        try:
            version.parse(manifest.version)
        except Exception:
            return ValidationResult.INVALID_MANIFEST, f"Invalid version format: {manifest.version}"

        # Validate plugin types
        valid_types = ["tool", "filter", "ai", "io", "ui", "workflow", "automation", "keymap"]
        declared_types: list[str]
        if isinstance(manifest.types, str):
            declared_types = [manifest.types]
        elif isinstance(manifest.types, tuple):
            declared_types = list(manifest.types)
        elif isinstance(manifest.types, list):
            declared_types = manifest.types
        else:
            declared_types = []

        for plugin_type in declared_types:
            if not isinstance(plugin_type, str) or plugin_type not in valid_types:
                return ValidationResult.INVALID_MANIFEST, f"Invalid plugin type: {plugin_type}"

        if not self._validate_plugin_module_name(manifest.module):
            return ValidationResult.INVALID_MANIFEST, (
                f"Invalid plugin module path: {manifest.module}"
            )

        return ValidationResult.VALID, None

    def check_compatibility(self, manifest: PluginManifest) -> tuple[ValidationResult, str | None]:
        """
        Check if plugin is compatible with current host.

        Args:
            manifest: Plugin manifest to check

        Returns:
            Tuple of (result, error_message)
        """
        # Parse engine version requirement
        try:
            spec = SpecifierSet(manifest.engine_version)
            if self.host_version not in spec:
                return ValidationResult.INCOMPATIBLE_VERSION, (
                    f"Plugin requires engine {manifest.engine_version}, "
                    f"but host is {self.host_version}"
                )
        except Exception as e:
            return ValidationResult.INCOMPATIBLE_VERSION, f"Invalid engine version spec: {e}"

        # Check Python version if needed
        import sys  # noqa: PLC0415

        current_python = f"{sys.version_info.major}.{sys.version_info.minor}"
        try:
            spec = SpecifierSet(manifest.python_version)
            if version.parse(current_python) not in spec:
                return ValidationResult.INCOMPATIBLE_VERSION, (
                    f"Plugin requires Python {manifest.python_version}, "
                    f"but current is {current_python}"
                )
        except Exception as e:
            return ValidationResult.INCOMPATIBLE_VERSION, f"Invalid Python version spec: {e}"

        return ValidationResult.VALID, None

    def check_permissions(
        self, manifest: PluginManifest, granted_permissions: dict[str, bool | list[str]]
    ) -> tuple[ValidationResult, str | None]:
        """
        Check if required permissions are granted.

        Args:
            manifest: Plugin manifest with permission requirements
            granted_permissions: User-granted permissions

        Returns:
            Tuple of (result, error_message)
        """
        required = []

        if manifest.file_system and not granted_permissions.get("file_system", False):
            required.append("file_system")

        if manifest.network and not granted_permissions.get("network", False):
            required.append("network")

        if manifest.gpu and not granted_permissions.get("gpu", False):
            required.append("gpu")

        if required:
            return ValidationResult.PERMISSION_DENIED, (
                f"Plugin requires permissions: {', '.join(required)}"
            )

        # Check network domains if network permission granted
        if manifest.network and manifest.network_domains:
            allowed_domains = granted_permissions.get("network_domains", [])
            for domain in manifest.network_domains:
                if domain not in allowed_domains and "*" not in allowed_domains:
                    return ValidationResult.PERMISSION_DENIED, (
                        f"Plugin requires network access to: {domain}"
                    )

        return ValidationResult.VALID, None

    def verify_signature(
        self, plugin_path: Path, manifest: PluginManifest
    ) -> tuple[ValidationResult, str | None]:
        """
        Verify plugin signature.

        Args:
            plugin_path: Path to plugin directory
            manifest: Plugin manifest with signature

        Returns:
            Tuple of (result, error_message)
        """
        if not manifest.signature:
            return ValidationResult.VALID, None

        try:
            key_id, algorithm, signature_b64 = self._parse_signature_header(manifest.signature)
        except ValueError as exc:
            return ValidationResult.SIGNATURE_INVALID, str(exc)

        public_key_material = self._trusted_public_keys.get(key_id)
        if not public_key_material:
            if key_id in self._legacy_trusted_key_ids:
                return ValidationResult.UNTRUSTED, (
                    f"Legacy key trust policy not usable for signature: {key_id}"
                )
            return ValidationResult.UNTRUSTED, f"Signature from untrusted key: {key_id}"

        signature = self._decode_signature(signature_b64)
        if not signature:
            return ValidationResult.SIGNATURE_INVALID, "Invalid signature encoding"

        try:
            public_key = self._load_public_key(public_key_material)
        except Exception as exc:
            return ValidationResult.SIGNATURE_INVALID, f"Invalid trusted key material: {exc}"

        payload = self._signature_payload(plugin_path)
        if algorithm != "ed25519":
            return (
                ValidationResult.SIGNATURE_INVALID,
                f"Unsupported signature algorithm: {algorithm}",
            )

        try:
            public_key.verify(signature, payload)
        except Exception as exc:
            logger.debug("Signature verification failed for %s: %s", manifest.id, exc)
            return ValidationResult.SIGNATURE_INVALID, "Signature verification failed"

        return ValidationResult.VALID, None

    def _parse_signature_header(self, signature: str) -> tuple[str, str, str]:
        """
        Parse signature field as `key_id:signature` or `key_id:alg:signature`.
        """
        if ":" not in signature:
            raise ValueError("Signature missing key ID")

        first, rest = signature.split(":", 1)
        if ":" in rest:
            algorithm, signature_b64 = rest.split(":", 1)
            if not first or not algorithm or not signature_b64:
                raise ValueError("Invalid signature format")
            return first.strip(), algorithm.strip().lower(), signature_b64.strip()

        if not first or not rest:
            raise ValueError("Invalid signature format")
        return first.strip(), "ed25519", rest.strip()

    def _decode_signature(self, signature_b64: str) -> bytes | None:
        """
        Decode base64 or url-safe base64 signature bytes.
        """
        try:
            return base64.b64decode(signature_b64, validate=True)
        except Exception:
            try:
                normalized = signature_b64.replace("-", "+").replace("_", "/")
                normalized = normalized + "=" * (-len(normalized) % 4)
                return base64.b64decode(normalized, validate=True)
            except Exception:
                return None

    def _resolve_public_key_material(self, material: str) -> bytes | None:
        """
        Resolve trusted key material from inline PEM, file path, or base64.
        """
        if not material:
            return None

        if material.startswith("-----BEGIN "):
            return material.encode("utf-8")

        candidate = Path(material).expanduser()
        if candidate.exists():
            try:
                return candidate.read_bytes()
            except Exception as exc:
                logger.debug("Failed to read trusted key file %s: %s", candidate, exc)
                return None

        fallback = self._decode_signature(material)
        if fallback is not None:
            return fallback

        return material.encode("utf-8")

    @staticmethod
    def _load_public_key(raw_material: bytes) -> Any:
        """
        Load a public key object from PEM or raw bytes.
        """
        if load_pem_public_key is not None:
            try:
                return load_pem_public_key(raw_material)
            except Exception as exc:
                logger.debug("Failed to parse PEM public key: %s", exc)

        if len(raw_material) != 32:
            raise ValueError("Unsupported public key format")
        if Ed25519PublicKey is None:
            raise ValueError("cryptography package is required for signature verification")
        try:
            return Ed25519PublicKey.from_public_bytes(raw_material)
        except Exception as exc:
            raise ValueError("Unsupported public key format") from exc

    @staticmethod
    def _signature_payload(plugin_path: Path) -> bytes:
        """
        Build the deterministic signature digest from plugin files.

        The signed value is the sha256 digest of the canonical byte stream
        defined in docs/dev/release_policy_specs.md (formerly
        plugin_signature_key_policy_2026-02-16.md).
        """
        payload = bytearray(b"loomstroke-plugin-signature-v1\n")
        files: list[Path] = []

        for entry in plugin_path.rglob("*"):
            if not entry.is_file():
                continue
            if entry.is_symlink():
                continue
            rel = entry.relative_to(plugin_path)
            if any(part in {"__pycache__", ".git", ".svn", ".hg"} for part in rel.parts):
                continue
            if entry.suffix in {".pyc", ".pyo"}:
                continue
            files.append(entry)

        for entry in sorted(files, key=lambda path: path.relative_to(plugin_path).as_posix()):
            rel = entry.relative_to(plugin_path).as_posix()
            rel_bytes = rel.encode("utf-8")
            content = PluginValidator._canonical_file_bytes(entry, rel)
            content_hash = hashlib.sha256(content).digest()
            payload.extend(len(rel_bytes).to_bytes(4, "big"))
            payload.extend(rel_bytes)
            payload.extend(content_hash)

        return hashlib.sha256(bytes(payload)).digest()

    @staticmethod
    def _canonical_file_bytes(entry: Path, relative_path: str) -> bytes:
        if relative_path == "plugin.toml":
            return PluginValidator._canonical_plugin_toml_bytes(entry)
        return entry.read_bytes()

    @staticmethod
    def _canonical_plugin_toml_bytes(path: Path) -> bytes:
        with open(path, "rb") as handle:
            parsed = tomllib.load(handle)

        plugin_table = parsed.get("plugin")
        if isinstance(plugin_table, dict):
            plugin_table.pop("signature", None)

        lines: list[str] = []
        top_level = {key: value for key, value in parsed.items() if not isinstance(value, dict)}
        for key in sorted(top_level):
            lines.append(f"{key} = {PluginValidator._toml_value(top_level[key])}")

        for table_name in sorted(key for key, value in parsed.items() if isinstance(value, dict)):
            if lines:
                lines.append("")
            lines.append(f"[{table_name}]")
            table = parsed[table_name]
            if not isinstance(table, dict):
                continue
            for key in sorted(table):
                lines.append(f"{key} = {PluginValidator._toml_value(table[key])}")

        return ("\n".join(lines) + "\n").encode("utf-8")

    @staticmethod
    def _toml_value(value: Any) -> str:
        if isinstance(value, str):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, float):
            return repr(value)
        if isinstance(value, list):
            return "[" + ", ".join(PluginValidator._toml_value(item) for item in value) + "]"
        raise TypeError(f"Unsupported plugin.toml value in signature payload: {type(value)!r}")

    def verify_checksum(
        self, plugin_path: Path, manifest: PluginManifest
    ) -> tuple[ValidationResult, str | None]:
        """
        Verify plugin file checksum.

        Args:
            plugin_path: Path to plugin directory
            manifest: Plugin manifest with checksum

        Returns:
            Tuple of (result, error_message)
        """
        if not manifest.checksum:
            return ValidationResult.VALID, None

        # Calculate actual checksum
        actual_checksum = self._calculate_checksum(plugin_path)

        if actual_checksum != manifest.checksum:
            return ValidationResult.SIGNATURE_INVALID, (
                f"Checksum mismatch: expected {manifest.checksum}, got {actual_checksum}"
            )

        return ValidationResult.VALID, None

    def _validate_plugin_id(self, plugin_id: str) -> bool:
        """
        Validate plugin ID format (reverse domain notation).

        Args:
            plugin_id: Plugin ID to validate

        Returns:
            True if valid format
        """
        parts = plugin_id.split(".")
        if len(parts) < 2:
            return False

        # Each part should be non-empty and contain valid characters
        for part in parts:
            if not part or not all(c.isalnum() or c in "-_" for c in part):
                return False

        return True

    @staticmethod
    def _validate_plugin_module_name(module_name: str | None) -> bool:
        """
        Validate plugin entry module path format.

        Restricts to Python identifier segments to prevent path traversal via manifest.module.
        """
        if not module_name or not module_name.strip():
            return False

        normalized = module_name.strip().replace("\\", ".").replace("/", ".")
        if normalized.startswith(".") or normalized.endswith("."):
            return False

        parts = normalized.split(".")
        for part in parts:
            if not part or part == ".." or keyword.iskeyword(part) or not part.isidentifier():
                return False

        return True

    def _calculate_checksum(self, plugin_path: Path) -> str:
        """
        Calculate checksum for plugin files.

        Args:
            plugin_path: Path to plugin directory

        Returns:
            SHA256 checksum string
        """
        hasher = hashlib.sha256()

        # Hash all Python files in plugin
        for py_file in sorted(plugin_path.rglob("*.py")):
            if "__pycache__" not in str(py_file):
                with open(py_file, "rb") as f:
                    hasher.update(f.read())

        return hasher.hexdigest()


class CompatibilityChecker:
    """
    Check compatibility between multiple plugins.

    Detects conflicts and dependency issues.
    """

    def __init__(self):
        """Initialize compatibility checker."""
        self.loaded_plugins: dict[str, PluginManifest] = {}

    def check_plugin_compatibility(
        self, new_plugin: PluginManifest, existing_plugins: list[PluginManifest]
    ) -> list[str]:
        """
        Check if new plugin is compatible with existing ones.

        Args:
            new_plugin: Plugin to check
            existing_plugins: Already loaded plugins

        Returns:
            List of compatibility warnings
        """
        warnings = []

        # Check for ID conflicts
        for existing in existing_plugins:
            if existing.id == new_plugin.id and version.parse(existing.version) != version.parse(
                new_plugin.version
            ):
                warnings.append(
                    f"Plugin {new_plugin.id} v{new_plugin.version} conflicts with "
                    f"existing v{existing.version}"
                )

        # Check for type conflicts (e.g., multiple tools claiming same ID)
        for existing in existing_plugins:
            common_types = set(existing.types) & set(new_plugin.types)
            if common_types and existing.id != new_plugin.id and "tool" in common_types:
                # Some overlap is OK (e.g., both have "ui")
                # But specific tools shouldn't conflict
                warnings.append(
                    f"Plugin {new_plugin.id} may conflict with {existing.id} "
                    f"(both provide tool functionality)"
                )

        return warnings

    def suggest_updates(
        self, plugins: list[PluginManifest], available_updates: dict[str, str]
    ) -> list[tuple[str, str, str]]:
        """
        Suggest plugin updates based on available versions.

        Args:
            plugins: Currently loaded plugins
            available_updates: Map of plugin_id -> latest_version

        Returns:
            List of (plugin_id, current_version, suggested_version)
        """
        suggestions = []

        for plugin in plugins:
            if plugin.id in available_updates:
                latest = available_updates[plugin.id]
                current = version.parse(plugin.version)
                available = version.parse(latest)

                if available > current:
                    suggestions.append((plugin.id, plugin.version, latest))

        return suggestions
