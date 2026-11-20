"""
SCRUM-321: Test suite for SecretEncryptionService and credential security.

Verifies:
1. AES-256-GCM encryption and decryption round-trip.
2. Tamper-resistance (authentication tag verification and corruption handling).
3. SecretString and mask_secret leak prevention (repr, str, formatting).
4. Key rotation and re-encryption across keyrings.
5. Associated data (AAD) authentication.
6. Key parsing resilience (hex, Base64, raw bytes).
"""

import base64
import os
import pytest

from app.integrations.crypto import (
    DecryptionError,
    KeyNotFoundError,
    SecretEncryptionService,
    SecretString,
    mask_secret,
    parse_key_bytes,
)


def test_parse_key_bytes():
    # 32 raw bytes
    raw_32 = os.urandom(32)
    assert parse_key_bytes(raw_32) == raw_32

    # 64-char hex string
    hex_key = raw_32.hex()
    assert len(hex_key) == 64
    assert parse_key_bytes(hex_key) == raw_32

    # Base64 string
    b64_key = base64.b64encode(raw_32).decode("ascii")
    assert parse_key_bytes(b64_key) == raw_32

    # Short string with padding
    padded = parse_key_bytes("short-key")
    assert len(padded) == 32
    assert padded.startswith(b"short-key\x00")


def test_secret_string_representation():
    raw_secret = "ghp_super_secret_github_token_12345"
    sec = SecretString(raw_secret)

    # Values must be hidden in str and repr
    assert str(sec) == "**********"
    assert repr(sec) == "SecretString('**********')"
    assert f"Secret is: {sec}" == "Secret is: **********"
    assert f"{sec!r}" == "SecretString('**********')"

    # Explicit extraction works
    assert sec.get_secret_value() == raw_secret
    assert sec == raw_secret
    assert sec == SecretString(raw_secret)


def test_mask_secret():
    assert mask_secret(None) == "********"
    assert mask_secret("") == "********"
    assert mask_secret("short") == "********"
    assert mask_secret("1234567890") == "********"

    token = "whsec_d47b59e2193b2a5f78c9"
    masked = mask_secret(token, visible_prefix=6, visible_suffix=4)
    assert masked == "whsec_...78c9"
    assert "d47b59e219" not in masked


def test_aes_gcm_roundtrip():
    key = os.urandom(32).hex()
    service = SecretEncryptionService(keyring={"k1": key}, active_key_id="k1")

    plaintext = "webhook-signing-secret-value-xyz-987"
    ciphertext = service.encrypt(plaintext)

    assert ciphertext.startswith("enc:v1:k1:")
    assert plaintext not in ciphertext
    assert service.is_encrypted(ciphertext) is True
    assert service.is_encrypted("not-encrypted-plaintext") is False

    decrypted = service.decrypt(ciphertext)
    assert decrypted == plaintext


def test_aes_gcm_secret_string_input_and_output():
    key = os.urandom(32).hex()
    service = SecretEncryptionService(keyring={"k1": key}, active_key_id="k1")

    secret_input = SecretString("jira_oauth_client_secret_xyz")
    ciphertext = service.encrypt(secret_input)

    decrypted_secret = service.decrypt_secret(ciphertext)
    assert isinstance(decrypted_secret, SecretString)
    assert decrypted_secret.get_secret_value() == "jira_oauth_client_secret_xyz"
    assert str(decrypted_secret) == "**********"


def test_tampered_ciphertext_fails():
    key = os.urandom(32).hex()
    service = SecretEncryptionService(keyring={"k1": key}, active_key_id="k1")

    ciphertext = service.encrypt("sensitive_api_payload")
    enc, v, kid, nonce_b64, ct_b64 = ciphertext.split(":")

    # Tamper with the encrypted bytes
    raw_ct = bytearray(base64.urlsafe_b64decode(ct_b64))
    raw_ct[0] ^= 0xFF
    tampered_ct_b64 = base64.urlsafe_b64encode(raw_ct).decode("ascii")
    tampered_ciphertext = f"{enc}:{v}:{kid}:{nonce_b64}:{tampered_ct_b64}"

    with pytest.raises(DecryptionError, match="Authentication tag mismatch"):
        service.decrypt(tampered_ciphertext)


def test_invalid_ciphertext_format():
    service = SecretEncryptionService()

    with pytest.raises(DecryptionError, match="Invalid ciphertext format"):
        service.decrypt("invalid-string-format")

    with pytest.raises(DecryptionError, match="Invalid ciphertext format"):
        service.decrypt("enc:v1:too:few:parts:here")


def test_missing_key_id_raises_key_not_found():
    service = SecretEncryptionService(
        keyring={"key_current": os.urandom(32).hex()}, active_key_id="key_current"
    )

    # Forge ciphertext with an unknown key_id
    nonce_b64 = base64.urlsafe_b64encode(os.urandom(12)).decode("ascii")
    ct_b64 = base64.urlsafe_b64encode(os.urandom(32)).decode("ascii")
    orphan_ciphertext = f"enc:v1:unknown_key_id:{nonce_b64}:{ct_b64}"

    with pytest.raises(KeyNotFoundError, match="unknown_key_id"):
        service.decrypt(orphan_ciphertext)


def test_associated_data_protection():
    key = os.urandom(32).hex()
    service = SecretEncryptionService(keyring={"k1": key}, active_key_id="k1")

    aad = b"tenant-id-12345"
    plaintext = "super-secret"

    ciphertext = service.encrypt(plaintext, associated_data=aad)

    # Correct AAD succeeds
    assert service.decrypt(ciphertext, associated_data=aad) == plaintext

    # Wrong AAD fails
    with pytest.raises(DecryptionError):
        service.decrypt(ciphertext, associated_data=b"tenant-id-67890")


def test_key_rotation_and_reencryption():
    key_old = os.urandom(32).hex()
    key_new = os.urandom(32).hex()

    # Step 1: Encrypt data using older service where key_old is active
    service_old = SecretEncryptionService(
        keyring={"v1": key_old},
        active_key_id="v1",
    )
    ciphertext_v1 = service_old.encrypt("secret-data-to-migrate")
    assert ciphertext_v1.startswith("enc:v1:v1:")

    # Step 2: New service includes both keys, with key_new as active
    service_rotated = SecretEncryptionService(
        keyring={"v1": key_old, "v2": key_new},
        active_key_id="v2",
    )

    # Old ciphertext can still be decrypted transparently
    assert service_rotated.decrypt(ciphertext_v1) == "secret-data-to-migrate"

    # Step 3: Re-encrypt to migrate to new active key
    ciphertext_v2, migrated = service_rotated.reencrypt_if_needed(ciphertext_v1)
    assert migrated is True
    assert ciphertext_v2.startswith("enc:v1:v2:")
    assert service_rotated.decrypt(ciphertext_v2) == "secret-data-to-migrate"

    # Step 4: Re-running reencrypt on an already updated key does nothing
    ciphertext_v2_again, migrated_again = service_rotated.reencrypt_if_needed(ciphertext_v2)
    assert migrated_again is False
    assert ciphertext_v2_again == ciphertext_v2
