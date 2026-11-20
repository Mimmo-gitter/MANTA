import time
from collections import defaultdict
from threading import Lock
from typing import Dict, List

from fastapi import HTTPException, Request, status


class InMemoryRateLimiter:
    """
    Thread-safe sliding-window in-memory rate limiter per client IP.
    Default: 10 registration requests per 60 seconds per IP.
    """

    def __init__(self, requests_limit: int = 10, window_seconds: int = 60):
        self.requests_limit = requests_limit
        self.window_seconds = window_seconds
        self._lock = Lock()
        self._requests: Dict[str, List[float]] = defaultdict(list)

    def check_rate_limit(self, request: Request) -> None:
        client_ip = request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or (
            request.client.host if request.client else "127.0.0.1"
        )
        now = time.time()
        with self._lock:
            timestamps = self._requests[client_ip]
            cutoff = now - self.window_seconds
            valid_timestamps = [ts for ts in timestamps if ts > cutoff]
            if len(valid_timestamps) >= self.requests_limit:
                retry_after = int(self.window_seconds - (now - valid_timestamps[0])) + 1
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Too many registration requests. Please try again later.",
                    headers={"Retry-After": str(max(1, retry_after))},
                )
            valid_timestamps.append(now)
            self._requests[client_ip] = valid_timestamps

    def reset(self) -> None:
        """Reset rate limiter state (useful for automated testing)."""
        with self._lock:
            self._requests.clear()


registration_rate_limiter = InMemoryRateLimiter()
