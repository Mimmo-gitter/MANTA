from typing import Any

import jwt
from jwt import PyJWK

from app.auth.errors import AuthError
from app.auth.jwks import ALLOWED_ALGORITHMS


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


def decode_token(token: str, key: PyJWK, audience: str) -> dict[str, Any]:
    """
    Cryptographically verify RS256 signature using the resolved public key,
    and validate audience and expiration claims.
    """
    return jwt.decode(
        token,
        key.key,
        algorithms=list(ALLOWED_ALGORITHMS),
        audience=audience,
    )
