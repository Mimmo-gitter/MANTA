import uuid
import pytest
from fastapi import Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import (
    async_engine,
    get_current_tenant_id,
    get_tenant_db,
    get_tenant_db_context,
)

TENANT_ALPHA = uuid.UUID("11111111-1111-1111-1111-111111111111")
TENANT_BETA = uuid.UUID("22222222-2222-2222-2222-222222222222")


class DummyRequest:
    """Mock Request object for testing tenant ID extraction."""

    def __init__(self, state_tid=None, header_tid=None):
        self.state = type("State", (), {})()
        if state_tid is not None:
            self.state.tenant_id = state_tid
        self.headers = {}
        if header_tid is not None:
            self.headers["X-Tenant-ID"] = header_tid


def test_get_current_tenant_id_from_state():
    """Verify tenant_id is correctly extracted from request.state."""
    req = DummyRequest(state_tid=TENANT_ALPHA)
    tid = get_current_tenant_id(req)
    assert tid == TENANT_ALPHA
    assert isinstance(tid, uuid.UUID)

    req_str = DummyRequest(state_tid=str(TENANT_ALPHA))
    tid_str = get_current_tenant_id(req_str)
    assert tid_str == TENANT_ALPHA


def test_get_current_tenant_id_from_header():
    """Verify tenant_id falls back to X-Tenant-ID header."""
    req = DummyRequest(header_tid=str(TENANT_BETA))
    tid = get_current_tenant_id(req)
    assert tid == TENANT_BETA
    assert isinstance(tid, uuid.UUID)


def test_get_current_tenant_id_missing_raises_401():
    """Verify 401 Unauthorized is raised when tenant context is missing."""
    req = DummyRequest()
    with pytest.raises(HTTPException) as exc_info:
        get_current_tenant_id(req)
    assert exc_info.value.status_code == 401
    assert "Tenant context required" in exc_info.value.detail


def test_get_current_tenant_id_invalid_format_raises_400():
    """Verify 400 Bad Request is raised for non-UUID strings."""
    req = DummyRequest(header_tid="not-a-valid-uuid")
    with pytest.raises(HTTPException) as exc_info:
        get_current_tenant_id(req)
    assert exc_info.value.status_code == 400
    assert "Invalid tenant_id format" in exc_info.value.detail


@pytest.mark.asyncio
async def test_get_tenant_db_context_applies_set_config():
    """SCRUM-297: Verify get_tenant_db_context sets app.current_tenant_id via set_config."""
    try:
        async with get_tenant_db_context(TENANT_ALPHA) as session:
            result = await session.scalar(
                text("SELECT current_setting('app.current_tenant_id', true)")
            )
            assert result == str(TENANT_ALPHA)

        # String UUID input support
        async with get_tenant_db_context(str(TENANT_BETA)) as session:
            result = await session.scalar(
                text("SELECT current_setting('app.current_tenant_id', true)")
            )
            assert result == str(TENANT_BETA)
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_get_tenant_db_dependency_in_fastapi():
    """SCRUM-297: Verify get_tenant_db dependency in a FastAPI route."""
    try:
        test_app = FastAPI()

        @test_app.get("/test-tenant-context")
        async def tenant_endpoint(db: AsyncSession = Depends(get_tenant_db)):
            current_tid = await db.scalar(
                text("SELECT current_setting('app.current_tenant_id', true)")
            )
            return {"current_tenant_id": current_tid}

        async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
            # 1. Successful request with X-Tenant-ID header
            response = await ac.get(
                "/test-tenant-context",
                headers={"X-Tenant-ID": str(TENANT_ALPHA)},
            )
            assert response.status_code == 200
            assert response.json() == {"current_tenant_id": str(TENANT_ALPHA)}

            # 2. Missing tenant context -> 401
            missing_resp = await ac.get("/test-tenant-context")
            assert missing_resp.status_code == 401
            assert missing_resp.json()["detail"] == "Tenant context required"

            # 3. Invalid tenant format -> 400
            invalid_resp = await ac.get(
                "/test-tenant-context",
                headers={"X-Tenant-ID": "malformed-id"},
            )
            assert invalid_resp.status_code == 400
            assert invalid_resp.json()["detail"] == "Invalid tenant_id format"
    finally:
        await async_engine.dispose()
