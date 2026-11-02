"""
SCRUM-288 & SCRUM-289: Comprehensive test suite for tenant registration and SECURITY DEFINER provisioning function.

Verifies:
1. SCRUM-288 AC 1 & 2: SECURITY DEFINER provisioning function with pinned search_path = public, pg_temp.
2. SCRUM-288 AC 3: Direct INSERT on tenants table is denied for eng_app_user (least-privilege).
3. SCRUM-288 AC 4: Function execution is granted to eng_app_user and revoked from PUBLIC.
4. SCRUM-288 AC 5: Atomic creation of tenant, admin user (TENANT_ADMIN), and user_roles record.
5. SCRUM-289 AC 1 & 2: Unauthenticated POST /tenants/register endpoint accessible without JWT or x-tenant-id.
6. SCRUM-289 AC 3 & 4: Pydantic request validation and automatic slug derivation.
7. SCRUM-289 AC 5: Returns HTTP 201 Created with valid tenant and admin IDs.
8. SCRUM-289 AC 6: Duplicate slug returns clear HTTP 409 Conflict.
9. SCRUM-289 AC 7: Sliding-window rate limiter returns HTTP 429 Too Many Requests with Retry-After header.
10. Atomicity guarantee: Rolled-back / failed provisioning leaves no orphaned rows.
"""

import uuid
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.session import AsyncSessionLocal, async_engine, get_tenant_db_context
from app.main import app
from app.tenants.rate_limiter import registration_rate_limiter


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """Reset rate limiter state before and after each test."""
    registration_rate_limiter.reset()
    yield
    registration_rate_limiter.reset()


@pytest.mark.asyncio
async def test_register_tenant_success_201():
    """
    SCRUM-289: Register a new organization and administrator with full payload.
    Asserts HTTP 201 Created and verifies database records are created atomically.
    """
    unique_suffix = uuid.uuid4().hex[:8]
    slug = f"acme-corp-{unique_suffix}"
    email = f"admin@{unique_suffix}.acme.com"

    payload = {
        "name": f"Acme Corporation {unique_suffix}",
        "slug": slug,
        "admin_email": email,
        "admin_name": "Alice Administrator",
        "allowed_domain": "acme.com",
    }

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/tenants/register", json=payload)

        assert response.status_code == 201, response.text
        data = response.json()

        assert data["name"] == payload["name"]
        assert data["slug"] == slug
        assert data["admin_email"] == email.lower()
        assert "tenant_id" in data
        assert "admin_id" in data
        tenant_id = uuid.UUID(data["tenant_id"])
        admin_id = uuid.UUID(data["admin_id"])

        # Verify atomic DB persistence under tenant context (RLS-isolated)
        async with get_tenant_db_context(tenant_id) as session:
            t_res = await session.execute(
                text("SELECT id, name, slug, allowed_domain, status FROM tenants WHERE id = :id"),
                {"id": tenant_id},
            )
            tenant_row = t_res.mappings().first()
            assert tenant_row is not None
            assert tenant_row["slug"] == slug
            assert tenant_row["status"] == "active"

            u_res = await session.execute(
                text(
                    "SELECT id, tenant_id, email, full_name, role, is_active FROM users WHERE id = :id"
                ),
                {"id": admin_id},
            )
            user_row = u_res.mappings().first()
            assert user_row is not None
            assert user_row["tenant_id"] == tenant_id
            assert user_row["email"] == email.lower()
            assert user_row["role"] == "TENANT_ADMIN"
            assert user_row["is_active"] is True

            r_res = await session.execute(
                text("SELECT role_id FROM user_roles WHERE user_id = :uid AND tenant_id = :tid"),
                {"uid": admin_id, "tid": tenant_id},
            )
            role_row = r_res.mappings().first()
            assert role_row is not None
            assert role_row["role_id"] == "TENANT_ADMIN"
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_register_tenant_auto_slug_generation():
    """
    SCRUM-289: Omitting explicit slug automatically slugifies organization name.
    """
    unique_suffix = uuid.uuid4().hex[:6]
    name = f"Beta Innovations {unique_suffix}"
    email = f"lead@{unique_suffix}.beta.org"

    payload = {
        "name": name,
        "admin_email": email,
        "admin_name": "Bob Builder",
    }

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post("/tenants/register", json=payload)

        assert response.status_code == 201, response.text
        data = response.json()
        assert data["slug"].startswith("beta-innovations-")
        assert data["admin_email"] == email.lower()
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_register_tenant_duplicate_slug_returns_409():
    """
    SCRUM-289: Registering an organization with an already existing slug returns HTTP 409 Conflict.
    """
    unique_suffix = uuid.uuid4().hex[:8]
    slug = f"unique-org-{unique_suffix}"

    payload = {
        "name": "First Organization",
        "slug": slug,
        "admin_email": f"first@{unique_suffix}.com",
        "admin_name": "First Admin",
    }

    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            # 1. First registration succeeds
            res1 = await ac.post("/tenants/register", json=payload)
            assert res1.status_code == 201

            # 2. Second registration with same slug fails with 409
            payload2 = {
                "name": "Second Organization With Same Slug",
                "slug": slug,
                "admin_email": f"second@{unique_suffix}.com",
                "admin_name": "Second Admin",
            }
            res2 = await ac.post("/tenants/register", json=payload2)
            assert res2.status_code == 409
            data2 = res2.json()
            assert f"slug '{slug}' already exists" in data2["detail"]
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_register_tenant_validation_errors_422():
    """
    SCRUM-289: Pydantic input validation rejects invalid payload components with 422.
    """
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        # Invalid email
        res_email = await ac.post(
            "/tenants/register",
            json={
                "name": "Bad Email Corp",
                "slug": "bad-email",
                "admin_email": "not-an-email",
                "admin_name": "Test User",
            },
        )
        assert res_email.status_code == 422

        # Invalid slug (uppercase & illegal chars)
        res_slug = await ac.post(
            "/tenants/register",
            json={
                "name": "Bad Slug Corp",
                "slug": "Bad_Slug!",
                "admin_email": "admin@badslug.com",
                "admin_name": "Test User",
            },
        )
        assert res_slug.status_code == 422

        # Blank name
        res_name = await ac.post(
            "/tenants/register",
            json={
                "name": "   ",
                "admin_email": "admin@blankname.com",
                "admin_name": "Test User",
            },
        )
        assert res_name.status_code == 422


@pytest.mark.asyncio
async def test_register_tenant_rate_limiting_429():
    """
    SCRUM-289: Exceeding 10 registration requests per IP within the window returns HTTP 429
    with Retry-After header.
    """
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            client_ip = "198.51.100.42"
            headers = {"X-Forwarded-For": client_ip}

            # Send 10 valid requests (with unique slugs)
            for _ in range(10):
                uid = uuid.uuid4().hex[:6]
                res = await ac.post(
                    "/tenants/register",
                    json={
                        "name": f"Rate Limit Org {uid}",
                        "slug": f"rate-limit-org-{uid}",
                        "admin_email": f"admin@{uid}.ratelimit.test",
                        "admin_name": "Rate Limiter",
                    },
                    headers=headers,
                )
                assert res.status_code == 201

            # 11th request must be rejected with 429 Too Many Requests
            uid = uuid.uuid4().hex[:6]
            res_blocked = await ac.post(
                "/tenants/register",
                json={
                    "name": f"Rate Limit Org {uid}",
                    "slug": f"rate-limit-org-{uid}",
                    "admin_email": f"admin@{uid}.ratelimit.test",
                    "admin_name": "Rate Limiter",
                },
                headers=headers,
            )
            assert res_blocked.status_code == 429
            assert "Retry-After" in res_blocked.headers
            assert int(res_blocked.headers["Retry-After"]) >= 1
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_direct_insert_tenants_denied_for_eng_app_user():
    """
    SCRUM-288 AC 3: Verify eng_app_user cannot insert into the tenants table directly.
    Permission denied must be raised.
    """
    try:
        async with AsyncSessionLocal() as session:
            # Check current database user is eng_app_user
            current_user_res = await session.execute(text("SELECT current_user;"))
            current_db_user = current_user_res.scalar()
            assert current_db_user == "eng_app_user", (
                f"Expected eng_app_user, got {current_db_user}"
            )

            # Attempt direct INSERT into tenants table
            with pytest.raises(DBAPIError) as exc_info:
                await session.execute(
                    text(
                        """
                        INSERT INTO tenants (id, slug, name, tier, status, max_projects, ai_processing_mode, retention_days, created_at, updated_at)
                        VALUES (gen_random_uuid(), 'illegal-direct-tenant', 'Illegal Tenant', 'starter', 'active', 5, 'cloud_zdr', 90, now(), now());
                        """
                    )
                )
            await session.rollback()

            assert "permission denied for table tenants" in str(exc_info.value).lower()
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_security_definer_function_attributes():
    """
    SCRUM-288 AC 1, 2, 4: Verify provision_tenant function metadata:
    - SECURITY DEFINER (prosecdef is true)
    - search_path = public, pg_temp (proconfig contains search_path)
    - Privileges: eng_app_user has EXECUTE, PUBLIC does not have EXECUTE
    """
    try:
        async with AsyncSessionLocal() as session:
            # 1. Check prosecdef and proconfig in pg_proc
            res = await session.execute(
                text(
                    """
                    SELECT p.prosecdef, p.proconfig
                    FROM pg_proc p
                    JOIN pg_namespace n ON p.pronamespace = n.oid
                    WHERE n.nspname = 'public' AND p.proname = 'provision_tenant';
                    """
                )
            )
            row = res.mappings().first()
            assert row is not None, "provision_tenant function not found in public schema"
            assert row["prosecdef"] is True, "Function must be marked SECURITY DEFINER"
            config_list = row["proconfig"] or []
            assert any("search_path=public, pg_temp" in cfg for cfg in config_list), (
                f"Expected search_path=public, pg_temp in proconfig, got {config_list}"
            )

            # 2. Check execute privileges
            perm_res = await session.execute(
                text(
                    """
                    SELECT
                        has_function_privilege('public', 'provision_tenant(varchar,varchar,varchar,varchar,varchar,varchar,varchar,varchar,uuid,uuid)', 'EXECUTE') AS public_exec,
                        has_function_privilege('eng_app_user', 'provision_tenant(varchar,varchar,varchar,varchar,varchar,varchar,varchar,varchar,uuid,uuid)', 'EXECUTE') AS app_user_exec;
                    """
                )
            )
            perms = perm_res.mappings().first()
            assert perms["public_exec"] is False, (
                "PUBLIC should not have EXECUTE on provision_tenant"
            )
            assert perms["app_user_exec"] is True, (
                "eng_app_user must have EXECUTE on provision_tenant"
            )
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_security_definer_atomicity_on_failure():
    """
    SCRUM-288 AC 5: When an error occurs during provisioning (e.g. empty admin name),
    the transaction is rolled back and no partial tenant or user records remain.
    """
    test_slug = f"atomic-rollback-{uuid.uuid4().hex[:6]}"

    try:
        async with AsyncSessionLocal() as session:
            with pytest.raises(DBAPIError) as exc_info:
                await session.execute(
                    text(
                        """
                        SELECT * FROM provision_tenant(
                            p_name => 'Atomic Test Corp',
                            p_slug => :slug,
                            p_admin_email => 'admin@atomic.com',
                            p_admin_name => ''
                        );
                        """
                    ),
                    {"slug": test_slug},
                )
            await session.rollback()

            assert "administrator name cannot be empty" in str(exc_info.value).lower()

            # Verify tenant row was not inserted
            check_res = await session.execute(
                text("SELECT count(*) FROM tenants WHERE slug = :slug"),
                {"slug": test_slug},
            )
            assert check_res.scalar() == 0
    finally:
        await async_engine.dispose()
