"""Strict, retryable migration of the legacy encrypted credential file."""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from .credential_migration_notice import report_migration_failure
from .legacy_credential_file import (
    LegacyCredentialFile,
    assert_legacy_source,
    read_legacy_file,
    replace_legacy_file,
)

logger = logging.getLogger(__name__)


class LegacyCredentialMigrationError(Exception):
    """A fixed failure category, never an exception or credential value."""

    def __init__(self, reason: str, *, removed_any: bool = False) -> None:
        super().__init__(reason)
        self.reason = reason
        self.removed_any = removed_any


def preserve_legacy_credentials(app_name: str, reason: str) -> None:
    logger.warning("Legacy credential data retained (%s)", reason)
    report_migration_failure(app_name, reason)


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LegacyCredentialMigrationError("invalid_structure")
        result[key] = value
    return result


def decode_legacy_credentials(encrypted: str, fernet: Any) -> dict[str, dict[str, str]]:
    """Decode completely before any keyring write or legacy file mutation."""
    if fernet is None:
        raise LegacyCredentialMigrationError("encryption_unavailable")
    try:
        ciphertext = base64.b64decode(encrypted.encode("ascii"), altchars=b"-_", validate=True)
        plaintext = fernet.decrypt(ciphertext).decode("utf-8")
        values = json.loads(plaintext, object_pairs_hook=_unique_object)
    except LegacyCredentialMigrationError:
        raise
    except Exception:
        raise LegacyCredentialMigrationError("decode_failed") from None
    if not isinstance(values, dict):
        raise LegacyCredentialMigrationError("invalid_structure")
    for service, users in values.items():
        if not isinstance(service, str) or not isinstance(users, dict):
            raise LegacyCredentialMigrationError("invalid_structure")
        if any(
            not isinstance(user, str) or not isinstance(value, str) for user, value in users.items()
        ):
            raise LegacyCredentialMigrationError("invalid_structure")
    return cast(dict[str, dict[str, str]], values)


def encode_legacy_credentials(credentials: dict[str, dict[str, str]], fernet: Any) -> str:
    try:
        encrypted = fernet.encrypt(json.dumps(credentials).encode("utf-8"))
        return base64.urlsafe_b64encode(encrypted).decode("ascii")
    except Exception:
        raise LegacyCredentialMigrationError("cleanup_failed") from None


def _verified_key_source(owner: Any, path: Path) -> LegacyCredentialFile | None:
    if owner._fernet is None:
        raise LegacyCredentialMigrationError("encryption_unavailable")
    source = read_legacy_file(path)
    if getattr(owner, "_legacy_key_origin", "disk") == "environment":
        # The existing explicit environment key takes priority over a disk key.
        return source
    if source is None:
        raise LegacyCredentialMigrationError("encryption_unavailable")
    try:
        from cryptography.fernet import Fernet  # noqa: PLC0415

        probe = b"LoomStroke legacy credential key consistency"
        encrypted_probe = owner._fernet.encrypt(probe)
        if Fernet(source.encrypted).decrypt(encrypted_probe) != probe:
            raise ValueError
    except Exception:
        raise LegacyCredentialMigrationError("encryption_unavailable") from None
    return source


def _verify_entries(app_name: str, credentials: dict[str, dict[str, str]]) -> None:
    try:
        import keyring  # noqa: PLC0415
    except Exception:
        raise LegacyCredentialMigrationError("keyring_unavailable") from None
    for service, users in credentials.items():
        for username, value in users.items():
            # Empty values were treated as cleared by the legacy store.
            if not value:
                continue
            try:
                existing = keyring.get_password(f"{app_name}.{service}", username)
            except Exception:
                raise LegacyCredentialMigrationError("keyring_lookup_failed") from None
            if existing is not None and existing != "":
                if existing != value:
                    raise LegacyCredentialMigrationError("keyring_conflict")
                continue
            try:
                keyring.set_password(f"{app_name}.{service}", username, value)
            except Exception:
                raise LegacyCredentialMigrationError("keyring_write_failed") from None
            try:
                verified = keyring.get_password(f"{app_name}.{service}", username)
            except Exception:
                raise LegacyCredentialMigrationError("keyring_verify_failed") from None
            if verified != value:
                raise LegacyCredentialMigrationError("keyring_verify_failed")


def primary_credential_was_absent(
    keyring_module: Any, error: Exception, service: str, username: str
) -> bool:
    """Confirm an idempotent delete result without hiding other backend failures."""
    error_type = getattr(getattr(keyring_module, "errors", None), "PasswordDeleteError", None)
    if not isinstance(error_type, type) or not isinstance(error, error_type):
        return False
    try:
        value = keyring_module.get_password(service, username)
    except Exception:
        return False
    absent = value is None or value == ""
    del value
    return absent


def _remove_verified_files(
    credential_file: Path,
    key_file: Path,
    credential_source: LegacyCredentialFile | None,
    key_source: LegacyCredentialFile | None,
) -> bool:
    removed = False
    assert_legacy_source(credential_file, credential_source)
    assert_legacy_source(key_file, key_source)
    for source in (credential_source, key_source):
        try:
            if source is not None:
                source.assert_current()
                source.path.unlink()
                removed = True
        except OSError:
            # Do not remove the key after a ciphertext deletion failure.
            raise LegacyCredentialMigrationError("cleanup_failed", removed_any=removed) from None
    return removed


def migrate_legacy_credentials(owner: Any) -> bool:
    """Return the existing 'any file removed' bool; preserve failures for retry."""
    try:
        credential_file = owner._get_credential_file_path()
        key_file = owner._get_key_file_path()
        credential_source = read_legacy_file(credential_file)
        key_source = read_legacy_file(key_file)
        if not owner._keyring_backend:
            if credential_source is not None:
                preserve_legacy_credentials(owner.app_name, "keyring_unavailable")
            return False
        if credential_source is None and key_source is None:
            return False
        if credential_source is not None:
            key_source = _verified_key_source(owner, key_file)
            try:
                encrypted = credential_source.encrypted.decode("utf-8")
            except UnicodeError:
                raise LegacyCredentialMigrationError("read_failed") from None
            credentials = decode_legacy_credentials(encrypted, owner._fernet)
            _verify_entries(owner.app_name, credentials)
        removed = _remove_verified_files(credential_file, key_file, credential_source, key_source)
    except LegacyCredentialMigrationError as failure:
        preserve_legacy_credentials(owner.app_name, failure.reason)
        return failure.removed_any
    except Exception:
        preserve_legacy_credentials(owner.app_name, "read_failed")
        return False
    owner._fernet = None
    if removed:
        logger.info("Verified legacy credential migration cleanup completed")
    return removed


def remove_legacy_credential(
    owner: Any, service: str, username: str, *, restrict_file_acl: Callable[[Path], None]
) -> bool:
    """Apply an explicit cleanup without confusing invalid data with an empty store."""
    try:
        path = owner._get_credential_file_path()
        source = read_legacy_file(path)
        if source is None:
            return True
        key_path = owner._get_key_file_path()
        key_source = _verified_key_source(owner, key_path)
        credentials = decode_legacy_credentials(source.encrypted.decode("utf-8"), owner._fernet)
        users = credentials.get(service, {})
        if username not in users:
            source.assert_current()
            return True
        del users[username]
        if not users:
            del credentials[service]
        if credentials:
            encoded = encode_legacy_credentials(credentials, owner._fernet).encode("ascii")
            replace_legacy_file(
                path,
                encoded,
                expected=source,
                key_path=key_path,
                expected_key=key_source,
                restrict_file_acl=restrict_file_acl,
            )
        else:
            source.assert_current()
            assert_legacy_source(key_path, key_source)
            path.unlink()
        return True
    except LegacyCredentialMigrationError as failure:
        preserve_legacy_credentials(owner.app_name, failure.reason)
    except Exception:
        preserve_legacy_credentials(owner.app_name, "cleanup_failed")
    return False


def store_legacy_credential(
    owner: Any,
    service: str,
    username: str,
    value: str,
    *,
    restrict_file_acl: Callable[[Path], None],
) -> bool:
    """Keep the explicitly enabled file store strict and atomic as well."""
    try:
        path = owner._get_credential_file_path()
        source = read_legacy_file(path)
        key_path = owner._get_key_file_path()
        key_source = _verified_key_source(owner, key_path)
        credentials = (
            decode_legacy_credentials(source.encrypted.decode("utf-8"), owner._fernet)
            if source is not None
            else {}
        )
        credentials.setdefault(service, {})[username] = value
        encoded = encode_legacy_credentials(credentials, owner._fernet).encode("ascii")
        replace_legacy_file(
            path,
            encoded,
            expected=source,
            key_path=key_path,
            expected_key=key_source,
            restrict_file_acl=restrict_file_acl,
        )
        return True
    except LegacyCredentialMigrationError as failure:
        preserve_legacy_credentials(owner.app_name, failure.reason)
    except Exception:
        preserve_legacy_credentials(owner.app_name, "cleanup_failed")
    return False
