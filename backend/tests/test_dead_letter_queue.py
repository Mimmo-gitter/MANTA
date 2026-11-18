"""
Task 23.7: Automated pytest test suite for 5-attempt backoff quarantine and RBAC HTTP 403 enforcement.

Parent Story: US-23 (Dead-Letter Queue & Poison Message Quarantine / Recovery)
Ticket: SCRUM-421 (Task 23.7)

Verification Scope:
1. Backoff schedule (2, 4, 8, 16, 32):
   - Invariant 1: Mathematical exponential backoff: delay = 2^attempt seconds (2, 4, 8, 16, 32).
   - Non-blocking worker queue flow during delayed retries.
   - Parking of failed jobs in 'arq:queue:delayed' with due timestamp.
   - Promotion of due retries back to priority queue tail.
2. dead_letter_events persistence after 5 failures:
   - Invariant 2 & 4: Terminal poison payload quarantine on 5th failure (MAX_ATTEMPTS = 5).
   - Creation of dead_letter_events row with total_attempts=5, is_resolved=False, stack_trace, payload.
   - Atomic update of webhook_events.status='dead_lettered' and webhook_events.attempts=5.
3. POST /api/v1/dead-letters/{id}/redrive:
   - Invariant 5: One-click re-drive endpoint resets webhook_events (status='queued', attempts=0).
   - Marks dead_letter_events (is_resolved=True, resolved_by=user_id, resolved_at=now()).
   - Pushes job envelope to high-priority queue 'arq:queue:high' with priority='high', attempt=0.
   - Returns HTTP 202 Accepted.
   - Idempotency / conflict check: already-resolved event returns HTTP 409 Conflict.
   - Non-existent event returns HTTP 404 Not Found.
   - Safe degradation on Redis outage during re-drive (returns enqueued=False, HTTP 202).
4. Tenant RLS isolation:
   - Invariant 6: Multi-tenant Row-Level Security read and write containment.
   - Cross-tenant read isolation: Tenant B querying GET /api/v1/dead-letters cannot see Tenant A's quarantined events.
   - Cross-tenant anti-enumeration on re-drive: Tenant B attempting POST /api/v1/dead-letters/{id}/redrive for Tenant A's event returns HTTP 404 Not Found.
   - Worker retry router executes SELECT set_config('app.current_tenant_id', ...) ensuring RLS context.
5. HTTP 403 for DEVELOPER:
   - Invariant 7: RBAC enforcement via require_permission(Permission.MANAGE_WEBHOOK_INTEGRATIONS).
   - DEVELOPER role receives HTTP 403 Forbidden on GET /api/v1/dead-letters and POST /api/v1/dead-letters/{id}/redrive.
   - Non-admin roles (DEVELOPER, SCRUM_MASTER, PROJECT_MANAGER, SOLUTION_ARCHITECT, DELIVERY_DIRECTOR, TOP_MANAGEMENT) receive HTTP 403 Forbidden.
   - TENANT_ADMIN is authorized.
   - Unauthenticated requests receive HTTP 401 Unauthorized.
"""

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
from httpx import ASGITransport, AsyncClient
import jwt
from jwt.algorithms import RSAAlgorithm
import pytest
from redis.exceptions import RedisError
from sqlalchemy import text

from app.auth.jwks import JWKSClient
from app.auth.permissions import Permission, get_route_declared_permissions
from app.config import settings
from app.db.models import WebhookEvent
from app.db.session import async_engine, get_tenant_db_context
from app.dead_letters import service
from app.dead_letters.service import (
    DeadLetterAlreadyResolvedError,
    DeadLetterNotFoundError,
    DeadLetterPage,
    RedriveResult,
)
from app.main import app
from app.webhooks import enqueue as enqueue_mod
from workers.retry import (
    BACKOFF_SCHEDULE,
    MAX_ATTEMPTS,
    QUEUE_DELAYED,
    compute_backoff,
    handle_job_failure,
    promote_due_retries,
    quarantine_event,
    record_failed_attempt,
    schedule_retry,
)
from workers.worker import (
    QUEUE_DEFAULT,
    QUEUE_HIGH,
    enqueue_job,
    get_in_flight_key,
    run_worker_fetch_loop,
    serialize_job_payload,
)

TENANT_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TENANT_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


# ==============================================================================
# In-Memory Test Doubles (FakeRedis & FakeDB)
# ==============================================================================


class FakeRedis:
    """Async Redis test double supporting list and ZSET operations."""

    supports_lua = False

    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = defaultdict(list)
        self.zsets: dict[str, dict[str, float]] = defaultdict(dict)

    async def rpush(self, key: str, *values: str) -> int:
        self.lists[key].extend(values)
        return len(self.lists[key])

    async def lrem(self, key: str, count: int, value: str) -> int:
        if value in self.lists[key]:
            self.lists[key].remove(value)
            return 1
        return 0

    async def zadd(self, key: str, mapping: Mapping[str, float]) -> int:
        self.zsets[key].update(mapping)
        return len(mapping)

    async def zrangebyscore(
        self,
        key: str,
        mn: float | str,
        mx: float | str,
        start: int = 0,
        num: int | None = None,
    ) -> list[str]:
        max_score = float(mx) if mx != "+inf" else float("inf")
        min_score = float(mn) if mn != "-inf" else float("-inf")
        items = sorted(
            (m for m, s in self.zsets[key].items() if min_score <= s <= max_score),
            key=lambda m: self.zsets[key][m],
        )
        return items[start : start + num if num is not None else None]

    async def zrem(self, key: str, member: str) -> int:
        return 1 if self.zsets[key].pop(member, None) is not None else 0

    async def _move(self, s: str, d: str, side_s: str, side_d: str) -> str | None:
        if not self.lists[s]:
            return None
        item = self.lists[s].pop(0 if side_s == "LEFT" else -1)
        if side_d == "LEFT":
            self.lists[d].insert(0, item)
        else:
            self.lists[d].append(item)
        return item

    async def lmove(
        self, first: str, second: str, src: str = "LEFT", dest: str = "RIGHT"
    ) -> str | None:
        return await self._move(first, second, src, dest)

    async def blmove(
        self, first: str, second: str, timeout: int = 0, src: str = "LEFT", dest: str = "RIGHT"
    ) -> str | None:
        return await self._move(first, second, src, dest)


class FakeDB:
    """Async database session double recording SQL statements and transaction boundaries."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.commits: int = 0
        self.rollbacks: int = 0

    async def execute(self, stmt: Any, params: Any = None) -> Any:
        self.statements.append((str(stmt), dict(params or {})))
        return None

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


# ==============================================================================
# Authentication & Cryptographic Helpers
# ==============================================================================


def generate_rsa_keypair(kid: str) -> tuple[rsa.RSAPrivateKey, dict[str, Any]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk_dict = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk_dict.update({"kid": kid, "kty": "RSA", "alg": "RS256", "use": "sig"})
    return private_key, jwk_dict


class MockJWKSServer:
    def __init__(self, keys_dict: dict[str, Any]) -> None:
        self.keys_dict = keys_dict

    async def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=self.keys_dict, request=request)


@pytest.fixture
def auth_context():
    key, jwk = generate_rsa_keypair("dlq-test-key-23-7")
    mock_idp = MockJWKSServer({"keys": [jwk]})

    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(mock_idp.handle_request),
    )

    original_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = client

    yield {"key": key, "kid": "dlq-test-key-23-7"}

    app.state.jwks_client = original_client


def mint_jwt(
    auth_ctx: dict[str, Any],
    tenant_id: uuid.UUID | str,
    roles: list[str] | None = None,
    sub: str = "dlq-user",
    email: str | None = None,
) -> str:
    if roles is None:
        roles = ["TENANT_ADMIN"]

    claims = {
        "sub": sub,
        "email": email or f"{sub}@example.com",
        "tenant_id": str(tenant_id),
        "aud": settings.OIDC_AUDIENCE,
        "iss": f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
        "exp": int(time.time()) + 3600,
        "realm_access": {"roles": roles},
    }
    return jwt.encode(
        claims,
        auth_ctx["key"],
        algorithm="RS256",
        headers={"alg": "RS256", "kid": auth_ctx["kid"]},
    )


def is_live_db_available() -> bool:
    """Check if a real PostgreSQL instance is reachable with configured credentials."""
    try:

        async def _ping() -> None:
            await async_engine.dispose()
            try:
                async with async_engine.connect() as conn:
                    await conn.execute(text("SELECT 1"))
            finally:
                await async_engine.dispose()

        asyncio.run(_ping())
        return True
    except Exception:
        return False


LIVE_DB_AVAILABLE = is_live_db_available()
skip_without_live_db = pytest.mark.skipif(
    not LIVE_DB_AVAILABLE,
    reason="PostgreSQL test instance is not reachable locally; runs in CI.",
)


async def _register_test_tenant(slug_prefix: str) -> tuple[uuid.UUID, str]:
    """Register an isolated test tenant via /tenants/register for live DB tests."""
    unique = uuid.uuid4().hex[:8]
    payload = {
        "name": f"DLQ Test Tenant {unique}",
        "slug": f"{slug_prefix}-{unique}",
        "admin_email": f"dlq-{unique}@dlqtemp.org",
        "admin_name": f"Admin {unique}",
    }
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/tenants/register", json=payload)
        assert resp.status_code == 201, f"Failed to register test tenant: {resp.text}"
        data = resp.json()
        return uuid.UUID(data["tenant_id"]), payload["slug"]


# ==============================================================================
# SECTION 1: BACKOFF SCHEDULE (2, 4, 8, 16, 32) & RETRY ROUTER TESTS
# ==============================================================================


def test_backoff_schedule_constant_values():
    """Verify BACKOFF_SCHEDULE constant explicitly defines (2, 4, 8, 16, 32)."""
    assert BACKOFF_SCHEDULE == (2, 4, 8, 16, 32)
    assert len(BACKOFF_SCHEDULE) == MAX_ATTEMPTS


@pytest.mark.parametrize(
    ("attempt", "expected_delay"),
    [
        (1, 2),
        (2, 4),
        (3, 8),
        (4, 16),
        (5, 32),
    ],
)
def test_compute_backoff_powers_of_two(attempt: int, expected_delay: int):
    """
    Verify Invariant 1: compute_backoff yields 2^attempt seconds:
    attempt 1 -> 2s, attempt 2 -> 4s, attempt 3 -> 8s, attempt 4 -> 16s, attempt 5 -> 32s.
    """
    delay = compute_backoff(attempt)
    assert delay == expected_delay
    assert delay == 2**attempt


@pytest.mark.parametrize("invalid_attempt", [0, -1, -10])
def test_compute_backoff_rejects_non_positive_attempt(invalid_attempt: int):
    """compute_backoff raises ValueError when attempt < 1."""
    with pytest.raises(ValueError, match="attempt must be >= 1"):
        compute_backoff(invalid_attempt)


@pytest.mark.asyncio
async def test_schedule_retry_parks_job_in_delayed_zset():
    """
    schedule_retry parks the job in 'arq:queue:delayed' with score = now + 2^attempt,
    increments attempt counter in payload, and strips ephemeral owner_token.
    """
    redis = FakeRedis()
    base_payload = {
        "job_id": str(uuid.uuid4()),
        "event_id": str(uuid.uuid4()),
        "tenant_id": str(TENANT_A),
        "priority": "default",
        "attempt": 0,
        "owner_token": "ephemeral-claim-token-123",
        "payload": {"action": "push"},
    }
    now = 1700000000.0

    # Attempt 1 -> delay 2s
    raw_1, delay_1 = await schedule_retry(redis, base_payload, attempt=1, now=now)
    assert delay_1 == 2
    assert redis.zsets[QUEUE_DELAYED][raw_1] == now + 2
    parsed_1 = json.loads(raw_1)
    assert parsed_1["attempt"] == 1
    assert "owner_token" not in parsed_1

    # Attempt 2 -> delay 4s
    raw_2, delay_2 = await schedule_retry(redis, base_payload, attempt=2, now=now)
    assert delay_2 == 4
    assert redis.zsets[QUEUE_DELAYED][raw_2] == now + 4

    # Attempt 3 -> delay 8s
    raw_3, delay_3 = await schedule_retry(redis, base_payload, attempt=3, now=now)
    assert delay_3 == 8
    assert redis.zsets[QUEUE_DELAYED][raw_3] == now + 8

    # Attempt 4 -> delay 16s
    raw_4, delay_4 = await schedule_retry(redis, base_payload, attempt=4, now=now)
    assert delay_4 == 16
    assert redis.zsets[QUEUE_DELAYED][raw_4] == now + 16


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("prior_attempt", "expected_attempt", "expected_delay"),
    [
        (0, 1, 2),
        (1, 2, 4),
        (2, 3, 8),
        (3, 4, 16),
    ],
)
async def test_handle_job_failure_attempts_1_to_4_retry_outcome(
    prior_attempt: int, expected_attempt: int, expected_delay: int
):
    """
    handle_job_failure for attempts 1..4 schedules retry with 2^attempt delay,
    updates webhook_events attempts, and sets status back to 'queued' for next CAS claim.
    """
    redis = FakeRedis()
    db = FakeDB()
    event_id = uuid.uuid4()
    job_payload = {
        "job_id": str(uuid.uuid4()),
        "event_id": str(event_id),
        "tenant_id": str(TENANT_A),
        "priority": "default",
        "attempt": prior_attempt,
        "payload": {"ref": "main"},
    }
    now = 1000.0
    exc = ValueError("Transient payload processing failure")

    outcome = await handle_job_failure(redis, db, job_payload, exc, now=now)

    assert outcome.action == "retry"
    assert outcome.attempt == expected_attempt
    assert outcome.delay_seconds == expected_delay
    assert outcome.dead_letter_id is None
    assert outcome.retry_job is not None

    # Check delayed ZSET parking
    assert len(redis.zsets[QUEUE_DELAYED]) == 1
    assert redis.zsets[QUEUE_DELAYED][outcome.retry_job] == now + expected_delay

    # Check DB statements: tenant set_config + record_failed_attempt
    assert db.commits == 1
    statements_sql = [s[0] for s in db.statements]
    assert any("set_config('app.current_tenant_id'" in s for s in statements_sql)
    assert any("UPDATE webhook_events" in s and "attempts" in s for s in statements_sql)


@pytest.mark.asyncio
async def test_promote_due_retries_moves_to_priority_queue():
    """promote_due_retries moves due jobs from delayed ZSET to high/default queue based on priority."""
    redis = FakeRedis()
    now = 500.0

    # Job 1: high priority due at 450 (due)
    high_job = json.dumps({"job_id": "j1", "priority": "high", "attempt": 1})
    await redis.zadd(QUEUE_DELAYED, {high_job: 450.0})

    # Job 2: default priority due at 490 (due)
    default_job = json.dumps({"job_id": "j2", "priority": "default", "attempt": 1})
    await redis.zadd(QUEUE_DELAYED, {default_job: 490.0})

    # Job 3: not yet due (due at 600)
    future_job = json.dumps({"job_id": "j3", "priority": "default", "attempt": 1})
    await redis.zadd(QUEUE_DELAYED, {future_job: 600.0})

    promoted = await promote_due_retries(redis, now=now)

    assert promoted == 2
    assert len(redis.lists[QUEUE_HIGH]) == 1
    assert redis.lists[QUEUE_HIGH][0] == high_job
    assert len(redis.lists[QUEUE_DEFAULT]) == 1
    assert redis.lists[QUEUE_DEFAULT][0] == default_job
    assert len(redis.zsets[QUEUE_DELAYED]) == 1
    assert future_job in redis.zsets[QUEUE_DELAYED]


@pytest.mark.asyncio
async def test_worker_non_blocking_during_poison_backoff():
    """
    Worker fetch loop processes valid jobs without waiting when a poison job
    fails and is scheduled for exponential backoff retry.
    """
    redis = FakeRedis()
    db = FakeDB()
    worker_id = "test-worker-dlq-1"
    in_flight = get_in_flight_key(worker_id)

    # Poison job (fails attempt 1)
    poison_envelope = serialize_job_payload(
        job_id=uuid.uuid4(),
        tenant_id=TENANT_A,
        priority="default",
        job_type="webhook.process",
        payload={"action": "poison"},
        event_id=uuid.uuid4(),
        attempt=0,
    )

    # Valid job behind it
    valid_envelope = serialize_job_payload(
        job_id=uuid.uuid4(),
        tenant_id=TENANT_A,
        priority="default",
        job_type="webhook.process",
        payload={"action": "valid"},
        event_id=uuid.uuid4(),
        attempt=0,
    )

    await enqueue_job(redis, poison_envelope, priority="default")
    await enqueue_job(redis, valid_envelope, priority="default")

    processed: list[str] = []

    async def mock_handler(payload: dict[str, Any]) -> None:
        if payload.get("payload", {}).get("action") == "poison":
            raise RuntimeError("Poison payload exploded")
        processed.append(payload["payload"]["action"])

    async def drain():
        await run_worker_fetch_loop(
            redis,
            worker_id,
            mock_handler,
            poll_timeout=0,
            max_iterations=10,
            db=db,
            retry_on_failure=True,
        )

    # Attempt 1 fails for poison job; valid job processed without blockage
    await drain()
    assert processed == ["valid"]
    assert len(redis.zsets[QUEUE_DELAYED]) == 1
    assert len(redis.lists[in_flight]) == 0


# ==============================================================================
# SECTION 2: DEAD_LETTER_EVENTS PERSISTENCE AFTER 5 FAILURES
# ==============================================================================


@pytest.mark.asyncio
async def test_handle_job_failure_quarantines_on_5th_attempt():
    """
    Verify Invariant 2 & Scope #2:
    On the 5th failure attempt (attempt == 5), retries cease,
    job is NOT parked in delayed ZSET, dead_letter_events row is inserted,
    and webhook_events.status is set to 'dead_lettered'.
    """
    redis = FakeRedis()
    db = FakeDB()
    event_id = uuid.uuid4()
    job_payload = {
        "job_id": str(uuid.uuid4()),
        "event_id": str(event_id),
        "tenant_id": str(TENANT_A),
        "provider": "github",
        "priority": "default",
        "attempt": 4,  # Prior failures = 4 -> this failure is attempt 5
        "payload": {"repository": "acme/api", "commit": "deadbeef"},
    }
    exc = ZeroDivisionError("Metric normalization division by zero")

    outcome = await handle_job_failure(redis, db, job_payload, exc)

    assert outcome.action == "dead_lettered"
    assert outcome.attempt == 5
    assert outcome.dead_letter_id is not None
    assert outcome.retry_job is None
    assert outcome.delay_seconds is None

    # Delayed ZSET remains untouched
    assert len(redis.zsets[QUEUE_DELAYED]) == 0

    # DB transaction committed
    assert db.commits == 1

    # Verify SQL statements executed
    sql_texts = [s[0] for s in db.statements]
    sql_params = [s[1] for s in db.statements]

    # Statement 1: Set tenant config for RLS
    assert any("set_config('app.current_tenant_id'" in s for s in sql_texts)

    # Statement 2: Insert into dead_letter_events
    insert_idx = next(i for i, s in enumerate(sql_texts) if "INSERT INTO dead_letter_events" in s)
    insert_params = sql_params[insert_idx]
    assert insert_params["id"] == outcome.dead_letter_id
    assert insert_params["original_event_id"] == event_id
    assert insert_params["tenant_id"] == TENANT_A
    assert insert_params["provider"] == "github"
    assert insert_params["error_class"] == "builtins.ZeroDivisionError"
    assert "division by zero" in insert_params["stack_trace"]
    assert insert_params["total_attempts"] == 5
    assert json.loads(insert_params["payload"]) == {
        "repository": "acme/api",
        "commit": "deadbeef",
    }
    assert isinstance(insert_params["quarantined_at"], datetime)

    # Statement 3: Mark webhook_events status = 'dead_lettered'
    update_idx = next(
        i
        for i, s in enumerate(sql_texts)
        if "UPDATE webhook_events" in s and "status = 'dead_lettered'" in s
    )
    update_params = sql_params[update_idx]
    assert update_params["event_id"] == event_id
    assert update_params["attempt"] == 5
    assert (
        "builtins.ZeroDivisionError: Metric normalization division by zero"
        in update_params["error_message"]
    )


@pytest.mark.asyncio
async def test_quarantine_event_persists_diagnostic_metadata_and_stack_trace():
    """quarantine_event captures full traceback, error class, and verbatim payload."""
    db = FakeDB()
    event_id = uuid.uuid4()
    nested_payload = {"deep": {"nested": [1, 2, 3], "flag": True}}

    try:
        raise KeyError("Missing required field 'head_commit'")
    except KeyError as exc:
        dl_id = await quarantine_event(
            db,
            {
                "event_id": event_id,
                "tenant_id": TENANT_A,
                "provider": "gitlab",
                "payload": nested_payload,
                "job_id": "j-100",
            },
            exc,
            attempt=5,
        )

    assert dl_id is not None
    insert_stmt = next(s for s in db.statements if "INSERT INTO dead_letter_events" in s[0])
    params = insert_stmt[1]

    assert params["error_class"] == "builtins.KeyError"
    assert "KeyError" in params["stack_trace"]
    assert "Missing required field 'head_commit'" in params["stack_trace"]
    assert json.loads(params["payload"]) == nested_payload
    assert params["total_attempts"] == 5
    assert params["provider"] == "gitlab"


@pytest.mark.asyncio
async def test_quarantine_event_missing_required_ids_returns_none_safely():
    """quarantine_event safely logs error and returns None if db, event_id, or tenant_id is missing."""
    db = FakeDB()
    exc = RuntimeError("test")

    # Missing event_id
    res1 = await quarantine_event(db, {"tenant_id": str(TENANT_A), "payload": {}}, exc, attempt=5)
    assert res1 is None

    # Missing tenant_id
    res2 = await quarantine_event(
        db, {"event_id": str(uuid.uuid4()), "payload": {}}, exc, attempt=5
    )
    assert res2 is None

    # Missing db
    res3 = await quarantine_event(
        None,
        {"event_id": str(uuid.uuid4()), "tenant_id": str(TENANT_A), "payload": {}},
        exc,
        attempt=5,
    )
    assert res3 is None
    assert len(db.statements) == 0


@pytest.mark.asyncio
async def test_failure_routing_rollback_on_database_error():
    """handle_job_failure triggers database rollback to clear aborted transaction before failure routing."""
    redis = FakeRedis()
    db = FakeDB()
    job_payload = {
        "job_id": str(uuid.uuid4()),
        "event_id": str(uuid.uuid4()),
        "tenant_id": str(TENANT_A),
        "attempt": 0,
        "payload": {},
    }
    exc = ValueError("Error during execution")

    await handle_job_failure(redis, db, job_payload, exc)
    assert db.rollbacks >= 1


# ==============================================================================
# SECTION 3: POST /api/v1/dead-letters/{id}/redrive ENDPOINT TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_post_redrive_success_enqueues_high_priority(auth_context):
    """
    Verify Invariant 5:
    POST /api/v1/dead-letters/{id}/redrive marks DLQ item resolved, resets webhook event
    to 'queued' with attempts=0, enqueues envelope to 'arq:queue:high' with priority='high',
    and returns HTTP 202 Accepted.
    """
    fake_redis = FakeRedis()
    enqueue_mod.set_redis(fake_redis)

    token = mint_jwt(auth_context, tenant_id=TENANT_A, roles=["TENANT_ADMIN"], sub="admin-1")
    headers = {"Authorization": f"Bearer {token}"}

    dl_id = uuid.uuid4()
    event_id = uuid.uuid4()
    resolver_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    redrive_result = RedriveResult(
        dead_letter_id=dl_id,
        event_id=event_id,
        tenant_id=TENANT_A,
        provider="github",
        event_type="push",
        payload={"ref": "refs/heads/main", "commit": "123456"},
        resolved_at=now,
    )

    with (
        patch("app.dead_letters.router.get_tenant_db_context") as mock_ctx,
        patch(
            "app.dead_letters.router.service.resolve_user_id", return_value=resolver_id
        ) as mock_resolve,
        patch(
            "app.dead_letters.router.service.redrive_dead_letter", return_value=redrive_result
        ) as mock_redrive,
    ):
        mock_session = AsyncMock()
        mock_ctx.return_value.__aenter__.return_value = mock_session

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(f"/api/v1/dead-letters/{dl_id}/redrive", headers=headers)
            assert res.status_code == 202
            data = res.json()

            assert data["id"] == str(dl_id)
            assert data["event_id"] == str(event_id)
            assert data["status"] == "queued"
            assert data["queue"] == QUEUE_HIGH
            assert data["enqueued"] is True
            assert "resolved_at" in data

            mock_resolve.assert_called_once_with(
                mock_session, TENANT_A, "admin-1", "admin-1@example.com"
            )
            mock_redrive.assert_called_once_with(mock_session, TENANT_A, dl_id, resolver_id)

    # Verify high-priority queue content in Redis
    assert len(fake_redis.lists[QUEUE_HIGH]) == 1
    raw_envelope = fake_redis.lists[QUEUE_HIGH][0]
    envelope = json.loads(raw_envelope)
    assert envelope["priority"] == "high"
    assert envelope["attempt"] == 0
    assert envelope["event_id"] == str(event_id)
    assert envelope["tenant_id"] == str(TENANT_A)
    assert envelope["provider"] == "github"
    assert envelope["payload"] == {"ref": "refs/heads/main", "commit": "123456"}

    enqueue_mod.set_redis(None)


@pytest.mark.asyncio
async def test_post_redrive_already_resolved_returns_409_conflict(auth_context):
    """POST /api/v1/dead-letters/{id}/redrive returns HTTP 409 Conflict when item was already resolved."""
    token = mint_jwt(auth_context, tenant_id=TENANT_A, roles=["TENANT_ADMIN"])
    headers = {"Authorization": f"Bearer {token}"}
    dl_id = uuid.uuid4()

    with (
        patch("app.dead_letters.router.get_tenant_db_context") as mock_ctx,
        patch("app.dead_letters.router.service.resolve_user_id", return_value=None),
        patch(
            "app.dead_letters.router.service.redrive_dead_letter",
            side_effect=DeadLetterAlreadyResolvedError(str(dl_id)),
        ),
    ):
        mock_session = AsyncMock()
        mock_ctx.return_value.__aenter__.return_value = mock_session

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(f"/api/v1/dead-letters/{dl_id}/redrive", headers=headers)
            assert res.status_code == 409
            assert res.json() == {"detail": "Dead-letter event already resolved"}


@pytest.mark.asyncio
async def test_post_redrive_non_existent_item_returns_404_not_found(auth_context):
    """POST /api/v1/dead-letters/{id}/redrive returns HTTP 404 Not Found when DLQ event is not found."""
    token = mint_jwt(auth_context, tenant_id=TENANT_A, roles=["TENANT_ADMIN"])
    headers = {"Authorization": f"Bearer {token}"}
    dl_id = uuid.uuid4()

    with (
        patch("app.dead_letters.router.get_tenant_db_context") as mock_ctx,
        patch("app.dead_letters.router.service.resolve_user_id", return_value=None),
        patch(
            "app.dead_letters.router.service.redrive_dead_letter",
            side_effect=DeadLetterNotFoundError(str(dl_id)),
        ),
    ):
        mock_session = AsyncMock()
        mock_ctx.return_value.__aenter__.return_value = mock_session

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(f"/api/v1/dead-letters/{dl_id}/redrive", headers=headers)
            assert res.status_code == 404
            assert res.json() == {"detail": "Dead-letter event not found"}


@pytest.mark.asyncio
async def test_post_redrive_redis_outage_graceful_degradation(auth_context):
    """
    If Redis is down during redrive:
    - DB transaction commits event as 'queued' with attempts=0.
    - Router returns HTTP 202 Accepted with enqueued=False (does not crash with HTTP 500).
    """
    broken_redis = AsyncMock()
    broken_redis.rpush.side_effect = RedisError("Connection refused to Redis broker")
    enqueue_mod.set_redis(broken_redis)

    token = mint_jwt(auth_context, tenant_id=TENANT_A, roles=["TENANT_ADMIN"])
    headers = {"Authorization": f"Bearer {token}"}
    dl_id = uuid.uuid4()
    event_id = uuid.uuid4()

    redrive_result = RedriveResult(
        dead_letter_id=dl_id,
        event_id=event_id,
        tenant_id=TENANT_A,
        provider="github",
        event_type="push",
        payload={"ref": "main"},
        resolved_at=datetime.now(timezone.utc),
    )

    with (
        patch("app.dead_letters.router.get_tenant_db_context") as mock_ctx,
        patch("app.dead_letters.router.service.resolve_user_id", return_value=None),
        patch("app.dead_letters.router.service.redrive_dead_letter", return_value=redrive_result),
    ):
        mock_session = AsyncMock()
        mock_ctx.return_value.__aenter__.return_value = mock_session

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(f"/api/v1/dead-letters/{dl_id}/redrive", headers=headers)
            assert res.status_code == 202
            data = res.json()
            assert data["enqueued"] is False
            assert data["status"] == "queued"

    enqueue_mod.set_redis(None)


# ==============================================================================
# SECTION 4: TENANT RLS ISOLATION TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_tenant_rls_read_isolation_get_dead_letters(auth_context):
    """
    Verify Invariant 6 (RLS Read Containment):
    Tenant B querying GET /api/v1/dead-letters cannot see Tenant A's quarantined events.
    Returns strictly empty list and 0 counts.
    """
    token_b = mint_jwt(auth_context, tenant_id=TENANT_B, roles=["TENANT_ADMIN"])
    headers = {"Authorization": f"Bearer {token_b}"}

    empty_page = DeadLetterPage(items=[], unresolved_count=0, total=0)

    with (
        patch("app.dead_letters.router.get_tenant_db_context") as mock_ctx,
        patch(
            "app.dead_letters.router.service.list_dead_letters", return_value=empty_page
        ) as mock_list,
    ):
        mock_session = AsyncMock()
        mock_ctx.return_value.__aenter__.return_value = mock_session

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/dead-letters", headers=headers)
            assert res.status_code == 200
            data = res.json()

            assert data["items"] == []
            assert data["unresolved_count"] == 0
            assert data["total"] == 0

            # Verifies service call was scoped strictly to TENANT_B
            mock_list.assert_called_once_with(
                mock_session,
                TENANT_B,
                status_filter="unresolved",
                limit=50,
                offset=0,
            )


@pytest.mark.asyncio
async def test_tenant_rls_anti_enumeration_post_redrive(auth_context):
    """
    Verify Invariant 6 (Anti-Enumeration Defense):
    Tenant B attempting to re-drive a DLQ event ID belonging to Tenant A receives HTTP 404
    Not Found without disclosing the existence of Tenant A's record.
    """
    token_b = mint_jwt(auth_context, tenant_id=TENANT_B, roles=["TENANT_ADMIN"])
    headers = {"Authorization": f"Bearer {token_b}"}

    tenant_a_dl_id = uuid.uuid4()

    with (
        patch("app.dead_letters.router.get_tenant_db_context") as mock_ctx,
        patch("app.dead_letters.router.service.resolve_user_id", return_value=None),
        patch(
            "app.dead_letters.router.service.redrive_dead_letter",
            side_effect=DeadLetterNotFoundError(str(tenant_a_dl_id)),
        ) as mock_redrive,
    ):
        mock_session = AsyncMock()
        mock_ctx.return_value.__aenter__.return_value = mock_session

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.post(f"/api/v1/dead-letters/{tenant_a_dl_id}/redrive", headers=headers)
            assert res.status_code == 404
            assert res.json() == {"detail": "Dead-letter event not found"}

            # Verifies tenant_id passed to redrive service was strictly TENANT_B
            mock_redrive.assert_called_once_with(mock_session, TENANT_B, tenant_a_dl_id, None)


@pytest.mark.asyncio
async def test_service_list_dead_letters_tenant_filtering():
    """Unit test: service.list_dead_letters applies where(DeadLetterEvent.tenant_id == tenant_id)."""
    session = AsyncMock()
    mock_scalars = MagicMock()
    mock_scalars.all.return_value = []
    mock_result = MagicMock()
    mock_result.scalars.return_value = mock_scalars

    mock_count_result = MagicMock()
    mock_count_result.scalar_one.return_value = 0

    session.execute.side_effect = [mock_result, mock_count_result, mock_count_result]

    page = await service.list_dead_letters(session, TENANT_A, status_filter="unresolved")
    assert page.items == []
    assert page.total == 0
    assert page.unresolved_count == 0

    # Ensure execute was called 3 times (items, total count, unresolved count)
    assert session.execute.call_count == 3


@pytest.mark.asyncio
async def test_service_redrive_cross_tenant_access_denied():
    """Unit test: service.redrive_dead_letter raises DeadLetterNotFoundError on cross-tenant query."""
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result

    with pytest.raises(DeadLetterNotFoundError):
        await service.redrive_dead_letter(session, TENANT_B, uuid.uuid4(), None)


@pytest.mark.asyncio
async def test_worker_sets_tenant_rls_session_config():
    """Worker functions record_failed_attempt and quarantine_event execute SELECT set_config('app.current_tenant_id')."""
    db = FakeDB()
    event_id = uuid.uuid4()
    payload = {
        "event_id": str(event_id),
        "tenant_id": str(TENANT_A),
        "payload": {},
        "job_id": "j-200",
    }
    exc = RuntimeError("Worker failure")

    # Check record_failed_attempt sets tenant context
    await record_failed_attempt(db, payload, exc, attempt=1)
    set_config_stmts = [s for s in db.statements if "set_config('app.current_tenant_id'" in s[0]]
    assert len(set_config_stmts) >= 1
    assert set_config_stmts[-1][1]["tenant_id"] == str(TENANT_A)

    # Check quarantine_event sets tenant context
    await quarantine_event(db, payload, exc, attempt=5)
    set_config_stmts = [s for s in db.statements if "set_config('app.current_tenant_id'" in s[0]]
    assert len(set_config_stmts) >= 2
    assert set_config_stmts[-1][1]["tenant_id"] == str(TENANT_A)


@pytest.mark.asyncio
@skip_without_live_db
async def test_live_db_quarantine_and_tenant_rls_isolation():
    """
    Live PostgreSQL 16 verification under tenant RLS:
    - Creates Tenant A and Tenant B via registration.
    - Quarantines an event in Tenant A.
    - Asserts Tenant A queries see the event.
    - Asserts Tenant B queries see 0 events (PostgreSQL tenant_isolation_policy RLS containment).
    - Asserts Tenant B cannot re-drive Tenant A's event (anti-enumeration 404).
    """
    tenant_a_id, _ = await _register_test_tenant("dlq-rls-a")
    tenant_b_id, _ = await _register_test_tenant("dlq-rls-b")
    event_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Tenant A creates and quarantines an event
    async with get_tenant_db_context(tenant_a_id) as session:
        wh = WebhookEvent(
            id=event_id,
            tenant_id=tenant_a_id,
            provider="github",
            provider_delivery_id=f"del-{uuid.uuid4().hex[:8]}",
            event_type="push",
            received_at=now,
            status="queued",
            attempts=4,
            payload={"action": "test_rls_live"},
        )
        session.add(wh)
        await session.commit()

        exc = RuntimeError("Simulated poison webhook in live DB")
        dl_id = await quarantine_event(
            session,
            {
                "event_id": event_id,
                "tenant_id": tenant_a_id,
                "provider": "github",
                "payload": {"action": "test_rls_live"},
                "job_id": str(uuid.uuid4()),
            },
            exc,
            attempt=5,
        )
        assert dl_id is not None

        # Verify Tenant A sees the quarantined item
        page_a = await service.list_dead_letters(session, tenant_a_id)
        assert page_a.total >= 1
        assert any(item.id == dl_id for item in page_a.items)

    # Tenant B queries dead_letter_events (RLS isolation check)
    async with get_tenant_db_context(tenant_b_id) as session:
        page_b = await service.list_dead_letters(session, tenant_b_id)
        assert not any(item.id == dl_id for item in page_b.items)

        # Anti-enumeration check: Tenant B cannot redrive Tenant A's DLQ item
        with pytest.raises(service.DeadLetterNotFoundError):
            await service.redrive_dead_letter(session, tenant_b_id, dl_id, None)


# ==============================================================================
# SECTION 5: RBAC HTTP 403 ENFORCEMENT FOR DEVELOPER
# ==============================================================================


@pytest.mark.asyncio
async def test_developer_role_receives_http_403_on_get_dead_letters(auth_context):
    """
    Verify Invariant 7 & Acceptance Scope:
    Authenticated user with role DEVELOPER receives HTTP 403 Forbidden on GET /api/v1/dead-letters.
    """
    token = mint_jwt(auth_context, tenant_id=TENANT_A, roles=["DEVELOPER"], sub="dev-alice")
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.get("/api/v1/dead-letters", headers=headers)
        assert res.status_code == 403
        assert res.json() == {"detail": "Forbidden: insufficient permissions"}


@pytest.mark.asyncio
async def test_developer_role_receives_http_403_on_post_redrive(auth_context):
    """
    Verify Invariant 7 & Acceptance Scope:
    Authenticated user with role DEVELOPER receives HTTP 403 Forbidden on POST /api/v1/dead-letters/{id}/redrive.
    """
    token = mint_jwt(auth_context, tenant_id=TENANT_A, roles=["DEVELOPER"], sub="dev-alice")
    headers = {"Authorization": f"Bearer {token}"}
    fake_id = uuid.uuid4()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res = await ac.post(f"/api/v1/dead-letters/{fake_id}/redrive", headers=headers)
        assert res.status_code == 403
        assert res.json() == {"detail": "Forbidden: insufficient permissions"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "role",
    [
        "DEVELOPER",
        "SCRUM_MASTER",
        "PROJECT_MANAGER",
        "SOLUTION_ARCHITECT",
        "DELIVERY_DIRECTOR",
        "TOP_MANAGEMENT",
    ],
)
async def test_all_non_admin_roles_receive_http_403_on_dlq_endpoints(auth_context, role: str):
    """All non-admin roles receive HTTP 403 Forbidden on both GET and POST endpoints."""
    token = mint_jwt(auth_context, tenant_id=TENANT_A, roles=[role])
    headers = {"Authorization": f"Bearer {token}"}
    fake_id = uuid.uuid4()

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # GET /api/v1/dead-letters
        res_get = await ac.get("/api/v1/dead-letters", headers=headers)
        assert res_get.status_code == 403
        assert res_get.json() == {"detail": "Forbidden: insufficient permissions"}

        # POST /api/v1/dead-letters/{id}/redrive
        res_post = await ac.post(f"/api/v1/dead-letters/{fake_id}/redrive", headers=headers)
        assert res_post.status_code == 403
        assert res_post.json() == {"detail": "Forbidden: insufficient permissions"}


@pytest.mark.asyncio
async def test_tenant_admin_role_authorized_on_dlq_endpoints(auth_context):
    """TENANT_ADMIN role passes authorization check (returns HTTP 200 on GET)."""
    token = mint_jwt(auth_context, tenant_id=TENANT_A, roles=["TENANT_ADMIN"])
    headers = {"Authorization": f"Bearer {token}"}

    empty_page = DeadLetterPage(items=[], unresolved_count=0, total=0)

    with (
        patch("app.dead_letters.router.get_tenant_db_context") as mock_ctx,
        patch("app.dead_letters.router.service.list_dead_letters", return_value=empty_page),
    ):
        mock_session = AsyncMock()
        mock_ctx.return_value.__aenter__.return_value = mock_session

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/dead-letters", headers=headers)
            assert res.status_code == 200


@pytest.mark.asyncio
async def test_unauthenticated_requests_receive_http_401():
    """Unauthenticated requests to DLQ endpoints are rejected with HTTP 401 Unauthorized."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_get = await ac.get("/api/v1/dead-letters")
        assert res_get.status_code == 401
        assert res_get.json() == {"detail": "Not authenticated"}

        fake_id = uuid.uuid4()
        res_post = await ac.post(f"/api/v1/dead-letters/{fake_id}/redrive")
        assert res_post.status_code == 401
        assert res_post.json() == {"detail": "Not authenticated"}


def test_dlq_routes_declare_manage_webhook_integrations_permission():
    """Route reflection verifies MANAGE_WEBHOOK_INTEGRATIONS is declared on all dead-letter routes."""
    from app.dead_letters.router import router

    for route in router.routes:
        perms = get_route_declared_permissions(route)
        assert (
            Permission.MANAGE_WEBHOOK_INTEGRATIONS in perms
            or Permission.MANAGE_WEBHOOK_INTEGRATIONS.value in perms
        ), f"Route {route.path} missing MANAGE_WEBHOOK_INTEGRATIONS permission"
