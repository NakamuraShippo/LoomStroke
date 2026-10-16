"""
Enhanced plugin loader with validation and sandboxing.

Per Master.md PR#11 specifications, provides safe plugin loading.
"""

from __future__ import annotations

import ast
import builtins as py_builtins
import importlib.abc
import importlib.util
import keyword
import logging
import os
import shutil
import stat
import sys
import tempfile
import uuid
import zipfile
from contextlib import suppress
from pathlib import Path
from typing import Any

from ...platform.user_dirs import plugin_data_root
from .base import PluginBase
from .validator import (
    CompatibilityChecker,
    PluginManifest,
    PluginValidator,
    ValidationResult,
)

logger = logging.getLogger(__name__)


class PluginLoadError(Exception):
    """Plugin loading error."""

    pass


class _PluginImportGuard(importlib.abc.MetaPathFinder):
    """
    Meta path finder that blocks import of dangerous modules during plugin execution.
    """

    def __init__(self) -> None:
        self._blocked_roots: set[str] = set()

    def set_blocked_roots(self, blocked_roots: set[str]) -> None:
        self._blocked_roots = set(blocked_roots)

    def find_spec(
        self,
        fullname: str,
        path: list[str] | None,
        target: Any | None = None,
    ) -> Any | None:
        root = fullname.split(".", 1)[0]
        if root in self._blocked_roots:
            raise PluginLoadError(f"Import blocked by plugin runtime policy: {fullname}")
        return None


class PluginLoader:
    """
    Safe plugin loader with validation and sandboxing.

    Validates plugins before loading and provides
    isolation between plugins.
    """

    def __init__(
        self,
        host_version: str = "1.0.0",
        plugins_dir: Path | None = None,
        trusted_keys: list[str] | None = None,
    ):
        """
        Initialize plugin loader.

        Args:
            host_version: Current host/engine version
            plugins_dir: Directory containing plugins
            trusted_keys: List of trusted signing keys
        """
        self.host_version = host_version
        self.plugins_dir = plugins_dir or plugin_data_root()
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.validator = PluginValidator(host_version, trusted_keys)
        self.compatibility_checker = CompatibilityChecker()
        self.loaded_plugins: dict[str, PluginBase] = {}
        self.plugin_manifests: dict[str, PluginManifest] = {}
        self._plugin_import_blocks: dict[str, set[str]] = {}
        self._import_guard = _PluginImportGuard()
        self._import_guard_active = False
        self._always_blocked_imports = frozenset(
            {
                "ctypes",
                "subprocess",
                "marshal",
                "pickle",
                "ssl",
                "signal",
                "threading",
                "multiprocessing",
            }
        )
        self._network_only_imports = frozenset(
            {
                "socket",
                "http",
                "urllib",
                "requests",
                "aiohttp",
                "websockets",
                "httpx",
                "urllib3",
            }
        )
        self._filesystem_only_imports = frozenset(
            {
                "os",
                "pathlib",
                "tempfile",
                "shutil",
                "glob",
            }
        )
        self._zip_max_members = 4096
        self._zip_max_uncompressed_bytes = 128 * 1024 * 1024
        self._zip_max_member_bytes = 64 * 1024 * 1024
        self._zip_max_compression_ratio = 300

    def list_plugin_backups(self, plugin_id: str) -> list[Path]:
        """
        Return candidate backup directories for a plugin id.

        Args:
            plugin_id: Plugin ID

        Returns:
            Backup directories ordered by newest-first
        """
        if not plugin_id:
            return []
        backup_prefix = f".{plugin_id}.backup."
        if not self.plugins_dir.exists():
            return []

        backups = [
            entry
            for entry in self.plugins_dir.iterdir()
            if entry.is_dir() and entry.name.startswith(backup_prefix)
        ]
        backups.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
        return backups

    def latest_plugin_backup(self, plugin_id: str) -> Path | None:
        """
        Return the latest backup path for a plugin, if present.
        """
        backups = self.list_plugin_backups(plugin_id)
        return backups[0] if backups else None

    def clear_plugin_backups(
        self,
        plugin_id: str,
        *,
        keep: Path | None = None,
    ) -> None:
        """
        Remove stale backup directories for the plugin.

        Args:
            plugin_id: Plugin ID
            keep: Optional backup path to keep
        """
        for backup in self.list_plugin_backups(plugin_id):
            if keep is not None and backup == keep:
                continue
            with suppress(Exception):
                shutil.rmtree(backup, ignore_errors=True)

    def discover_plugins(self) -> list[tuple[Path, PluginManifest]]:
        """
        Discover all plugins in the plugins directory.

        Returns:
            List of (plugin_path, manifest) tuples
        """
        discovered = []

        if not self.plugins_dir.exists():
            logger.warning(f"Plugins directory does not exist: {self.plugins_dir}")
            return discovered

        # Look for plugin directories
        for item in self.plugins_dir.iterdir():
            if item.is_dir():
                manifest_path = item / "plugin.toml"
                if manifest_path.exists():
                    try:
                        manifest = PluginManifest.from_toml(manifest_path)
                        discovered.append((item, manifest))
                        logger.info(f"Discovered plugin: {manifest.id} v{manifest.version}")
                    except Exception as e:
                        logger.error(f"Failed to load manifest from {item}: {e}")

        return discovered

    def load_from_source(self, source: Path | str) -> tuple[Path, PluginManifest]:
        """
        Prepare a plugin package from a file-system source.

        Supports directory plugins with ``plugin.toml`` and zip archives.

        Returns:
            Tuple of (installed_plugin_path, manifest)
        """
        source_path = Path(source).expanduser()

        if source_path.is_dir():
            if not (source_path / "plugin.toml").exists():
                raise PluginLoadError(f"Missing plugin.toml in directory: {source_path}")
            manifest = PluginManifest.from_toml(source_path / "plugin.toml")
            return source_path, manifest

        if source_path.is_file():
            if source_path.suffix.lower() != ".zip":
                raise PluginLoadError(f"Unsupported plugin source type: {source_path}")
            return self._install_from_zip(source_path)

        raise PluginLoadError(f"Plugin source not found: {source_path}")

    def inspect_source(
        self, source: Path | str
    ) -> tuple[PluginManifest, ValidationResult, str | None]:
        """
        Inspect a plugin source and return manifest with validation result.

        This validates plugin metadata before importing or installing.

        Args:
            source: Plugin source path

        Returns:
            Tuple of (manifest, validation_result, message)
        """
        source_path = Path(source).expanduser()

        if source_path.is_dir():
            manifest_path = source_path / "plugin.toml"
            if not manifest_path.exists():
                raise PluginLoadError(f"Missing plugin.toml in directory: {source_path}")
            manifest = PluginManifest.from_toml(manifest_path)
            result, msg = self.validate_plugin(source_path)
            return manifest, result, msg

        if source_path.is_file():
            if source_path.suffix.lower() != ".zip":
                raise PluginLoadError(f"Unsupported plugin source type: {source_path}")
            staging_root = Path(
                tempfile.mkdtemp(prefix="loomstroke_plugin_inspect_", dir=str(self.plugins_dir))
            )
            try:
                extracted_root = self._extract_zip(source_path, staging_root)
                manifest_path = self._find_plugin_manifest(extracted_root)
                manifest = PluginManifest.from_toml(manifest_path)
                result, msg = self.validate_plugin(manifest_path.parent, require_signature=True)
                return manifest, result, msg
            finally:
                shutil.rmtree(staging_root, ignore_errors=True)

        raise PluginLoadError(f"Plugin source not found: {source_path}")

    def _install_from_zip(self, zip_path: Path) -> tuple[Path, PluginManifest]:
        staging_root = Path(
            tempfile.mkdtemp(prefix="loomstroke_plugin_zip_", dir=str(self.plugins_dir))
        )
        try:
            extracted_root = self._extract_zip(zip_path, staging_root)
            manifest_path = self._find_plugin_manifest(extracted_root)
            manifest = PluginManifest.from_toml(manifest_path)
            plugin_path = manifest_path.parent

            result, msg = self.validate_plugin(plugin_path, require_signature=True)
            if result != ValidationResult.VALID:
                raise PluginLoadError(f"Plugin validation failed: {msg}")

            installed_path, _ = self._install_plugin_path(plugin_path, manifest.id)
            return installed_path, manifest
        except Exception as e:
            raise PluginLoadError(f"Failed to install plugin from zip: {e}") from e
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

    def install_from_source(
        self,
        source: Path | str,
        *,
        keep_backup: bool = False,
    ) -> tuple[Path, PluginManifest, Path | None]:
        source_path = Path(source).expanduser()
        if source_path.is_dir():
            manifest_path = source_path / "plugin.toml"
            if not manifest_path.exists():
                raise PluginLoadError(f"Missing plugin.toml in directory: {source_path}")
            manifest = PluginManifest.from_toml(manifest_path)
            result, msg = self.validate_plugin(source_path)
            if result != ValidationResult.VALID:
                raise PluginLoadError(f"Plugin validation failed: {msg}")
            return source_path, manifest, None

        if source_path.is_file():
            if source_path.suffix.lower() != ".zip":
                raise PluginLoadError(f"Unsupported plugin source type: {source_path}")
            staging_root = Path(
                tempfile.mkdtemp(prefix="loomstroke_plugin_zip_staging_", dir=str(self.plugins_dir))
            )
            try:
                extracted_root = self._extract_zip(source_path, staging_root)
                manifest_path = self._find_plugin_manifest(extracted_root)
                manifest = PluginManifest.from_toml(manifest_path)
                plugin_path = manifest_path.parent
                result, msg = self.validate_plugin(plugin_path, require_signature=True)
                if result != ValidationResult.VALID:
                    raise PluginLoadError(f"Plugin validation failed: {msg}")
                installed_path, backup_path = self._install_plugin_path(
                    plugin_path,
                    manifest.id,
                    keep_backup=keep_backup,
                )
                return installed_path, manifest, backup_path
            finally:
                shutil.rmtree(staging_root, ignore_errors=True)

        raise PluginLoadError(f"Plugin source not found: {source_path}")

    def _extract_zip(self, zip_path: Path, extract_root: Path) -> Path:
        if not zipfile.is_zipfile(zip_path):
            raise PluginLoadError(f"Invalid zip file: {zip_path}")

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                entries = zf.infolist()
                if len(entries) > self._zip_max_members:
                    raise PluginLoadError(
                        f"Zip has too many entries: {len(entries)} (max {self._zip_max_members})"
                    )

                total_size = 0
                seen_members: set[str] = set()
                for info in entries:
                    member = info.filename
                    normalized_member = member.replace("\\", "/")
                    if "\x00" in member or not normalized_member.strip():
                        raise PluginLoadError(f"Zip contains unsafe path: {member}")
                    if (
                        os.path.splitdrive(member)[0]
                        or os.path.isabs(member)
                        or normalized_member.startswith(("/", "\\"))
                    ):
                        raise PluginLoadError(f"Zip contains unsafe path: {member}")

                    normalized_parts = [part for part in normalized_member.split("/") if part]
                    if ".." in normalized_parts:
                        raise PluginLoadError(f"Zip contains unsafe path: {member}")
                    safe_member = "/".join(normalized_parts)
                    if safe_member in seen_members:
                        raise PluginLoadError(f"Zip contains duplicate entry: {member}")
                    seen_members.add(safe_member)

                    if info.file_size > self._zip_max_member_bytes:
                        raise PluginLoadError(
                            f"Zip entry too large: {member} ({info.file_size} bytes)"
                        )
                    if info.compress_size > 0:
                        ratio = info.file_size / max(info.compress_size, 1)
                        if ratio > self._zip_max_compression_ratio and info.file_size > 1024 * 1024:
                            raise PluginLoadError(
                                f"Zip entry compression ratio too high: {member} ({ratio:.1f}x)"
                            )
                    total_size += info.file_size
                    if total_size > self._zip_max_uncompressed_bytes:
                        raise PluginLoadError(
                            f"Zip uncompressed size too large: {total_size} bytes "
                            f"(max {self._zip_max_uncompressed_bytes})"
                        )

                    mode = info.external_attr >> 16
                    if stat.S_ISLNK(mode):
                        raise PluginLoadError(f"Zip contains symlink entry: {member}")

                zf.extractall(extract_root)
        except Exception as e:
            raise PluginLoadError(f"Failed to extract zip: {e}") from e

        return extract_root

    def _find_plugin_manifest(self, root: Path) -> Path:
        direct = root / "plugin.toml"
        if direct.exists():
            return direct

        candidates = list(root.rglob("plugin.toml"))
        if not candidates:
            raise PluginLoadError(f"plugin.toml not found under {root}")
        candidates.sort(key=lambda candidate: len(candidate.relative_to(root).parts))
        return candidates[0]

    def _install_plugin_path(
        self,
        source_root: Path,
        plugin_id: str,
        *,
        keep_backup: bool = False,
    ) -> tuple[Path, Path | None]:
        target = self.plugins_dir / plugin_id
        source_root = source_root.resolve()
        staging = target.parent / f".{plugin_id}.install_staging.{uuid.uuid4().hex}"
        backup = target.parent / f".{plugin_id}.backup.{uuid.uuid4().hex}"
        preserved_backup: Path | None = None

        try:
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if target.exists():
                shutil.move(str(target), str(backup))
                preserved_backup = backup
            if not source_root.exists():
                raise PluginLoadError(f"Plugin source not found after extraction: {source_root}")
            shutil.move(str(source_root), str(staging))
            shutil.move(str(staging), str(target))
            if preserved_backup is not None and not keep_backup:
                shutil.rmtree(backup, ignore_errors=True)
                preserved_backup = None
            return target, preserved_backup
        except Exception as e:
            if preserved_backup is not None:
                restored_from_backup = False
                with suppress(Exception):
                    if target.exists():
                        shutil.rmtree(target, ignore_errors=True)
                    shutil.move(str(preserved_backup), str(target))
                    restored_from_backup = True
                if restored_from_backup:
                    preserved_backup = None
            elif staging.exists():
                with suppress(Exception):
                    # Try to restore extracted content to the original location for diagnosability
                    shutil.move(str(staging), str(source_root))
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if not keep_backup and preserved_backup is not None and preserved_backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
                preserved_backup = None
            raise PluginLoadError(f"Plugin installation failed: {plugin_id}") from e

        finally:
            # Keep a clean temp space in all paths.
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)
            if not keep_backup and preserved_backup is not None:
                shutil.rmtree(preserved_backup, ignore_errors=True)

    def validate_plugin(
        self,
        plugin_path: Path,
        check_signature: bool = True,
        require_signature: bool = False,
    ) -> tuple[ValidationResult, str | None]:
        """
        Validate a plugin.

        Args:
            plugin_path: Path to plugin directory
            check_signature: Whether to verify signature
            require_signature: Whether an absent signature is invalid

        Returns:
            Tuple of (result, error_message)
        """
        return self.validator.validate_plugin(
            plugin_path,
            check_signature=check_signature,
            require_signature=require_signature,
        )

    def load_plugin(
        self,
        plugin_path: Path,
        manifest: PluginManifest,
        granted_permissions: dict[str, bool | list[str]] | None = None,
    ) -> PluginBase:
        """
        Load a validated plugin.

        Args:
            plugin_path: Path to plugin directory
            manifest: Plugin manifest
            granted_permissions: User-granted permissions

        Returns:
            Loaded plugin instance

        Raises:
            PluginLoadError: Failed to load plugin
        """
        # Check if already loaded
        if manifest.id in self.loaded_plugins:
            logger.info(f"Plugin {manifest.id} already loaded")
            return self.loaded_plugins[manifest.id]

        # Validate first
        result, error = self.validator.validate_plugin(plugin_path)
        if result != ValidationResult.VALID:
            raise PluginLoadError(f"Plugin validation failed: {error}")

        # Check permissions
        result, error = self.validator.check_permissions(manifest, granted_permissions or {})
        if result != ValidationResult.VALID:
            raise PluginLoadError(f"Permission check failed: {error}")

        # Check compatibility with existing plugins
        warnings = self.compatibility_checker.check_plugin_compatibility(
            manifest, list(self.plugin_manifests.values())
        )
        if warnings:
            logger.warning(f"Compatibility warnings for {manifest.id}: {warnings}")

        # Load the plugin module
        try:
            blocked_imports = self._get_blocked_imports(manifest)
            self._register_import_policy(manifest.id, blocked_imports)
            plugin_instance = self._load_plugin_module(plugin_path, manifest)

            # Store plugin
            self.loaded_plugins[manifest.id] = plugin_instance
            self.plugin_manifests[manifest.id] = manifest

            logger.info(f"Successfully loaded plugin: {manifest.id} v{manifest.version}")
            return plugin_instance

        except Exception as e:
            self._unregister_import_policy(manifest.id)
            raise PluginLoadError(f"Failed to load plugin {manifest.id}: {e}")  # noqa: B904

    def _load_plugin_module(self, plugin_path: Path, manifest: PluginManifest) -> PluginBase:
        """
        Load plugin module and create instance.

        Args:
            plugin_path: Path to plugin directory
            manifest: Plugin manifest

        Returns:
            Plugin instance

        Raises:
            PluginLoadError: Failed to load module
        """
        module_path = self._resolve_manifest_module_file(plugin_path, manifest.module)
        module_name = self._normalize_module_name(manifest.module)

        self._validate_plugin_source_tree(plugin_path, manifest)

        # Add plugin path to sys.path temporarily
        sys.path.insert(0, str(plugin_path))

        try:
            # Import the module
            spec = importlib.util.spec_from_file_location(
                f"plugins.{manifest.id}.{module_name}", module_path
            )

            if not spec or not spec.loader:
                raise PluginLoadError(f"Failed to load module spec: {manifest.module}")

            module = importlib.util.module_from_spec(spec)
            module.__dict__["__builtins__"] = self._sandboxed_builtins(manifest)

            # Add to sys.modules to handle imports within the plugin
            sys.modules[spec.name] = module

            # Execute the module
            spec.loader.exec_module(module)

            # Get the factory function
            factory = getattr(module, manifest.factory, None)
            if not factory:
                raise PluginLoadError(f"Factory function not found: {manifest.factory}")

            # Create plugin instance
            plugin_instance = factory()

            # Validate instance
            if not isinstance(plugin_instance, PluginBase):
                # For compatibility with different plugin types
                # wrap in adapter if needed
                plugin_instance = self._wrap_plugin(plugin_instance, manifest)

            return plugin_instance

        finally:
            # Remove plugin path from sys.path
            if str(plugin_path) in sys.path:
                sys.path.remove(str(plugin_path))

    @staticmethod
    def _normalize_module_name(module_name: str | None) -> str:
        """
        Normalize manifest.module into dotted module path.
        """
        if not module_name:
            raise PluginLoadError("Module name missing in plugin manifest")
        normalized = module_name.strip().replace("\\", ".").replace("/", ".")
        if not normalized:
            raise PluginLoadError("Invalid empty module name in plugin manifest")
        if normalized.startswith(".") or normalized.endswith("."):
            raise PluginLoadError(f"Invalid module name format: {module_name}")
        return normalized

    def _resolve_manifest_module_file(self, plugin_path: Path, module_name: str | None) -> Path:
        """
        Resolve plugin module source path without allowing path traversal.
        """
        normalized_module = self._normalize_module_name(module_name)
        parts = normalized_module.split(".")

        if any(
            part == ".." or not part.isidentifier() or keyword.iskeyword(part) for part in parts
        ):
            raise PluginLoadError(f"Invalid module path: {module_name}")

        for idx in range(1, len(parts) + 1):
            segment_path = plugin_path.joinpath(*parts[:idx])
            if segment_path.exists() and segment_path.is_symlink():
                raise PluginLoadError(
                    f"Invalid module path contains symlink segment: {segment_path}"
                )

        candidate = plugin_path.joinpath(*parts)
        module_path = candidate.with_suffix(".py")
        if module_path.exists():
            if module_path.is_symlink():
                raise PluginLoadError(f"Module path is symlink: {module_path}")
            return module_path

        init_path = candidate / "__init__.py"
        if init_path.exists():
            if init_path.is_symlink():
                raise PluginLoadError(f"Module path is symlink: {init_path}")
            return init_path

        raise PluginLoadError(f"Module not found: {module_name}")

    def _register_import_policy(self, plugin_id: str, blocked_imports: set[str]) -> None:
        """
        Register plugin-specific import restriction policy.
        """
        self._plugin_import_blocks[plugin_id] = blocked_imports
        self._update_import_guard()

    def _unregister_import_policy(self, plugin_id: str) -> None:
        """
        Remove plugin-specific import restriction policy.
        """
        self._plugin_import_blocks.pop(plugin_id, None)
        self._update_import_guard()

    def _update_import_guard(self) -> None:
        blocked = set()
        for plugin_block in self._plugin_import_blocks.values():
            blocked.update(plugin_block)

        if blocked:
            self._import_guard.set_blocked_roots(blocked)
            if not self._import_guard_active:
                sys.meta_path.insert(0, self._import_guard)
                self._import_guard_active = True
        elif self._import_guard_active:
            with suppress(ValueError):
                sys.meta_path.remove(self._import_guard)
            self._import_guard_active = False

    def _get_blocked_imports(self, manifest: PluginManifest) -> set[str]:
        """
        Return the root module names blocked for the plugin.
        """
        blocked_imports = set(self._always_blocked_imports)
        if not manifest.network:
            blocked_imports.update(self._network_only_imports)
        if not manifest.file_system:
            blocked_imports.update(self._filesystem_only_imports)
        return blocked_imports

    def _sandboxed_builtins(self, manifest: PluginManifest) -> dict[str, Any]:
        """
        Build a sandboxed builtins map for plugin execution.
        """
        blocked_imports = self._get_blocked_imports(manifest)

        original_import = py_builtins.__import__
        safe_builtins: dict[str, Any] = dict(
            (name, getattr(py_builtins, name))
            for name in dir(py_builtins)
            if name in py_builtins.__dict__ and not name.startswith("__")
        )
        # Remove dynamic introspection helpers that could bypass sandbox
        for name in ("getattr", "delattr", "vars"):
            safe_builtins.pop(name, None)
        if not manifest.file_system:
            for name in ("open", "input"):
                safe_builtins.pop(name, None)

        def guarded_import(
            name: str, globals: Any = None, locals: Any = None, fromlist: Any = (), level: int = 0
        ):
            root = name.split(".", 1)[0]
            if root in blocked_imports:
                raise PluginLoadError(f"Import blocked by plugin sandbox: {name}")
            return original_import(name, globals, locals, fromlist, level)

        safe_builtins["__import__"] = guarded_import
        safe_builtins["__build_class__"] = py_builtins.__build_class__
        return safe_builtins

    def _validate_plugin_source(self, source_file: Path, manifest: PluginManifest) -> None:
        """
        Perform lightweight static validation before executing plugin source.
        """
        source = source_file.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(source, filename=str(source_file))
        except SyntaxError as exc:
            raise PluginLoadError(f"Syntax error in plugin source: {exc}") from exc

        blocked_imports = self._get_blocked_imports(manifest)

        blocked_calls = {"exec", "eval", "compile", "__import__"}
        # Dynamic attribute access helpers that can bypass sandbox
        blocked_calls.update({"getattr", "delattr", "vars"})
        if not manifest.file_system:
            blocked_calls.add("open")

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".", 1)[0]
                    if root in blocked_imports:
                        raise PluginLoadError(
                            f"Plugin import blocked by manifest/policy: {alias.name}"
                        )
            elif isinstance(node, ast.ImportFrom):
                name = node.module or ""
                if node.level == 0 and name:
                    root = name.split(".", 1)[0]
                    if root in blocked_imports:
                        raise PluginLoadError(f"Plugin import blocked by manifest/policy: {name}")
                elif node.level > 0 and name:
                    # Check relative imports for blocked module names
                    root = name.split(".", 1)[0]
                    if root in blocked_imports:
                        raise PluginLoadError(
                            f"Plugin relative import blocked by manifest/policy: {name}"
                        )
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id in blocked_calls:
                    raise PluginLoadError(
                        f"Plugin source uses prohibited built-in call: {node.func.id}"
                    )
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "importlib"
                and node.func.attr == "import_module"
            ):
                raise PluginLoadError(
                    "Plugin source uses prohibited dynamic importlib import_module"
                )

        if os.environ.get("LOOMSTROKE_PLUGIN_SOURCE_AUDIT") == "1":
            logger.info("Plugin source validated by sandbox policy: %s", source_file)

    def _validate_plugin_source_tree(self, plugin_path: Path, manifest: PluginManifest) -> None:
        """
        Validate all Python sources in plugin package for sandbox compatibility.
        """
        for source_file in sorted(plugin_path.rglob("*.py")):
            parts = source_file.relative_to(plugin_path).parts
            if "__pycache__" in parts:
                continue
            self._validate_plugin_source(source_file, manifest)

    def _wrap_plugin(self, plugin: Any, manifest: PluginManifest) -> PluginBase:
        """
        Wrap a non-standard plugin in an adapter.

        Args:
            plugin: Plugin instance
            manifest: Plugin manifest

        Returns:
            Wrapped plugin
        """
        # Create adapter based on plugin type
        from .base import PluginAdapter  # noqa: PLC0415

        return PluginAdapter(plugin, manifest)

    def unload_plugin(self, plugin_id: str) -> bool:
        """
        Unload a plugin.

        Args:
            plugin_id: Plugin ID to unload

        Returns:
            True if unloaded successfully
        """
        if plugin_id not in self.loaded_plugins:
            logger.warning(f"Plugin not loaded: {plugin_id}")
            return False

        try:
            plugin = self.loaded_plugins[plugin_id]

            # Call cleanup if available
            if hasattr(plugin, "cleanup"):
                plugin.cleanup()

            # Remove from loaded plugins
            del self.loaded_plugins[plugin_id]
            del self.plugin_manifests[plugin_id]

            # Remove from sys.modules
            module_prefix = f"plugins.{plugin_id}"
            modules_to_remove = [name for name in sys.modules if name.startswith(module_prefix)]
            for name in modules_to_remove:
                del sys.modules[name]

            self._unregister_import_policy(plugin_id)
            logger.info(f"Unloaded plugin: {plugin_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to unload plugin {plugin_id}: {e}")
            return False

    def reload_plugin(self, plugin_id: str) -> PluginBase | None:
        """
        Reload a plugin.

        Args:
            plugin_id: Plugin ID to reload

        Returns:
            Reloaded plugin instance or None
        """
        if plugin_id not in self.plugin_manifests:
            logger.error(f"Cannot reload unknown plugin: {plugin_id}")
            return None

        manifest = self.plugin_manifests[plugin_id]

        # Find plugin path
        plugin_path = None
        for item in self.plugins_dir.iterdir():
            if item.is_dir():
                manifest_path = item / "plugin.toml"
                if manifest_path.exists():
                    try:
                        test_manifest = PluginManifest.from_toml(manifest_path)
                        if test_manifest.id == plugin_id:
                            plugin_path = item
                            break
                    except Exception:
                        logger.debug("plugin manifest probe failed", exc_info=True)

        if not plugin_path:
            logger.error(f"Cannot find plugin path for: {plugin_id}")
            return None

        # Unload first
        self.unload_plugin(plugin_id)

        # Reload
        try:
            return self.load_plugin(plugin_path, manifest)
        except Exception as e:
            logger.error(f"Failed to reload plugin {plugin_id}: {e}")
            return None

    def get_plugin(self, plugin_id: str) -> PluginBase | None:
        """
        Get a loaded plugin by ID.

        Args:
            plugin_id: Plugin ID

        Returns:
            Plugin instance or None
        """
        return self.loaded_plugins.get(plugin_id)

    def list_loaded_plugins(self) -> list[str]:
        """
        List all loaded plugin IDs.

        Returns:
            List of plugin IDs
        """
        return list(self.loaded_plugins.keys())

    def get_plugin_info(self, plugin_id: str) -> PluginManifest | None:
        """
        Get plugin manifest information.

        Args:
            plugin_id: Plugin ID

        Returns:
            Plugin manifest or None
        """
        return self.plugin_manifests.get(plugin_id)
