from contextlib import asynccontextmanager
from typing import AsyncGenerator
import uuid

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
    "postgresql+asyncpg://manta_user:manta_password@localhost:5432/manta_db",
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
    """Dependency injection for FastAPI endpoints."""
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def get_tenant_db_context(tenant_id: uuid.UUID) -> AsyncGenerator[AsyncSession, None]:
    """
    Acquires an async session and enforces Row-Level Security tenant context
    using transaction-local 'set_config(..., is_local=true)' (equivalent to SET LOCAL).
    Note: PostgreSQL SET does not accept bind parameters ($1), so set_config is required.
    """
    async with AsyncSessionLocal() as session:
        async with session.begin():
            await session.execute(
                text("SELECT set_config('app.current_tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            yield session
