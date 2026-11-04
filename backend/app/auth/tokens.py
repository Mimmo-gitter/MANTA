from typing import Any, Container
import uuid

import jwt
from jwt import PyJWK

from app.auth.errors import AuthError
from app.auth.jwks import ALLOWED_ALGORITHMS

NIL_UUID = uuid.UUID(int=0)


def extract_bearer_token(authorization: str | None) -> str:
    """Extract and validate the raw Bearer token from the Authorization header."""
    if not authorization:
        raise AuthError("missing authorization header")

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("authorization header must be in 'Bearer <token>' format")

    return token.strip()


def read_kid(token: str) -> str:
    """
    Decode unverified token header, assert algorithm allow-list,
    and extract the key ID ('kid').
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise AuthError("malformed token header") from exc

    alg = header.get("alg")
    if alg not in ALLOWED_ALGORITHMS:
        raise AuthError(f"unsupported algorithm '{alg}'")

    kid = header.get("kid")
    if not kid or not isinstance(kid, str):
        raise AuthError("token header missing valid 'kid'")

    return kid


def decode_token(
    token: str,
    key: PyJWK,
    audience: str,
    issuer: str | Container[str],
) -> dict[str, Any]:
    """
    Cryptographically verify RS256 signature using the resolved public key,
    validate exp, iss, aud, and sub claims, and enforce a non-empty,
    non-nil RFC 4122 canonical UUID tenant_id claim.
    """
    try:
        payload = jwt.decode(
            token,
            key.key,
            algorithms=list(ALLOWED_ALGORITHMS),
            audience=audience,
            issuer=issuer,
            options={
                "verify_signature": True,
                "verify_exp": True,
                "verify_iss": True,
                "verify_aud": True,
                "verify_sub": True,
                "require": ["exp", "iss", "aud", "sub"],
            },
        )
    except (jwt.PyJWTError, TypeError, ValueError) as exc:
        raise AuthError(f"JWT validation failed: {exc}") from exc

    # Validate sub claim presence and type
    sub = payload.get("sub")
    if not sub or not isinstance(sub, str) or not sub.strip():
        raise AuthError("token missing or empty 'sub' claim")

    # Validate tenant_id claim presence and type
    tenant_id = payload.get("tenant_id")
    if not tenant_id or not isinstance(tenant_id, str) or not tenant_id.strip():
        raise AuthError("token missing or empty 'tenant_id' claim")

    # Validate RFC 4122 UUID and reject nil UUID (Architecture §6.5)
    clean_tenant_id = tenant_id.strip()
    try:
        parsed_uuid = uuid.UUID(clean_tenant_id)
    except (ValueError, AttributeError) as exc:
        raise AuthError("tenant_id claim must be a valid UUID") from exc

    if parsed_uuid == NIL_UUID:
        raise AuthError("nil UUID tenant_id is forbidden")

    # Normalize to canonical lowercase 36-character hyphenated UUID string
    payload["tenant_id"] = str(parsed_uuid)
    return payload
