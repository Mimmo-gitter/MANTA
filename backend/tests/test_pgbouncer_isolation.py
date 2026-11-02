"""Live tenant-isolation tests through PgBouncer (SCRUM-245 / US-9).

Proves that PgBouncer, running in transaction pooling mode with
``server_reset_query = DISCARD ALL`` and ``server_reset_query_always = 1``,
never hands session-level state set by one client to the next client that is
given the same physical server connection.

Skipped unless ``PGBOUNCER_TEST_DSN`` points at a running PgBouncer, e.g.::

    docker compose -f backend/docker-compose.yml up -d --wait postgres pgbouncer
    PGBOUNCER_TEST_DSN=postgresql://manta_user:manta_password@localhost:6432/manta_db \
        python -m pytest backend/tests/test_pgbouncer_isolation.py -v
"""

import os
import uuid

import pytest

PGBOUNCER_TEST_DSN = os.getenv("PGBOUNCER_TEST_DSN")

pytestmark = pytest.mark.skipif(
    not PGBOUNCER_TEST_DSN,
    reason="PGBOUNCER_TEST_DSN not set; live PgBouncer isolation tests skipped",
)

# Same knobs as backend/app/db/session.py: no client-side statement caching.
ASYNCPG_KWARGS = {"statement_cache_size": 0}
MAX_ATTEMPTS = 25
ADVISORY_LOCK_KEY = 4242

PROBE_SQL = """
SELECT
    pg_backend_pid()                                  AS pid,
    current_setting('app.current_tenant_id', true)    AS tenant_id,
    current_setting('search_path')                    AS search_path,
    to_regclass('pg_temp.leak_probe') IS NULL         AS temp_table_gone,
    (SELECT count(*) FROM pg_locks
      WHERE locktype = 'advisory' AND pid = pg_backend_pid()) AS advisory_locks
"""


async def _pollute_session(conn, tenant_id: str) -> int:
    """Simulate a misbehaving client that sets SESSION-level state (not SET LOCAL).

    Everything runs in one explicit transaction so it is guaranteed to execute on
    a single server connection; the state outlives the COMMIT because it is
    session-scoped. Returns the backend PID that now carries the state.
    """
    async with conn.transaction():
        await conn.execute(f"SET app.current_tenant_id = '{tenant_id}'")
        await conn.execute("SET search_path TO leaked_tenant_schema")
        await conn.execute("CREATE TEMP TABLE leak_probe (id int)")
        await conn.execute(f"SELECT pg_advisory_lock({ADVISORY_LOCK_KEY})")
        return await conn.fetchval("SELECT pg_backend_pid()")


async def _probe(conn):
    async with conn.transaction():
        return await conn.fetchrow(PROBE_SQL)


async def test_session_state_does_not_leak_to_next_client():
    """AC: session-level state set by one client is gone for the next."""
    import asyncpg

    client_a = await asyncpg.connect(PGBOUNCER_TEST_DSN, **ASYNCPG_KWARGS)
    client_b = await asyncpg.connect(PGBOUNCER_TEST_DSN, **ASYNCPG_KWARGS)
    try:
        same_backend_observations = []
        for _ in range(MAX_ATTEMPTS):
            leaked_tenant = str(uuid.uuid4())
            polluted_pid = await _pollute_session(client_a, leaked_tenant)
            row = await _probe(client_b)
            if row["pid"] == polluted_pid:
                same_backend_observations.append((leaked_tenant, row))
            if len(same_backend_observations) >= 3:
                break

        assert same_backend_observations, (
            f"client B never reused client A's server connection in {MAX_ATTEMPTS} "
            "attempts; cannot prove reset behaviour (is pool_mode=transaction?)"
        )
        for leaked_tenant, row in same_backend_observations:
            assert row["tenant_id"] in (None, ""), (
                f"tenant context leaked across clients: {row['tenant_id']!r}"
            )
            assert row["tenant_id"] != leaked_tenant
            assert "leaked_tenant_schema" not in row["search_path"]
            assert row["temp_table_gone"] is True, "temp table survived connection reuse"
            assert row["advisory_locks"] == 0, "session advisory lock survived reuse"
    finally:
        await client_a.close()
        await client_b.close()


async def test_session_state_is_discarded_after_each_transaction():
    """With server_reset_query_always=1 a client cannot rely on session state at all."""
    import asyncpg

    conn = await asyncpg.connect(PGBOUNCER_TEST_DSN, **ASYNCPG_KWARGS)
    try:
        await _pollute_session(conn, str(uuid.uuid4()))
        row = await _probe(conn)
        assert row["tenant_id"] in (None, "")
        assert row["temp_table_gone"] is True
        assert row["advisory_locks"] == 0
    finally:
        await conn.close()


async def test_app_tenant_context_through_pgbouncer(monkeypatch):
    """AC: the app connects through PgBouncer; SET LOCAL context is per-transaction."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.db import session as db_session

    url = PGBOUNCER_TEST_DSN.replace("postgresql://", "postgresql+asyncpg://", 1)
    engine = create_async_engine(
        url,
        connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
    )
    monkeypatch.setattr(db_session, "AsyncSessionLocal", session_factory)

    try:
        for _ in range(5):
            tenant_id = uuid.uuid4()
            async with db_session.get_tenant_db_context(tenant_id) as session:
                inside = await session.scalar(
                    text("SELECT current_setting('app.current_tenant_id', true)")
                )
            assert inside == str(tenant_id)

            async with session_factory() as session:
                after = await session.scalar(
                    text("SELECT current_setting('app.current_tenant_id', true)")
                )
            assert after in (None, ""), f"tenant context outlived its transaction: {after!r}"
    finally:
        await engine.dispose()
