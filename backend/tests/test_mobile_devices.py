from datetime import datetime, timezone
import json
from types import SimpleNamespace
import time
import uuid

from cryptography.hazmat.primitives.asymmetric import rsa
import httpx
from httpx import ASGITransport, AsyncClient
import jwt
from jwt.algorithms import RSAAlgorithm
from pydantic import ValidationError
import pytest
from sqlalchemy import func, select

from app.auth.jwks import JWKSClient
from app.config import settings
from app.db.models.models import MobileDevice
from app.db.session import async_engine, get_tenant_db_context
from app.main import app
from app.mobile.schemas import RegisterMobileDeviceRequest
from app.mobile.service import resolve_or_create_mobile_device
from app.tenants.rate_limiter import registration_rate_limiter


@pytest.fixture(autouse=True)
async def reset_mobile_test_state():
    """Reset registration rate limiter and dispose DB connections between tests."""
    registration_rate_limiter.reset()
    yield
    await async_engine.dispose()
    registration_rate_limiter.reset()


class MockJWKSServer:
    def __init__(self, jwk: dict[str, object]) -> None:
        self.jwk = jwk

    async def handle_request(self, request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [self.jwk]}, request=request)


@pytest.fixture
def mobile_auth_fixture():
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk.update({"kid": "mobile-test-key", "kty": "RSA", "alg": "RS256", "use": "sig"})
    idp = MockJWKSServer(jwk)
    client = JWKSClient(
        jwks_url="http://mock-idp/certs",
        ttl_seconds=300.0,
        transport=httpx.MockTransport(idp.handle_request),
    )
    original_client = getattr(app.state, "jwks_client", None)
    app.state.jwks_client = client
    yield private_key
    app.state.jwks_client = original_client


async def register_test_tenant() -> tuple[uuid.UUID, uuid.UUID, str]:
    unique = uuid.uuid4().hex[:8]
    admin_email = f"mobile-admin-{unique}@manta.local"
    payload = {
        "name": f"Mobile Test {unique}",
        "slug": f"mobile-test-{unique}",
        "admin_email": admin_email,
        "admin_name": f"Mobile Admin {unique}",
    }

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/tenants/register", json=payload)

    assert response.status_code == 201, response.text
    data = response.json()
    return uuid.UUID(data["tenant_id"]), uuid.UUID(data["admin_id"]), admin_email


def create_mobile_auth_token(
    private_key: rsa.RSAPrivateKey,
    tenant_id: uuid.UUID,
    user_id: uuid.UUID | str,
    email: str | None,
) -> str:
    now = int(time.time())
    claims: dict[str, object] = {
        "sub": str(user_id),
        "tenant_id": str(tenant_id),
        "aud": settings.OIDC_AUDIENCE,
        "iss": f"{settings.KEYCLOAK_URL}/realms/{settings.KEYCLOAK_REALM}",
        "exp": now + 3600,
        "realm_access": {"roles": ["TENANT_ADMIN"]},
    }
    if email is not None:
        claims["email"] = email
    return jwt.encode(
        claims,
        private_key,
        algorithm="RS256",
        headers={"kid": "mobile-test-key", "alg": "RS256"},
    )


async def post_mobile_device(
    client: AsyncClient, token: str, fcm_token: str, **metadata: str
) -> httpx.Response:
    return await client.post(
        "/api/v1/mobile/devices",
        headers={"Authorization": f"Bearer {token}"},
        json={"fcm_token": fcm_token, **metadata},
    )


def test_register_mobile_device_request_defaults_and_trims_metadata() -> None:
    request = RegisterMobileDeviceRequest(
        fcm_token="fcm-registration-token",
        device_name="  Pixel  ",
        app_version="  1.2.3  ",
    )

    assert request.platform == "android"
    assert request.device_name == "Pixel"
    assert request.app_version == "1.2.3"


def test_register_mobile_device_request_accepts_field_limits() -> None:
    request = RegisterMobileDeviceRequest(
        fcm_token="t" * 512,
        platform="p" * 32,
        device_name="d" * 128,
        app_version="v" * 32,
    )

    assert len(request.fcm_token) == 512
    assert len(request.device_name or "") == 128


@pytest.mark.parametrize(
    "payload",
    [
        {"fcm_token": " "},
        {"fcm_token": " token "},
        {"fcm_token": "t" * 513},
        {"fcm_token": "token", "platform": " "},
        {"fcm_token": "token", "device_name": " "},
        {"fcm_token": "token", "app_version": "v" * 33},
        {"fcm_token": "token", "tenant_id": "client-supplied"},
    ],
)
def test_register_mobile_device_request_rejects_invalid_values(
    payload: dict[str, str],
) -> None:
    with pytest.raises(ValidationError):
        RegisterMobileDeviceRequest(**payload)


@pytest.mark.asyncio
async def test_register_mobile_device_returns_only_non_critical_device_data() -> None:
    now = datetime.now(timezone.utc)
    tenant_id = uuid.uuid4()
    user_id = uuid.uuid4()
    device = SimpleNamespace(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        user_id=user_id,
        fcm_token="sensitive-fcm-token",
        platform="ios",
        device_name="iPhone",
        app_version="1.2.3",
        is_active=True,
        last_seen_at=now,
        created_at=now,
        updated_at=now,
    )

    class FakeSession:
        async def execute(self, _statement: object) -> SimpleNamespace:
            assert _statement is not None
            return SimpleNamespace(scalar_one=lambda: device)

    response = await resolve_or_create_mobile_device(
        FakeSession(),  # type: ignore[arg-type]
        tenant_id,
        user_id,
        RegisterMobileDeviceRequest(fcm_token="sensitive-fcm-token", platform="ios"),
    )

    assert response.platform == "ios"
    assert response.device_name == "iPhone"
    assert response.model_dump() == {
        "id": device.id,
        "platform": "ios",
        "device_name": "iPhone",
        "app_version": "1.2.3",
        "is_active": True,
        "last_seen_at": now,
        "created_at": now,
        "updated_at": now,
    }


@pytest.mark.asyncio
async def test_mobile_device_endpoints_require_authentication() -> None:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        post_resp = await client.post(
            "/api/v1/mobile/devices",
            json={"fcm_token": "unauthenticated-token"},
        )
        delete_resp = await client.delete("/api/v1/mobile/devices/unauthenticated-token")

    assert post_resp.status_code == 401
    assert post_resp.json() == {"detail": "Not authenticated"}
    assert delete_resp.status_code == 401
    assert delete_resp.json() == {"detail": "Not authenticated"}


@pytest.mark.asyncio
async def test_register_mobile_device_is_idempotent(mobile_auth_fixture) -> None:
    private_key = mobile_auth_fixture
    tenant_id, user_id, email = await register_test_tenant()
    token = create_mobile_auth_token(private_key, tenant_id, user_id, email)
    fcm_token = "idempotent-mobile-token"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await post_mobile_device(
            client,
            token,
            fcm_token,
            platform="android",
            device_name="Pixel",
            app_version="1.0.0",
        )
        second = await post_mobile_device(
            client,
            token,
            fcm_token,
            platform="ios",
            device_name="iPhone",
            app_version="2.0.0",
        )

    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    first_data = first.json()
    second_data = second.json()
    assert second_data["id"] == first_data["id"]
    assert second_data["platform"] == "ios"
    assert second_data["device_name"] == "iPhone"
    assert second_data["app_version"] == "2.0.0"
    assert datetime.fromisoformat(second_data["last_seen_at"]) >= datetime.fromisoformat(
        first_data["last_seen_at"]
    )

    async with get_tenant_db_context(tenant_id) as session:
        count = await session.scalar(
            select(func.count())
            .select_from(MobileDevice)
            .where(
                MobileDevice.tenant_id == tenant_id,
                MobileDevice.user_id == user_id,
                MobileDevice.fcm_token == fcm_token,
            )
        )
    assert count == 1


@pytest.mark.asyncio
async def test_mobile_device_rls_hides_registration_from_another_tenant(
    mobile_auth_fixture,
) -> None:
    private_key = mobile_auth_fixture
    tenant_a_id, user_a_id, email_a = await register_test_tenant()
    tenant_b_id, _, _ = await register_test_tenant()
    token_a = create_mobile_auth_token(private_key, tenant_a_id, user_a_id, email_a)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await post_mobile_device(client, token_a, "tenant-isolation-token")

    assert response.status_code == 200, response.text
    device_id = uuid.UUID(response.json()["id"])

    async with get_tenant_db_context(tenant_b_id) as session:
        visible_device = await session.scalar(
            select(MobileDevice).where(MobileDevice.id == device_id)
        )

    assert visible_device is None


@pytest.mark.asyncio
async def test_delete_mobile_device_removes_registration(mobile_auth_fixture) -> None:
    private_key = mobile_auth_fixture
    tenant_id, user_id, email = await register_test_tenant()
    token = create_mobile_auth_token(private_key, tenant_id, user_id, email)
    fcm_token = "delete-mobile-token"

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        registered = await post_mobile_device(client, token, fcm_token)
        assert registered.status_code == 200, registered.text
        deleted = await client.delete(
            f"/api/v1/mobile/devices/{fcm_token}",
            headers={"Authorization": f"Bearer {token}"},
        )
        repeated_delete = await client.delete(
            f"/api/v1/mobile/devices/{fcm_token}",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert deleted.status_code == 204
    assert repeated_delete.status_code == 204
    async with get_tenant_db_context(tenant_id) as session:
        device = await session.scalar(
            select(MobileDevice).where(
                MobileDevice.tenant_id == tenant_id,
                MobileDevice.user_id == user_id,
                MobileDevice.fcm_token == fcm_token,
            )
        )
    assert device is None


@pytest.mark.asyncio
async def test_mobile_device_resolves_non_uuid_keycloak_sub_and_distinct_google_sub(
    mobile_auth_fixture,
) -> None:
    """Keycloak subjects (non-UUID strings like `user-mobile-test` or IdP UUIDs different from
    `users.id`) must resolve to the provisioned `users.id` row instead of failing with 400 or
    violating the `mobile_devices.user_id` foreign key."""
    from app.db.models.models import User

    private_key = mobile_auth_fixture
    tenant_id, _, _ = await register_test_tenant()

    # 1. Seed a user mimicking realm-export.json (non-UUID google_sub)
    string_sub_user_id = uuid.uuid4()
    string_sub = f"user-mobile-test-{uuid.uuid4().hex[:6]}"
    string_sub_email = f"{string_sub}@manta.local"

    # 2. Seed a user mimicking /tenants/register or /invitations/accept (UUID google_sub != users.id)
    uuid_sub_user_id = uuid.uuid4()
    distinct_kc_sub = str(uuid.uuid4())
    uuid_sub_email = f"kc-user-{uuid.uuid4().hex[:6]}@manta.local"

    async with get_tenant_db_context(tenant_id) as session:
        session.add_all(
            [
                User(
                    id=string_sub_user_id,
                    tenant_id=tenant_id,
                    email=string_sub_email,
                    full_name="Mobile Test User",
                    google_sub=string_sub,
                    role="SCRUM_MASTER",
                    is_active=True,
                ),
                User(
                    id=uuid_sub_user_id,
                    tenant_id=tenant_id,
                    email=uuid_sub_email,
                    full_name="Invited Mobile User",
                    google_sub=distinct_kc_sub,
                    role="DEVELOPER",
                    is_active=True,
                ),
            ]
        )

    token_string_sub = create_mobile_auth_token(
        private_key, tenant_id, string_sub, string_sub_email
    )
    token_uuid_sub = create_mobile_auth_token(
        private_key, tenant_id, distinct_kc_sub, uuid_sub_email
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp_str = await post_mobile_device(client, token_string_sub, "fcm-non-uuid-sub")
        resp_uuid = await post_mobile_device(client, token_uuid_sub, "fcm-distinct-uuid-sub")
        del_str = await client.delete(
            "/api/v1/mobile/devices/fcm-non-uuid-sub",
            headers={"Authorization": f"Bearer {token_string_sub}"},
        )

    assert resp_str.status_code == 200, resp_str.text
    assert resp_uuid.status_code == 200, resp_uuid.text
    assert del_str.status_code == 204

    async with get_tenant_db_context(tenant_id) as session:
        deleted_row = await session.scalar(
            select(MobileDevice).where(
                MobileDevice.tenant_id == tenant_id,
                MobileDevice.user_id == string_sub_user_id,
                MobileDevice.fcm_token == "fcm-non-uuid-sub",
            )
        )
        saved_row = await session.scalar(
            select(MobileDevice).where(
                MobileDevice.tenant_id == tenant_id,
                MobileDevice.user_id == uuid_sub_user_id,
                MobileDevice.fcm_token == "fcm-distinct-uuid-sub",
            )
        )

    assert deleted_row is None
    assert saved_row is not None
    assert saved_row.user_id == uuid_sub_user_id


@pytest.mark.asyncio
async def test_mobile_device_returns_404_on_post_and_204_on_delete_for_unprovisioned_user(
    mobile_auth_fixture,
) -> None:
    private_key = mobile_auth_fixture
    tenant_id, _, _ = await register_test_tenant()
    missing_token = create_mobile_auth_token(
        private_key,
        tenant_id,
        f"missing-user-{uuid.uuid4().hex[:6]}",
        f"missing-{uuid.uuid4().hex[:6]}@manta.local",
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        post_resp = await post_mobile_device(client, missing_token, "fcm-unprovisioned-user")
        del_resp = await client.delete(
            "/api/v1/mobile/devices/fcm-unprovisioned-user",
            headers={"Authorization": f"Bearer {missing_token}"},
        )

    assert post_resp.status_code == 404
    assert del_resp.status_code == 204
