"""
SCRUM-321: Authenticated Encryption Service for Integration Credentials & Webhook Secrets.

Features:
- Authenticated symmetric encryption: AES-256-GCM.
- Key rotation design: Ciphertext embeds key_id and format version.
- Secret masking & SecretString container to prevent leakage into logs or API responses.
- Resilient key parsing: Supports 32-byte raw bytes, 64-char hex strings, and Base64 strings.
- Keyring support for seamless zero-downtime key rotation.
"""

import base64
import json
import logging
import os
from typing import Any, Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

logger = logging.getLogger("app.integrations.crypto")

CIPHERTEXT_PREFIX = "enc:v1:"
NONCE_LENGTH = 12  # 96 bits recommended for AES-GCM
KEY_LENGTH = 32  # 256 bits


class CryptoError(Exception):
    """Base exception for cryptographic operations."""


class KeyNotFoundError(CryptoError):
    """Raised when the key_id embedded in ciphertext does not exist in the keyring."""


class DecryptionError(CryptoError):
    """Raised when ciphertext is corrupted, tampered with, or invalid."""


def parse_key_bytes(raw_key: str | bytes) -> bytes:
    """
    Parses a secret key representation into exactly 32 bytes for AES-256-GCM.
    Supports:
    - Exactly 32 bytes.
    - 64-character hexadecimal string.
    - Standard or URL-safe Base64 string of 32 bytes.
    - Fallback: zero-padded/truncated to 32 bytes.
    """
    if isinstance(raw_key, bytes):
        if len(raw_key) == KEY_LENGTH:
            return raw_key
        raw_key = raw_key.decode("utf-8", errors="ignore")

    raw_key = raw_key.strip()

    # 1. Try 64-character hex string
    if len(raw_key) == 64:
        try:
            k = bytes.fromhex(raw_key)
            if len(k) == KEY_LENGTH:
                return k
        except ValueError:
            pass

    # 2. Try Base64 decoding
    try:
        b64 = base64.b64decode(raw_key, validate=True)
        if len(b64) == KEY_LENGTH:
            return b64
    except Exception:
        pass

    try:
        b64_url = base64.urlsafe_b64decode(raw_key + "==")
        if len(b64_url) == KEY_LENGTH:
            return b64_url
    except Exception:
        pass

    # 3. Fallback: encode as utf-8 and pad/truncate to 32 bytes
    k_bytes = raw_key.encode("utf-8")
    if len(k_bytes) >= KEY_LENGTH:
        return k_bytes[:KEY_LENGTH]
    return k_bytes.ljust(KEY_LENGTH, b"\0")


class SecretString:
    """
    String wrapper that hides its value in __str__, __repr__, and logging.
    Plaintext is accessed exclusively via the .get_secret_value() method.
    """

    def __init__(self, secret: str) -> None:
        self._secret = str(secret)

    def get_secret_value(self) -> str:
        return self._secret

    def __repr__(self) -> str:
        return "SecretString('**********')"

    def __str__(self) -> str:
        return "**********"

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, SecretString):
            return self._secret == other._secret
        if isinstance(other, str):
            return self._secret == other
        return False

    def __hash__(self) -> int:
        return hash(self._secret)


def mask_secret(secret: str | None, visible_prefix: int = 4, visible_suffix: int = 4) -> str:
    """
    Returns a safe, masked representation of a secret for UI display or diagnostics.
    E.g. 'whsec_d47b59e21...3a2f' or '********'.
    """
    if not secret:
        return "********"
    s = str(secret)
    if len(s) <= (visible_prefix + visible_suffix + 2):
        return "********"
    return f"{s[:visible_prefix]}...{s[-visible_suffix:]}"


class SecretEncryptionService:
    """
    High-performance AES-256-GCM authenticated encryption service with key rotation support.

    Ciphertext wire format:
    enc:v1:{key_id}:{nonce_b64}:{ciphertext_and_tag_b64}
    """

    def __init__(
        self,
        keyring: Mapping[str, bytes | str] | None = None,
        active_key_id: str = "primary",
    ) -> None:
        self.keyring: dict[str, bytes] = {}
        self.active_key_id = active_key_id

        if keyring:
            for kid, raw in keyring.items():
                self.keyring[kid] = parse_key_bytes(raw)

        # Fallback to application settings if keyring is empty
        if not self.keyring:
            master_key = getattr(
                settings,
                "ENCRYPTION_MASTER_KEY",
                "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
            )
            self.keyring[self.active_key_id] = parse_key_bytes(master_key)

            # Check if additional keys exist in JSON keyring setting
            keyring_json = getattr(settings, "ENCRYPTION_KEYRING_JSON", None)
            if keyring_json:
                try:
                    loaded = json.loads(keyring_json)
                    if isinstance(loaded, dict):
                        for kid, raw in loaded.items():
                            self.keyring[kid] = parse_key_bytes(raw)
                except Exception as exc:
                    logger.warning(f"Failed to parse ENCRYPTION_KEYRING_JSON: {exc}")

        if self.active_key_id not in self.keyring:
            first_key_id = next(iter(self.keyring))
            self.active_key_id = first_key_id

    def encrypt(
        self,
        plaintext: str | bytes | SecretString,
        key_id: str | None = None,
        associated_data: bytes | None = None,
    ) -> str:
        """
        Encrypts plaintext with AES-256-GCM.
        Returns a formatted ciphertext string with embedded key_id and nonce.
        """
        target_kid = key_id or self.active_key_id
        if target_kid not in self.keyring:
            raise KeyNotFoundError(f"Active encryption key '{target_kid}' not found in keyring")

        key = self.keyring[target_kid]
        aesgcm = AESGCM(key)

        if isinstance(plaintext, SecretString):
            data = plaintext.get_secret_value().encode("utf-8")
        elif isinstance(plaintext, str):
            data = plaintext.encode("utf-8")
        else:
            data = plaintext

        nonce = os.urandom(NONCE_LENGTH)
        ciphertext_and_tag = aesgcm.encrypt(nonce, data, associated_data)

        nonce_b64 = base64.urlsafe_b64encode(nonce).decode("ascii")
        ct_b64 = base64.urlsafe_b64encode(ciphertext_and_tag).decode("ascii")

        return f"{CIPHERTEXT_PREFIX}{target_kid}:{nonce_b64}:{ct_b64}"

    def decrypt(
        self,
        ciphertext: str,
        associated_data: bytes | None = None,
    ) -> str:
        """
        Decrypts ciphertext formatted by this service.
        Automatically resolves the corresponding key_id from the keyring.
        """
        if not isinstance(ciphertext, str):
            raise DecryptionError("Ciphertext must be a string")

        if not ciphertext.startswith(CIPHERTEXT_PREFIX):
            raise DecryptionError(
                f"Invalid ciphertext format. Expected prefix '{CIPHERTEXT_PREFIX}'"
            )

        payload = ciphertext[len(CIPHERTEXT_PREFIX) :]
        parts = payload.split(":")
        if len(parts) != 3:
            raise DecryptionError(
                "Invalid ciphertext format. Expected format 'enc:v1:key_id:nonce:data'"
            )

        kid, nonce_b64, ct_b64 = parts
        if kid not in self.keyring:
            raise KeyNotFoundError(f"Decryption key '{kid}' not present in active keyring")

        key = self.keyring[kid]
        aesgcm = AESGCM(key)

        try:
            nonce = base64.urlsafe_b64decode(nonce_b64)
            ct = base64.urlsafe_b64decode(ct_b64)
            if len(nonce) != NONCE_LENGTH:
                raise DecryptionError(f"Invalid nonce length: {len(nonce)}")

            decrypted_bytes = aesgcm.decrypt(nonce, ct, associated_data)
            return decrypted_bytes.decode("utf-8")
        except InvalidTag as exc:
            logger.error(
                "Decryption failed: authenticated tag mismatch (data tampered or wrong key)"
            )
            raise DecryptionError("Authentication tag mismatch or corrupted ciphertext") from exc
        except Exception as exc:
            if isinstance(exc, (CryptoError, KeyNotFoundError, DecryptionError)):
                raise
            raise DecryptionError(f"Failed to decrypt ciphertext: {exc}") from exc

    def decrypt_secret(
        self,
        ciphertext: str,
        associated_data: bytes | None = None,
    ) -> SecretString:
        """Decrypts ciphertext and wraps in SecretString for safe internal handling."""
        raw = self.decrypt(ciphertext, associated_data)
        return SecretString(raw)

    def reencrypt_if_needed(self, ciphertext: str) -> tuple[str, bool]:
        """
        If ciphertext was encrypted with an older key_id, re-encrypts it with
        the current active_key_id. Returns (new_ciphertext, was_migrated).
        """
        if not ciphertext.startswith(CIPHERTEXT_PREFIX):
            return ciphertext, False

        payload = ciphertext[len(CIPHERTEXT_PREFIX) :]
        parts = payload.split(":")
        if len(parts) != 3:
            return ciphertext, False

        kid = parts[0]
        if kid != self.active_key_id:
            decrypted = self.decrypt(ciphertext)
            new_ciphertext = self.encrypt(decrypted, key_id=self.active_key_id)
            return new_ciphertext, True

        return ciphertext, False

    def is_encrypted(self, value: str) -> bool:
        """Checks if a string is in encrypted wire format."""
        return isinstance(value, str) and value.startswith(CIPHERTEXT_PREFIX)


# Module-level default singleton
encryption_service = SecretEncryptionService()
