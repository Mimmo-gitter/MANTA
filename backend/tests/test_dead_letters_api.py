"""
Task 23.4: GET /api/v1/dead-letters and POST /api/v1/dead-letters/{id}/redrive test suite.

Verifies:
1. RBAC enforcement:
   - Unauthenticated requests return HTTP 401.
   - DEVELOPER (and other non-admin roles) receive HTTP 403 Forbidden.
   - TENANT_ADMIN requests are authorized.
2. GET /api/v1/dead-letters:
   - Returns list of dead-letter items, unresolved_count, total, limit, offset.
   - Respects status filter ("unresolved" vs "all") and pagination.
3. POST /api/v1/dead-letters/{id}/redrive:
   - Resets webhook_events (attempts=0, status='queued', error_message=None).
   - Marks dead_letter_events (is_resolved=True, resolved_by=user_id, resolved_at=now()).
   - Pushes job envelope to arq:queue:high with priority="high", attempt=0.
   - Returns HTTP 202 Accepted.
   - Safe degradation when Redis is temporarily unavailable (returns enqueued=False).
   - Returns HTTP 404 when dead-letter event does not exist or belongs to another tenant.
   - Returns HTTP 409 when dead-letter event was already resolved.
"""

from collections import defaultdict
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

from app.auth.jwks import JWKSClient
from app.auth.permissions import Permission, get_route_declared_permissions
from app.config import settings
from app.dead_letters import service
from app.dead_letters.schemas import DeadLetterItem
from app.dead_letters.service import (
    DeadLetterAlreadyResolvedError,
    DeadLetterNotFoundError,
    DeadLetterPage,
    RedriveResult,
)
from app.main import app
from app.webhooks import enqueue as enqueue_mod
from workers.worker import QUEUE_HIGH

TENANT_A = uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TENANT_B = uuid.UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")


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
    key, jwk = generate_rsa_keypair("dlq-test-key")
    mock_idp = MockJWKSServer({"keys": [jwk]})

    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(mock_idp.handle_request),
    )

    original_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = client

    yield {"key": key, "kid": "dlq-test-key"}

    app.state.jwks_client = original_client


def mint_jwt(
    auth_ctx: dict[str, Any],
    tenant_id: uuid.UUID | str,
    roles: list[str] | None = None,
    sub: str = "dlq-admin-user",
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


class FakeRedis:
    def __init__(self) -> None:
        self.lists: dict[str, list[str]] = defaultdict(list)

    async def rpush(self, key: str, *values: str) -> int:
        self.lists[key].extend(values)
        return len(self.lists[key])


# ==============================================================================
# RBAC TESTS (Acceptance Criteria: DEVELOPER receives HTTP 403 Forbidden)
# ==============================================================================


@pytest.mark.asyncio
async def test_dlq_endpoints_require_authentication():
    """Unauthenticated GET and POST requests are rejected with HTTP 401."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        res_get = await ac.get("/api/v1/dead-letters")
        assert res_get.status_code == 401
        assert res_get.json() == {"detail": "Not authenticated"}

        fake_id = uuid.uuid4()
        res_post = await ac.post(f"/api/v1/dead-letters/{fake_id}/redrive")
        assert res_post.status_code == 401
        assert res_post.json() == {"detail": "Not authenticated"}


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
async def test_dlq_endpoints_forbidden_for_non_admins(auth_context, role: str):
    """
    Non-admin roles (specifically DEVELOPER) receive HTTP 403 Forbidden.
    Enforces require_permission(Permission.MANAGE_WEBHOOK_INTEGRATIONS).
    """
    token = mint_jwt(auth_context, tenant_id=TENANT_A, roles=[role])
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # GET /api/v1/dead-letters
        res_get = await ac.get("/api/v1/dead-letters", headers=headers)
        assert res_get.status_code == 403
        assert res_get.json() == {"detail": "Forbidden: insufficient permissions"}

        # POST /api/v1/dead-letters/{id}/redrive
        fake_id = uuid.uuid4()
        res_post = await ac.post(f"/api/v1/dead-letters/{fake_id}/redrive", headers=headers)
        assert res_post.status_code == 403
        assert res_post.json() == {"detail": "Forbidden: insufficient permissions"}


def test_dlq_routes_declare_manage_webhook_integrations_permission():
    """Verifies route reflection confirms MANAGE_WEBHOOK_INTEGRATIONS is declared."""
    from app.dead_letters.router import router

    for route in router.routes:
        perms = get_route_declared_permissions(route)
        assert (
            Permission.MANAGE_WEBHOOK_INTEGRATIONS in perms
            or Permission.MANAGE_WEBHOOK_INTEGRATIONS.value in perms
        ), f"Route {route.path} missing MANAGE_WEBHOOK_INTEGRATIONS permission"


# ==============================================================================
# GET /api/v1/dead-letters TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_list_dead_letters_success(auth_context):
    """TENANT_ADMIN can list dead-letter items and view unresolved_count."""
    token = mint_jwt(auth_context, tenant_id=TENANT_A, roles=["TENANT_ADMIN"])
    headers = {"Authorization": f"Bearer {token}"}

    dl_id = uuid.uuid4()
    orig_event_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    mock_item = DeadLetterItem(
        id=dl_id,
        original_event_id=orig_event_id,
        provider="github",
        error_class="builtins.ValueError",
        stack_trace="Traceback...",
        total_attempts=5,
        payload={"event": "push"},
        quarantined_at=now,
        is_resolved=False,
        resolved_by=None,
        resolved_at=None,
    )

    mock_page = DeadLetterPage(items=[mock_item], unresolved_count=1, total=1)

    with (
        patch("app.dead_letters.router.get_tenant_db_context") as mock_ctx,
        patch(
            "app.dead_letters.router.service.list_dead_letters", return_value=mock_page
        ) as mock_list,
    ):
        mock_session = AsyncMock()
        mock_ctx.return_value.__aenter__.return_value = mock_session

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get(
                "/api/v1/dead-letters?status=unresolved&limit=10&offset=0", headers=headers
            )
            assert res.status_code == 200
            data = res.json()

            assert data["unresolved_count"] == 1
            assert data["total"] == 1
            assert data["limit"] == 10
            assert data["offset"] == 0
            assert len(data["items"]) == 1
            assert data["items"][0]["id"] == str(dl_id)
            assert data["items"][0]["original_event_id"] == str(orig_event_id)
            assert data["items"][0]["total_attempts"] == 5
            assert data["items"][0]["is_resolved"] is False

            mock_list.assert_called_once_with(
                mock_session,
                TENANT_A,
                status_filter="unresolved",
                limit=10,
                offset=0,
            )


@pytest.mark.asyncio
async def test_list_dead_letters_status_all_filter(auth_context):
    """Status filter 'all' is forwarded to the query service."""
    token = mint_jwt(auth_context, tenant_id=TENANT_A, roles=["TENANT_ADMIN"])
    headers = {"Authorization": f"Bearer {token}"}

    mock_page = DeadLetterPage(items=[], unresolved_count=3, total=15)

    with (
        patch("app.dead_letters.router.get_tenant_db_context") as mock_ctx,
        patch(
            "app.dead_letters.router.service.list_dead_letters", return_value=mock_page
        ) as mock_list,
    ):
        mock_session = AsyncMock()
        mock_ctx.return_value.__aenter__.return_value = mock_session

        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            res = await ac.get("/api/v1/dead-letters?status=all", headers=headers)
            assert res.status_code == 200
            data = res.json()
            assert data["unresolved_count"] == 3
            assert data["total"] == 15

            mock_list.assert_called_once_with(
                mock_session,
                TENANT_A,
                status_filter="all",
                limit=50,
                offset=0,
            )


# ==============================================================================
# POST /api/v1/dead-letters/{id}/redrive TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_redrive_success_enqueues_high_priority(auth_context):
    """
    POST /api/v1/dead-letters/{id}/redrive:
    - Successfully resolves DLQ item and resets event to 'queued' with attempts=0.
    - Pushes canonical envelope to arq:queue:high with priority="high" and attempt=0.
    - Returns HTTP 202 with enqueued=True.
    """
    fake_redis = FakeRedis()
    enqueue_mod.set_redis(fake_redis)

    token = mint_jwt(auth_context, tenant_id=TENANT_A, roles=["TENANT_ADMIN"], sub="admin-sub")
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
        payload={"ref": "refs/heads/main"},
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

            mock_resolve.assert_called_once_with(
                mock_session, TENANT_A, "admin-sub", "admin-sub@example.com"
            )
            mock_redrive.assert_called_once_with(mock_session, TENANT_A, dl_id, resolver_id)

    # Check high-priority queue content in Redis
    assert len(fake_redis.lists[QUEUE_HIGH]) == 1
    raw_envelope = fake_redis.lists[QUEUE_HIGH][0]
    envelope = json.loads(raw_envelope)
    assert envelope["priority"] == "high"
    assert envelope["attempt"] == 0
    assert envelope["event_id"] == str(event_id)
    assert envelope["tenant_id"] == str(TENANT_A)
    assert envelope["provider"] == "github"
    assert envelope["payload"] == {"ref": "refs/heads/main"}

    enqueue_mod.set_redis(None)


@pytest.mark.asyncio
async def test_redrive_redis_down_graceful_degradation(auth_context):
    """
    If Redis is down during redrive:
    - DB transaction is already committed (event is queued with attempts=0).
    - Endpoint returns HTTP 202 with enqueued=False instead of crashing with HTTP 500.
    """
    broken_redis = AsyncMock()
    broken_redis.rpush.side_effect = RedisError("Connection refused")
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
        payload={"action": "retest"},
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


@pytest.mark.asyncio
async def test_redrive_not_found(auth_context):
    """Non-existent DLQ id (or cross-tenant) returns HTTP 404 Not Found."""
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
async def test_redrive_already_resolved_conflict(auth_context):
    """Re-driving an already-resolved DLQ item returns HTTP 409 Conflict."""
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


# ==============================================================================
# SERVICE UNIT TESTS
# ==============================================================================


@pytest.mark.asyncio
async def test_service_redrive_not_found():
    """service.redrive_dead_letter raises DeadLetterNotFoundError when row is missing."""
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    session.execute.return_value = mock_result

    with pytest.raises(DeadLetterNotFoundError):
        await service.redrive_dead_letter(session, TENANT_A, uuid.uuid4(), None)


@pytest.mark.asyncio
async def test_service_redrive_already_resolved():
    """service.redrive_dead_letter raises DeadLetterAlreadyResolvedError when already resolved."""
    session = AsyncMock()
    mock_dl = MagicMock()
    mock_dl.is_resolved = True
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = mock_dl
    session.execute.return_value = mock_result

    with pytest.raises(DeadLetterAlreadyResolvedError):
        await service.redrive_dead_letter(session, TENANT_A, uuid.uuid4(), None)


@pytest.mark.asyncio
async def test_service_resolve_user_id():
    """resolve_user_id queries User by sub/email/uuid."""
    session = AsyncMock()
    user_id = uuid.uuid4()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = user_id
    session.execute.return_value = mock_result

    found = await service.resolve_user_id(session, TENANT_A, str(user_id), "admin@example.com")
    assert found == user_id


@pytest.mark.asyncio
async def test_service_redrive_success():
    """service.redrive_dead_letter updates WebhookEvent and DeadLetterEvent and returns RedriveResult."""
    session = AsyncMock()
    dl_id = uuid.uuid4()
    orig_event_id = uuid.uuid4()
    resolver_id = uuid.uuid4()

    mock_dl = MagicMock()
    mock_dl.id = dl_id
    mock_dl.original_event_id = orig_event_id
    mock_dl.provider = "github"
    mock_dl.is_resolved = False
    mock_dl.payload = {"ref": "main"}

    mock_dl_result = MagicMock()
    mock_dl_result.scalar_one_or_none.return_value = mock_dl

    mock_wh_result = MagicMock()
    mock_wh_result.first.return_value = ("push",)

    mock_update_dl_result = MagicMock()

    session.execute.side_effect = [mock_dl_result, mock_wh_result, mock_update_dl_result]

    result = await service.redrive_dead_letter(session, TENANT_A, dl_id, resolver_id)

    assert result.dead_letter_id == dl_id
    assert result.event_id == orig_event_id
    assert result.tenant_id == TENANT_A
    assert result.provider == "github"
    assert result.event_type == "push"
    assert result.payload == {"ref": "main"}
    assert session.execute.call_count == 3
