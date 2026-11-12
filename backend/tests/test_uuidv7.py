"""
Task 33.2: Unit & concurrency tests for RFC 9562 monotonic UUIDv7 generator.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import time
import uuid

import pytest

from app.core import uuidv7 as uuidv7_module
from app.core.uuidv7 import extract_uuidv7_timestamp_ms, generate_uuidv7
from app.db.models.models import OutboxEvent


def test_generate_uuidv7_rfc9562_format_and_timestamp() -> None:
    """Verify 48-bit millisecond timestamp, version 7, and RFC 9562 variant bits."""
    before_ms = time.time_ns() // 1_000_000
    generated = generate_uuidv7()
    after_ms = time.time_ns() // 1_000_000

    assert isinstance(generated, uuid.UUID)
    assert generated.version == 7
    assert generated.variant == uuid.RFC_4122

    embedded_ms = extract_uuidv7_timestamp_ms(generated)
    assert before_ms <= embedded_ms or abs(embedded_ms - before_ms) <= 10
    assert embedded_ms >= before_ms
    # Account for monotonic counter rollover advancing at most a few ms if burst-tested
    assert embedded_ms <= after_ms + 50


def test_generate_uuidv7_strict_monotonic_burst_and_rollover() -> None:
    """Verify strict int and lexicographic monotonicity across 5,000 IDs in one frozen ms."""
    frozen_ns = 1_795_000_000_123 * 1_000_000

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(uuidv7_module, "_last_ts_ms", -1)
        mp.setattr(uuidv7_module, "_last_seq", 0)
        mp.setattr(uuidv7_module.time, "time_ns", lambda: frozen_ns)

        # Exceeds 12-bit sequence space (4096) in a single ms to exercise rollover
        ids = [generate_uuidv7() for _ in range(5000)]

    assert len(set(ids)) == 5000
    for prev, curr in zip(ids, ids[1:]):
        assert prev.version == 7 and curr.version == 7
        assert prev.int < curr.int
        assert str(prev) < str(curr)


def test_generate_uuidv7_handles_clock_regression() -> None:
    """Verify OS wall-clock backward steps never produce smaller or colliding UUIDv7s."""
    t_initial_ns = 1_795_000_100_000 * 1_000_000
    t_regressed_ns = (1_795_000_100_000 - 5_000) * 1_000_000
    timestamps = iter([t_initial_ns, t_regressed_ns, t_regressed_ns + 1_000_000])

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(uuidv7_module, "_last_ts_ms", -1)
        mp.setattr(uuidv7_module, "_last_seq", 0)
        mp.setattr(uuidv7_module.time, "time_ns", lambda: next(timestamps))

        first = generate_uuidv7()
        second = generate_uuidv7()
        third = generate_uuidv7()

    assert first.int < second.int < third.int
    assert str(first) < str(second) < str(third)


def test_generate_uuidv7_thread_safe_uniqueness() -> None:
    """Verify concurrent threads produce strictly unique RFC 9562 UUIDv7 identifiers."""
    per_thread = 500
    workers = 8

    def _batch(_: int) -> list[uuid.UUID]:
        return [generate_uuidv7() for _ in range(per_thread)]

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_batch, range(workers)))

    all_ids = [item for sublist in results for item in sublist]
    assert len(all_ids) == workers * per_thread
    assert len(set(all_ids)) == workers * per_thread
    assert all(u.version == 7 and u.variant == uuid.RFC_4122 for u in all_ids)


def test_outbox_event_model_uses_uuidv7_default_factory() -> None:
    """Verify OutboxEvent.id column uses generate_uuidv7 as default factory."""
    default_obj = OutboxEvent.__table__.c.id.default
    assert default_obj is not None
    unwrapped = getattr(default_obj.arg, "__wrapped__", default_obj.arg)
    assert unwrapped is generate_uuidv7
    generated = default_obj.arg(None)
    assert isinstance(generated, uuid.UUID)
    assert generated.version == 7


def test_extract_uuidv7_timestamp_rejects_non_v7() -> None:
    """Verify extract_uuidv7_timestamp_ms rejects UUIDv4 values."""
    with pytest.raises(ValueError, match="Expected UUID version 7"):
        extract_uuidv7_timestamp_ms(uuid.uuid4())
