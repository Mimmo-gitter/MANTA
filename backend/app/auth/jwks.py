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
    single-flight concurrency protection, rate-limited on-demand refresh on unknown kid,
    and stale-on-error resiliency.
    """

    def __init__(
        self,
        jwks_url: str,
        ttl_seconds: float,
        *,
        http_timeout: float = 5.0,
        failure_backoff_seconds: float = 30.0,
        min_refresh_interval_seconds: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._url = jwks_url
        self._ttl = ttl_seconds
        self._timeout = http_timeout
        self._backoff = failure_backoff_seconds
        self._min_refresh_interval = min_refresh_interval_seconds
        self._transport = transport
        self._clock = clock
        self._keys: dict[str, PyJWK] = {}
        self._expires_at: float | None = None
        self._last_refresh_time: float = 0.0
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
        """
        Resolve signing key by kid from in-memory cache.
        If cache is expired, or if kid is unknown (key rotation), triggers
        a rate-limited on-demand refresh under single-flight concurrency lock.
        """
        # 1. Fast path: key is already cached and cache TTL is fresh
        if self._is_fresh() and kid in self._keys:
            return self._keys[kid]

        # 2. Key is missing OR cache TTL expired: acquire lock to refresh or resolve
        async with self.lock:
            # Re-check after acquiring lock (double-checked locking)
            if self._is_fresh() and kid in self._keys:
                return self._keys[kid]

            now = self._clock()
            cache_expired = not self._is_fresh()

            if not cache_expired:
                # Cache is fresh, but kid was not found: rate-limit on-demand refetches
                elapsed = now - self._last_refresh_time
                if elapsed < self._min_refresh_interval:
                    logger.warning(
                        "JWKS refresh rate-limited for unknown key id '%s' "
                        "(%.1fs elapsed < %.1fs cooldown)",
                        kid,
                        elapsed,
                        self._min_refresh_interval,
                        extra={
                            "component": "jwks",
                            "operation": "jwks_refresh_rate_limited",
                            "kid": kid,
                            "elapsed_seconds": elapsed,
                            "cooldown_seconds": self._min_refresh_interval,
                        },
                    )
                    raise SigningKeyNotFound(kid)

                logger.info(
                    "Unknown key id '%s' encountered; triggering on-demand JWKS refresh",
                    kid,
                    extra={
                        "component": "jwks",
                        "operation": "jwks_refresh_on_unknown_kid",
                        "kid": kid,
                    },
                )

            # Perform the fetch from IdP
            await self._fetch_and_update_keys()

            # Check if kid is now resolved in keyset
            if kid in self._keys:
                logger.info(
                    "Signing key '%s' resolved successfully after JWKS refresh",
                    kid,
                    extra={
                        "component": "jwks",
                        "operation": "signing_key_resolved_after_refresh",
                        "kid": kid,
                    },
                )
                return self._keys[kid]

            logger.warning(
                "Signing key '%s' not found in JWKS after refresh",
                kid,
                extra={
                    "component": "jwks",
                    "operation": "signing_key_not_found_after_refresh",
                    "kid": kid,
                },
            )
            raise SigningKeyNotFound(kid)

    async def _fetch_and_update_keys(self) -> None:
        """Internal helper to fetch keys from IdP JWKS endpoint and update cache."""
        now = self._clock()
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, transport=self._transport
            ) as client:
                resp = await client.get(self._url)
                resp.raise_for_status()
                payload = resp.json()
                keyset = jwt.PyJWKSet.from_dict(payload)
        except (httpx.HTTPError, ValueError, jwt.PyJWKSetError) as exc:
            self._last_refresh_time = now  # Suppress immediate spam on failing IdP
            if self._keys:
                # Stale-on-error: continue serving existing keys with short backoff
                self._expires_at = now + self._backoff
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
        self._expires_at = now + self._ttl
        self._last_refresh_time = now
        self.fetch_count += 1
        logger.info(
            "JWKS refreshed successfully (%d usable keys)",
            len(self._keys),
            extra={
                "component": "jwks",
                "operation": "jwks_refresh_success",
                "usable_keys": len(self._keys),
            },
        )

    async def refresh(self, *, force: bool = False) -> None:
        """Fetch keys from IdP JWKS endpoint and update cache."""
        async with self.lock:
            if not force and self._is_fresh():
                return  # Another task refreshed while waiting for lock
            await self._fetch_and_update_keys()

    def clear_cache(self) -> None:
        """Clear cache state (primarily for test resets)."""
        self._keys.clear()
        self._expires_at = None
        self._last_refresh_time = 0.0
        self.fetch_count = 0
