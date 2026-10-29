from contextlib import asynccontextmanager
from typing import AsyncGenerator, Union
import uuid

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

try:
    from app.config import settings
except ImportError:
    from backend.app.core.config import settings

# Retrieve database connection string
DATABASE_URL = getattr(settings, "DATABASE_URL", None) or getattr(
    settings,
    "ASYNC_DATABASE_URL",
    "postgresql+asyncpg://eng_app_user:password@localhost:5432/manta_db",
)

# 1. Asynchronous Engine for FastAPI / Workers (routed through PgBouncer)
# statement_cache_size=0 is strictly required when using PgBouncer in transaction pooling mode
async_engine = create_async_engine(
    DATABASE_URL,
    connect_args={
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
    },
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency injection for non-tenant / system FastAPI endpoints."""
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def get_tenant_db_context(
    tenant_id: Union[uuid.UUID, str],
) -> AsyncGenerator[AsyncSession, None]:
    """
    Acquires an async session and enforces Row-Level Security tenant context
    using transaction-local 'SELECT set_config('app.current_tenant_id', :tid, true)'.
    """
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tid, true)"),
                {"tid": str(tenant_id)},
            )
            yield session


def get_current_tenant_id(request: Request) -> uuid.UUID:
    """
    Extracts and validates tenant_id from request context (set by auth middleware
    on request.state.tenant_id) or fallback to 'X-Tenant-ID' header.
    """
    tenant_id = getattr(request.state, "tenant_id", None) or request.headers.get("X-Tenant-ID")
    if not tenant_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tenant context required",
        )
    try:
        return uuid.UUID(str(tenant_id))
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid tenant_id format",
        )


async def get_tenant_db(
    tenant_id: uuid.UUID = Depends(get_current_tenant_id),
) -> AsyncGenerator[AsyncSession, None]:
    """
    FastAPI dependency that enforces Row-Level Security tenant context
    per transaction using set_config('app.current_tenant_id', :tid, true).
    """
    async with get_tenant_db_context(tenant_id) as session:
        yield session

