import asyncio
import logging
import time
from typing import Callable

import httpx
import jwt
from jwt import PyJWK

from app.auth.errors import JWKSUnavailable, SigningKeyNotFound

ALLOWED_ALGORITHMS = ("RS256",)

logger = logging.getLogger("app.auth.jwks")


class JWKSClient:
    """
    Fetches and caches JSON Web Key Sets (JWKS) in-memory with a configurable TTL,
    single-flight concurrency protection, and stale-on-error resiliency.
    """

    def __init__(
        self,
        jwks_url: str,
        ttl_seconds: float,
        *,
        http_timeout: float = 5.0,
        failure_backoff_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = jwks_url
        self._ttl = ttl_seconds
        self._timeout = http_timeout
        self._backoff = failure_backoff_seconds
        self._transport = transport
        self._clock = clock
        self._keys: dict[str, PyJWK] = {}
        self._expires_at: float | None = None
        self._lock: asyncio.Lock | None = None
        self.fetch_count: int = 0

    @property
    def lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _is_fresh(self) -> bool:
        return self._expires_at is not None and self._clock() < self._expires_at

    async def get_signing_key(self, kid: str) -> PyJWK:
        """Resolve signing key by kid from in-memory cache, refreshing if expired."""
        if not self._is_fresh():
            await self.refresh()
        try:
            return self._keys[kid]
        except KeyError:
            raise SigningKeyNotFound(kid) from None

    async def refresh(self, *, force: bool = False) -> None:
        """Fetch keys from IdP JWKS endpoint and update cache."""
        async with self.lock:
            if not force and self._is_fresh():
                return  # Another task refreshed while waiting for lock

            try:
                async with httpx.AsyncClient(
                    timeout=self._timeout, transport=self._transport
                ) as client:
                    resp = await client.get(self._url)
                    resp.raise_for_status()
                    payload = resp.json()
                    keyset = jwt.PyJWKSet.from_dict(payload)
            except (httpx.HTTPError, ValueError, jwt.PyJWKSetError) as exc:
                if self._keys:
                    # Stale-on-error: continue serving existing keys with short backoff
                    self._expires_at = self._clock() + self._backoff
                    logger.warning("JWKS refresh failed; serving stale keys", exc_info=exc)
                    return
                logger.error("JWKS fetch failed and no cached keys available", exc_info=exc)
                raise JWKSUnavailable() from exc

            self._keys = {
                k.key_id: k
                for k in keyset.keys
                if k.key_id
                and getattr(k, "algorithm_name", None) in ALLOWED_ALGORITHMS
                and getattr(k, "public_key_use", None) in (None, "sig")
            }
            self._expires_at = self._clock() + self._ttl
            self.fetch_count += 1
            logger.info("JWKS refreshed successfully (%d usable keys)", len(self._keys))

    def clear_cache(self) -> None:
        """Clear cache state (primarily for test resets)."""
        self._keys.clear()
        self._expires_at = None
        self.fetch_count = 0
