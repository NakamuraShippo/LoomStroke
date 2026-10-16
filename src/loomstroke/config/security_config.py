"""Security configuration and credential management."""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import sys
from pathlib import Path
from typing import Any, cast

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC as PBKDF2
except Exception:
    Fernet = None  # type: ignore[assignment,misc]  # optional cryptography import fallback
    hashes = None  # type: ignore[assignment]  # optional cryptography import fallback
    PBKDF2 = None  # type: ignore[assignment,misc]  # optional cryptography import fallback

logger = logging.getLogger(__name__)

_ALLOW_FILE_CREDENTIAL_FALLBACK_ENV = "LOOMSTROKE_ALLOW_FILE_CREDENTIAL_FALLBACK"


def _file_credential_fallback_allowed() -> bool:
    """Return True only for explicit test / emergency fallback opt-in."""
    return os.environ.get(_ALLOW_FILE_CREDENTIAL_FALLBACK_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _restrict_file_acl(filepath: Path) -> None:
    """Restrict file permissions to the current user only (cross-platform)."""
    if sys.platform != "win32":
        os.chmod(filepath, 0o600)
    else:
        try:
            import subprocess  # noqa: PLC0415

            subprocess.run(  # noqa: S603
                ["icacls", str(filepath), "/inheritance:r", "/grant:r", f"{os.getlogin()}:F"],
                capture_output=True,
                check=False,
            )
        except Exception:
            logger.warning("Failed to set Windows ACL on %s", filepath)


class SecurityConfig:
    """Security configuration and credential management.

    Provides encryption/decryption for sensitive data and
    platform-specific secure storage integration.
    """

    def __init__(self, app_name: str = "LoomStroke"):
        """Initialize security configuration.

        Args:
            app_name: Application name for key derivation

        Phase 8a security note (2026-04-29): the keyring backend is
        probed FIRST so the encryption fallback only fires when there
        is no DPAPI / Keychain / Secret Service to lean on. This
        prevents the constructor from creating a fresh ``.key`` file
        on every boot when keyring is the live store — avoiding the
        original Critical-#1 leak (co-located key + ciphertext) from
        re-establishing itself after each ``purge_local_fallback``
        cleanup.
        """
        self.app_name = app_name
        self._fernet: Fernet | None = None
        self._keyring_backend: Any | None = None

        # Probe the OS keyring first.
        self._initialize_keyring()

        # Initialize the legacy encryption helper. It no longer acts
        # as the normal credential store; paid API keys require the OS
        # keyring. The helper remains available to migrate or delete
        # old ``.credentials`` files.
        self._initialize_encryption()
        self.purge_local_fallback_if_keyring_active()

    def _initialize_encryption(self) -> None:
        """Initialize encryption system.

        When keyring is available we only LOAD an existing ``.key``
        (for legacy migration / decrypt of a leftover ``.credentials``);
        we do NOT generate a new one. When keyring is unavailable,
        file credential storage is disabled by default so a missing
        runtime dependency cannot silently reintroduce the co-located
        Fernet key leak. Tests or emergency maintenance can opt in
        with ``LOOMSTROKE_ALLOW_FILE_CREDENTIAL_FALLBACK=1``.
        """
        if Fernet is None:
            logger.warning("Encryption is unavailable (cryptography not installed)")
            return

        if self._keyring_backend:
            key = self._load_existing_key()
            if key:
                self._fernet = Fernet(key)
                logger.info("Encryption initialized (loaded legacy key for migration)")
            return

        if not _file_credential_fallback_allowed():
            key = self._load_existing_key()
            if key:
                self._fernet = Fernet(key)
                logger.info("Encryption initialized (loaded legacy key for cleanup)")
            else:
                logger.info("Encryption file fallback disabled (OS keyring required)")
            return

        # Explicit opt-in only — fall back to the encrypted-file path.
        key = self._get_or_create_key()
        if key:
            self._fernet = Fernet(key)
            logger.warning("Encryption initialized (explicit file credential fallback)")
        else:
            logger.warning("Encryption not available")

    def _load_existing_key(self) -> bytes | None:
        """Return the on-disk Fernet key if present, else ``None``.

        Mirrors the read paths of :meth:`_get_or_create_key` but never
        creates a new key — used when keyring is the primary store.
        """
        if Fernet is None:
            return None
        env_key = os.environ.get("LOOMSTROKE_KEY")
        if env_key:
            try:
                return base64.urlsafe_b64decode(env_key.encode())
            except Exception:
                logger.warning("Invalid encryption key in environment")
        key_file = self._get_key_file_path()
        if key_file.exists():
            try:
                with open(key_file, "rb") as f:
                    return f.read()
            except OSError as e:
                logger.error(f"Failed to read key file: {e}")
        return None

    def _get_or_create_key(self) -> bytes | None:
        """Get or create encryption key.

        Returns:
            Encryption key as bytes or None
        """
        if Fernet is None:
            logger.warning("Encryption key generation skipped: cryptography not installed")
            return None

        # Try to get from environment variable first
        env_key = os.environ.get("LOOMSTROKE_KEY")
        if env_key:
            try:
                return base64.urlsafe_b64decode(env_key.encode())
            except Exception:
                logger.warning("Invalid encryption key in environment")

        # Try to get from user config directory
        key_file = self._get_key_file_path()
        if key_file.exists():
            try:
                with open(key_file, "rb") as f:
                    return f.read()
            except OSError as e:
                logger.error(f"Failed to read key file: {e}")

        # Generate new key
        key = Fernet.generate_key()

        # Try to save it
        try:
            key_file.parent.mkdir(parents=True, exist_ok=True)
            with open(key_file, "wb") as f:
                f.write(key)

            _restrict_file_acl(key_file)

            logger.info("New encryption key created")
            return key

        except OSError as e:
            logger.error(f"Failed to save key file: {e}")
            # Return key anyway for session use
            return key

    def _get_key_file_path(self) -> Path:
        """Get path to key file.

        Returns:
            Path to key file
        """
        if sys.platform == "win32":
            base_path = Path(os.environ.get("APPDATA", ""))
        elif sys.platform == "darwin":
            base_path = Path.home() / "Library" / "Application Support"
        else:  # Linux and others
            base_path = Path.home() / ".config"

        return base_path / self.app_name / ".key"

    def _initialize_keyring(self) -> None:
        """Initialize system keyring if available."""
        try:
            import keyring  # noqa: PLC0415
            from keyring.backends import SecretService, Windows, macOS  # noqa: PLC0415

            # Try to get appropriate backend
            if sys.platform == "win32":
                self._keyring_backend = cast(Any, Windows.WinVaultKeyring)()
            elif sys.platform == "darwin":
                self._keyring_backend = macOS.Keyring()
            else:
                # Linux - try SecretService (GNOME Keyring, KWallet)
                try:
                    self._keyring_backend = SecretService.Keyring()
                except Exception:
                    logger.info("SecretService not available")

            if self._keyring_backend:
                keyring.set_keyring(self._keyring_backend)
                logger.info(f"Keyring backend initialized: {type(self._keyring_backend).__name__}")

        except ImportError:
            logger.info("Keyring module not available")
        except Exception as e:
            logger.warning(f"Failed to initialize keyring: {e}")

    # Encryption Methods

    def encrypt(self, data: str) -> str | None:
        """Encrypt string data.

        Args:
            data: String to encrypt

        Returns:
            Encrypted data as base64 string, or ``None`` if encryption
            is unavailable or fails.
        """
        if not self._fernet:
            logger.warning("Encryption not available")
            return None

        try:
            encrypted = self._fernet.encrypt(data.encode())
            return base64.urlsafe_b64encode(encrypted).decode()
        except Exception as e:
            logger.error(f"Encryption failed: {e}")
            return None

    def decrypt(self, encrypted_data: str) -> str:
        """Decrypt string data.

        Args:
            encrypted_data: Base64 encrypted string

        Returns:
            Decrypted string
        """
        if not self._fernet:
            logger.warning("Decryption not available, returning data as-is")
            return encrypted_data

        try:
            encrypted_bytes = base64.urlsafe_b64decode(encrypted_data.encode())
            decrypted = self._fernet.decrypt(encrypted_bytes)
            return decrypted.decode()
        except Exception as e:
            logger.error(f"Decryption failed: {e}")
            return ""

    def encrypt_dict(self, data: dict[str, Any]) -> str | None:
        """Encrypt dictionary data.

        Args:
            data: Dictionary to encrypt

        Returns:
            Encrypted data as base64 string, or ``None`` on failure.
        """
        import json  # noqa: PLC0415

        json_str = json.dumps(data)
        return self.encrypt(json_str)

    def decrypt_dict(self, encrypted_data: str) -> dict[str, Any]:
        """Decrypt dictionary data.

        Args:
            encrypted_data: Encrypted dictionary as base64 string

        Returns:
            Decrypted dictionary
        """
        import json  # noqa: PLC0415

        json_str = self.decrypt(encrypted_data)
        if json_str:
            try:
                return cast("dict[str, Any]", json.loads(json_str))
            except json.JSONDecodeError:
                return {}
        return {}

    # Keyring Methods

    def store_credential(self, service: str, username: str, password: str) -> bool:
        """Store credential in system keyring.

        Phase 8a security note (2026-04-29): when the keyring backend
        is available AND the keyring write succeeds, the encrypted
        Fernet fallback is NOT also written. The previous "double
        write for safety" caused the same plaintext-equivalent secret
        to land in ``.credentials`` next to its ``.key`` (the Fernet
        key being co-located on disk meant any same-user process
        could decrypt). The fallback path now activates only when
        keyring is unavailable or its write fails — i.e. when there
        is no DPAPI / Keychain / Secret Service to lean on.

        Additionally, when the keyring write succeeds, any pre-
        existing credential in the Fernet fallback for the same
        ``(service, username)`` pair is purged so a stale plaintext-
        equivalent copy does not persist.

        Args:
            service: Service name (e.g., "comfyui")
            username: Username
            password: Password

        Returns:
            True if successful
        """
        stored_in_keyring = False
        if self._keyring_backend:
            try:
                import keyring  # noqa: PLC0415

                keyring.set_password(f"{self.app_name}.{service}", username, password)
                logger.info(f"Credential stored for {service}")
                stored_in_keyring = True
            except Exception as e:
                logger.error(f"Failed to store credential in keyring: {e}")

        if stored_in_keyring:
            # Keyring (DPAPI / Keychain / Secret Service) now owns the
            # value; remove any stale Fernet fallback entry so the same
            # secret is not also reachable via the co-located .key file.
            if self._fernet:
                try:
                    self._delete_credential_fallback(service, username)
                except Exception as exc:
                    logger.debug(
                        "Failed to purge stale Fernet fallback for %s/%s: %s",
                        service,
                        username,
                        exc,
                    )
            return True

        if _file_credential_fallback_allowed() and self._fernet:
            return self._store_credential_fallback(service, username, password)
        logger.error(
            "Cannot store credential for %s/%s: OS keyring is required",
            service,
            username,
        )
        return False

    def get_credential(self, service: str, username: str) -> str | None:
        """Get credential from system keyring.

        Phase 8a security note (2026-04-29): when the keyring backend
        is available, the Fernet fallback is NEVER consulted — even
        if keyring returns ``None`` for the lookup. Falling through to
        the encrypted file would let a stale entry shadow a freshly-
        deleted keyring credential, recovering a value the user
        explicitly cleared. The fallback path is reachable only on
        systems without keyring.

        Args:
            service: Service name
            username: Username

        Returns:
            Password or None
        """
        if self._keyring_backend:
            try:
                import keyring  # noqa: PLC0415

                password = keyring.get_password(f"{self.app_name}.{service}", username)
                return cast("str | None", password) if password else None
            except Exception as e:
                logger.error(f"Failed to get credential: {e}")
                return None

        if _file_credential_fallback_allowed():
            return self._get_credential_fallback(service, username)
        return None

    def has_credential(self, service: str, username: str) -> bool:
        """Check whether a credential exists without exposing its value.

        Phase 8a (NanoBanana) "read-on-demand" policy: UI surfaces
        such as "API key configured ✓" indicators need to know if a
        credential is set, but must NOT pull the value into Python
        memory longer than absolutely necessary. This helper reads
        the keyring just long enough to test for non-emptiness, then
        deletes the local binding so the password lives only inside
        the keyring backend (Windows Credential Manager / macOS
        Keychain / Linux Secret Service).

        The encrypted-fallback path inspects the credentials dict for
        membership without returning the value to the caller.

        Args:
            service: Service name (e.g. "nanobanana")
            username: Username (e.g. "api")

        Returns:
            True iff a non-empty credential is stored.
        """
        if self._keyring_backend:
            try:
                import keyring  # noqa: PLC0415

                password = keyring.get_password(f"{self.app_name}.{service}", username)
                present = bool(password)
                # Drop the local binding immediately so the caller
                # cannot inadvertently retain the secret. Python str
                # is immutable so the underlying bytes may linger
                # until GC, but no Python-level reference remains.
                del password
                return present
            except Exception as e:
                logger.debug(f"has_credential keyring lookup failed: {e}")
                # Don't fall through to the Fernet fallback when
                # keyring is the primary store — a stale fallback
                # entry could shadow a cleared keyring credential.
                return False

        if _file_credential_fallback_allowed():
            return self._has_credential_fallback(service, username)
        return False

    def list_credentials(self, service: str) -> set[str]:
        """List stored credential usernames for a service.

        Uses encrypted file fallback when available.
        """
        if not self._fernet or not _file_credential_fallback_allowed():
            return set()
        try:
            cred_file = self._get_credential_file_path()
            if not cred_file.exists():
                return set()
            with open(cred_file) as f:
                encrypted = f.read()
                if not encrypted:
                    return set()

            credentials = self.decrypt_dict(encrypted)
            users = credentials.get(service, {})
            if isinstance(users, dict):
                return {str(username) for username in users if username}
            return set()
        except Exception as e:
            logger.error(f"Failed to list credentials for {service}: {e}")
            return set()

    def delete_credential(self, service: str, username: str) -> bool:
        """Delete credential from system keyring.

        Args:
            service: Service name
            username: Username

        Returns:
            True if successful
        """
        deleted_in_keyring = False
        if self._keyring_backend:
            try:
                import keyring  # noqa: PLC0415

                keyring.delete_password(f"{self.app_name}.{service}", username)
                logger.info(f"Credential deleted for {service}")
                deleted_in_keyring = True
            except Exception as e:
                logger.error(f"Failed to delete credential from keyring: {e}")

        if not self._fernet:
            return deleted_in_keyring

        fallback_deleted = self._delete_credential_fallback(service, username)
        return fallback_deleted or deleted_in_keyring

    # Fallback Methods

    def _store_credential_fallback(self, service: str, username: str, password: str) -> bool:
        """Store credential in encrypted file as fallback.

        Args:
            service: Service name
            username: Username
            password: Password

        Returns:
            True if successful
        """
        if not self._fernet:
            return False

        try:
            cred_file = self._get_credential_file_path()

            # Load existing credentials
            credentials = {}
            if cred_file.exists():
                with open(cred_file) as f:
                    encrypted = f.read()
                    if encrypted:
                        credentials = self.decrypt_dict(encrypted)

            # Add new credential
            if service not in credentials:
                credentials[service] = {}
            credentials[service][username] = password

            # Save encrypted
            new_encrypted = self.encrypt_dict(credentials)
            if new_encrypted is None:
                logger.error("Cannot store credential: encryption unavailable")
                return False
            cred_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cred_file, "w") as f:
                f.write(new_encrypted)

            _restrict_file_acl(cred_file)

            self._cleanup_legacy_plaintext_files()
            return True

        except Exception as e:
            logger.error(f"Failed to store credential (fallback): {e}")
            return False

    def _cleanup_legacy_plaintext_files(self) -> None:
        """Remove legacy plaintext credential files if they exist.

        Called after successful encrypted credential storage to ensure
        no plaintext secrets remain on disk.
        """
        legacy_names = [".api_keys", ".api_key", "api_keys.json"]
        cred_dir = self._get_credential_file_path().parent
        for name in legacy_names:
            legacy_file = cred_dir / name
            if legacy_file.is_file():
                try:
                    legacy_file.unlink()
                    logger.info("Removed legacy plaintext file: %s", name)
                except OSError as e:
                    logger.warning("Failed to remove legacy file %s: %s", name, e)

    def _has_credential_fallback(self, service: str, username: str) -> bool:
        """Encrypted-file existence check that does not return the value.

        The encrypted file MUST be decrypted to inspect its dict
        structure (the value is naturally exposed in the decrypted
        ``credentials`` dict), but the lookup result is collapsed to
        a bool before the function returns so callers cannot retain
        the value through this code path.
        """
        if not self._fernet:
            return False
        try:
            cred_file = self._get_credential_file_path()
            if not cred_file.exists():
                return False
            with open(cred_file) as f:
                encrypted = f.read()
            if not encrypted:
                return False
            credentials = self.decrypt_dict(encrypted)
            users = credentials.get(service, {})
            if not isinstance(users, dict):
                return False
            value = users.get(username)
            present = bool(value)
            # Defensive cleanup — strip the secret from the dict copy
            # before returning so it is not retained by tracebacks or
            # the local frame's debugger inspector.
            try:
                if username in users:
                    users[username] = ""
                credentials.clear()
            except Exception:
                pass
            return present
        except Exception as e:
            logger.debug(f"_has_credential_fallback failed: {e}")
            return False

    def _get_credential_fallback(self, service: str, username: str) -> str | None:
        """Get credential from encrypted file as fallback.

        Args:
            service: Service name
            username: Username

        Returns:
            Password or None
        """
        if not self._fernet:
            return None

        try:
            cred_file = self._get_credential_file_path()
            if not cred_file.exists():
                return None

            with open(cred_file) as f:
                encrypted = f.read()
                if not encrypted:
                    return None

            credentials = self.decrypt_dict(encrypted)
            return cast("str | None", credentials.get(service, {}).get(username))

        except Exception as e:
            logger.error(f"Failed to get credential (fallback): {e}")
            return None

    def _delete_credential_fallback(self, service: str, username: str) -> bool:
        """Delete credential from encrypted file as fallback.

        Args:
            service: Service name
            username: Username

        Returns:
            True if successful
        """
        if not self._fernet:
            return False

        try:
            cred_file = self._get_credential_file_path()
            if not cred_file.exists():
                return True

            with open(cred_file) as f:
                encrypted = f.read()
                if not encrypted:
                    return True

            credentials = self.decrypt_dict(encrypted)

            # Remove credential
            if service in credentials and username in credentials[service]:
                del credentials[service][username]
                if not credentials[service]:
                    del credentials[service]

            # Save updated
            if credentials:
                reencrypted = self.encrypt_dict(credentials)
                if reencrypted is None:
                    logger.error("Cannot update credential file: encryption unavailable")
                    return False
                with open(cred_file, "w") as f:
                    f.write(reencrypted)
            else:
                # Delete file if empty
                cred_file.unlink()

            return True

        except Exception as e:
            logger.error(f"Failed to delete credential (fallback): {e}")
            return False

    def _get_credential_file_path(self) -> Path:
        """Get path to credential file.

        Returns:
            Path to credential file
        """
        key_file = self._get_key_file_path()
        return key_file.parent / ".credentials"

    def purge_local_fallback_if_keyring_active(self) -> bool:
        """Migrate any encrypted-fallback credentials into keyring,
        then delete the on-disk Fernet key + ciphertext.

        Phase 8a security follow-up (2026-04-29): the encrypted
        fallback file lives next to its Fernet key on disk, which
        means a same-user process can decrypt every credential in
        it. When the keyring backend is available there is no reason
        to keep that vulnerability around — we lift any entries that
        are NOT already in keyring, write them to keyring, and then
        delete both ``.credentials`` and the ``.key`` files outright.

        Returns ``True`` when files were removed, ``False`` when no
        action was needed (no keyring backend, or no fallback files
        present). Never raises — failures are logged at debug level
        because this runs at startup and must not block the app.
        """
        if not self._keyring_backend:
            return False

        cred_file = self._get_credential_file_path()
        key_file = self._get_key_file_path()
        if not cred_file.exists() and not key_file.exists():
            return False

        if cred_file.exists() and self._fernet:
            self._lift_fallback_entries_to_keyring(cred_file)

        removed = self._unlink_fallback_files(cred_file, key_file)
        self._fernet = None
        if removed:
            logger.info("Purged encrypted credential fallback files (keyring is primary)")
        return removed

    def _lift_fallback_entries_to_keyring(self, cred_file: Path) -> None:
        """Move ``.credentials`` entries into keyring (skip ones already there)."""
        try:
            with open(cred_file, encoding="utf-8") as fh:
                encrypted = fh.read()
            credentials = self.decrypt_dict(encrypted) if encrypted else {}
        except Exception as exc:
            logger.debug("Could not parse fallback credentials for migration: %s", exc)
            return
        try:
            import keyring  # noqa: PLC0415
        except Exception as exc:
            logger.debug("Keyring import failed during migration: %s", exc)
            return
        for service, users in credentials.items():
            if not isinstance(users, dict):
                continue
            for username, value in users.items():
                if not value:
                    continue
                self._migrate_one_to_keyring(keyring, service, username, value)

    def _migrate_one_to_keyring(
        self,
        keyring_module: Any,
        service: str,
        username: str,
        value: str,
    ) -> None:
        """Write a single fallback entry to keyring if not already present."""
        try:
            existing = keyring_module.get_password(f"{self.app_name}.{service}", username)
        except Exception as exc:
            logger.debug("Keyring lookup failed for %s/%s: %s", service, username, exc)
            return
        if existing:
            return
        try:
            keyring_module.set_password(f"{self.app_name}.{service}", username, value)
        except Exception as exc:
            logger.debug("Failed to migrate %s/%s to keyring: %s", service, username, exc)

    def _unlink_fallback_files(self, *paths: Path) -> bool:
        """Delete each fallback file. Returns ``True`` if any was removed."""
        removed = False
        for path in paths:
            try:
                if path.exists():
                    path.unlink()
                    removed = True
            except OSError as exc:
                logger.debug("Failed to remove %s: %s", path, exc)
        return removed

    # Security Validation

    def validate_url(self, url: str) -> bool:
        """Validate URL for security.

        Args:
            url: URL to validate

        Returns:
            True if valid and safe
        """
        # Basic validation
        if not url:
            return False

        # Check protocol
        allowed_protocols = ["http://", "https://", "ws://", "wss://"]
        return any(url.startswith(p) for p in allowed_protocols)

    def hash_password(self, password: str, salt: bytes | str | None = None) -> tuple[str, str]:
        """Hash password with salt.

        Args:
            password: Password to hash
            salt: Optional salt (generated if not provided)

        Returns:
            Tuple of (hashed_password, salt) as base64 strings
        """
        if salt is None:
            salt = os.urandom(32)
        else:
            salt = base64.urlsafe_b64decode(salt.encode() if isinstance(salt, str) else salt)

        # Use PBKDF2 for key derivation
        if hashes is not None and PBKDF2 is not None:
            kdf = PBKDF2(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=100000,
            )
            key = kdf.derive(password.encode())
        else:
            key = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 100000, dklen=32)

        hashed = base64.urlsafe_b64encode(key).decode()
        salt_str = base64.urlsafe_b64encode(salt).decode()

        return hashed, salt_str

    def verify_password(self, password: str, hashed: str, salt: str) -> bool:
        """Verify password against hash.

        Args:
            password: Password to verify
            hashed: Hashed password
            salt: Salt used for hashing

        Returns:
            True if password matches
        """
        try:
            import hmac  # noqa: PLC0415

            new_hash, _ = self.hash_password(password, salt)
            return hmac.compare_digest(new_hash, hashed)
        except Exception:
            return False
