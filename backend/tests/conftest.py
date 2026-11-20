import pytest

from app.db.session import async_engine


@pytest.fixture(autouse=True)
def cleanup_db_engine():
    """Ensure SQLAlchemy async engine connection pool is cleanly disposed.

    In pytest-asyncio with function-scoped event loops, underlying asyncpg
    Connection objects cannot be shared across different loops. Disposing
    the engine pool before and after each test ensures that no stale connection
    references an already-closed event loop.
    """
    async_engine.sync_engine.dispose()
    yield
    async_engine.sync_engine.dispose()
