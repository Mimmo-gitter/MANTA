"""
Task 33.2: Application-side RFC 9562 UUIDv7 Monotonic Generator.

Implements a thread-safe, Python 3.12+ compatible RFC 9562 UUIDv7 generator using a
48-bit millisecond Unix timestamp and 74 bits of monotonic counter / random entropy
(12-bit Method-1 counter in `rand_a` + 62-bit CSPRNG entropy in `rand_b`).
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid

_MASK_48 = (1 << 48) - 1
_SEQ_MAX = 0xFFF

_state_lock = threading.Lock()
_last_ts_ms: int = -1
_last_seq: int = 0


def generate_uuidv7() -> uuid.UUID:
    """Generate a monotonically increasing RFC 9562 UUIDv7 identifier.

    Bit layout (MSB -> LSB, 128 bits):
    - Bits 127..80 (48 bits): Unix epoch timestamp in milliseconds (`unix_ts_ms`).
    - Bits 79..76  (4 bits):  Version `0111` (7).
    - Bits 75..64  (12 bits): Monotonic `rand_a` sub-millisecond sequence counter.
    - Bits 63..62  (2 bits):  RFC 9562 variant `10`.
    - Bits 61..0   (62 bits): Cryptographically random `rand_b` bits.

    Guarantees strict process-local monotonicity (`u_prev.int < u_next.int` and
    `str(u_prev) < str(u_next)`), including under sub-millisecond bursts,
    12-bit counter rollover, and OS wall-clock backward steps.
    """
    global _last_ts_ms, _last_seq

    now_ms = time.time_ns() // 1_000_000
    with _state_lock:
        if now_ms > _last_ts_ms:
            _last_ts_ms = now_ms
            # Seed 11 bits so the MSB of the 12-bit counter starts cleared (>= 2048 steps).
            _last_seq = secrets.randbits(11)
        else:
            # Same millisecond or wall clock regressed: advance monotonic sequence.
            _last_seq += 1
            if _last_seq > _SEQ_MAX:
                _last_ts_ms += 1
                _last_seq = secrets.randbits(11)

        ts_ms = _last_ts_ms
        seq = _last_seq

    rand_b = secrets.randbits(62)
    value = (
        ((ts_ms & _MASK_48) << 80) | (0x7 << 76) | ((seq & _SEQ_MAX) << 64) | (0x2 << 62) | rand_b
    )
    return uuid.UUID(int=value)


def extract_uuidv7_timestamp_ms(value: uuid.UUID) -> int:
    """Return the embedded 48-bit Unix millisecond timestamp from a UUIDv7."""
    if value.version != 7:
        raise ValueError(f"Expected UUID version 7, got version {value.version}: {value}")
    return (value.int >> 80) & _MASK_48
