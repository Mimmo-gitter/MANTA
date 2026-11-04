class AuthError(Exception):
    """Base exception for authentication failures."""


class SigningKeyNotFound(AuthError):
    """Signing key with specified kid was not found in JWKS."""

    def __init__(self, kid: str) -> None:
        super().__init__(f"Signing key with kid '{kid}' not found in JWKS")
        self.kid = kid


class JWKSUnavailable(AuthError):
    """JWKS endpoint is unreachable and no cached keys are available."""
