"""SCRUM-246 / US-10 (SCRUM-305): Database RLS & FORCE RLS Security Invariants.

Verifies that PostgreSQL Row-Level Security (RLS) and FORCE ROW LEVEL SECURITY
are strictly enabled and enforced across all tenant tables, partitioned parent
tables (relkind='p'), and child partitions (relispartition=true):
1. Every tenant table/partition must have relrowsecurity = true (RLS enabled).
2. Every tenant table/partition must have relforcerowsecurity = true (FORCE RLS enabled).
3. Every tenant table/partition must have tenant_isolation_policy defined for eng_app_user.
4. Non-partition tenant tables must match SQLAlchemy Base.metadata dynamically.

If any table has RLS disabled (DISABLE ROW LEVEL SECURITY), FORCE RLS removed
(NO FORCE ROW LEVEL SECURITY), or its isolation policy dropped, this test suite
fails immediately, ensuring the security stage catches regressions.
"""

import pytest
from sqlalchemy import text

from app.db.models import Base
from app.db.session import async_engine

_TENANT_RELATIONS_SQL = text(
    """
    SELECT DISTINCT
        c.relname,
        c.relkind::text AS relkind,
        c.relispartition,
        c.relrowsecurity,
        c.relforcerowsecurity
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid
    WHERE n.nspname = 'public'
      AND c.relkind IN ('r', 'p')
      AND (a.attname = 'tenant_id' OR c.relname = 'tenants')
      AND a.attnum > 0
      AND NOT a.attisdropped
    ORDER BY c.relname;
    """
)


def _expected_tenant_base_tables() -> set[str]:
    tables = {
        table_name
        for table_name, table_obj in Base.metadata.tables.items()
        if "tenant_id" in table_obj.columns
    }
    if "tenants" in Base.metadata.tables:
        tables.add("tenants")
    return tables


@pytest.mark.asyncio
async def test_tenant_tables_have_rls_and_force_rls_active():
    """Verify all tenant tables and partitions in public schema have RLS and FORCE RLS enabled.

    Catches regressions where:
    - ALTER TABLE <table> DISABLE ROW LEVEL SECURITY was executed.
    - ALTER TABLE <table> NO FORCE ROW LEVEL SECURITY was executed.
    """
    expected_base_tables = _expected_tenant_base_tables()
    try:
        async with async_engine.connect() as conn:
            result = await conn.execute(_TENANT_RELATIONS_SQL)
            rows = result.fetchall()

            assert len(rows) > 0, "No tenant tables found in PostgreSQL public schema"

            unprotected = []
            base_tables: set[str] = set()
            child_partitions: set[str] = set()

            for relname, _relkind, relispartition, relrowsecurity, relforcerowsecurity in rows:
                if relispartition:
                    child_partitions.add(relname)
                else:
                    base_tables.add(relname)
                if not relrowsecurity or not relforcerowsecurity:
                    unprotected.append(
                        f"{relname} (partition={relispartition}, rls={relrowsecurity}, force_rls={relforcerowsecurity})"
                    )

            assert not unprotected, (
                f"Found tenant tables/partitions without FORCE RLS enabled: {', '.join(unprotected)}"
            )

            missing_from_db = expected_base_tables - base_tables
            unexpected_in_db = base_tables - expected_base_tables
            assert base_tables == expected_base_tables, (
                f"Tenant base/parent tables drifted from SQLAlchemy metadata. "
                f"Missing: {sorted(missing_from_db)}, Unexpected: {sorted(unexpected_in_db)}"
            )

            if child_partitions:
                inh_query = text(
                    """
                    SELECT
                        child.relname AS child_name,
                        parent.relname AS parent_name,
                        parent.relrowsecurity AS parent_rls,
                        parent.relforcerowsecurity AS parent_force_rls
                    FROM pg_inherits inh
                    JOIN pg_class child ON child.oid = inh.inhrelid
                    JOIN pg_class parent ON parent.oid = inh.inhparent
                    JOIN pg_namespace n ON n.oid = child.relnamespace
                    WHERE n.nspname = 'public'
                      AND child.relispartition = true;
                    """
                )
                for child_name, parent_name, parent_rls, parent_force_rls in (
                    await conn.execute(inh_query)
                ).fetchall():
                    if parent_name in expected_base_tables:
                        assert child_name in child_partitions, (
                            f"Child partition '{child_name}' of '{parent_name}' missing from RLS check"
                        )
                        assert parent_rls and parent_force_rls, (
                            f"Parent '{parent_name}' of partition '{child_name}' lacks FORCE RLS"
                        )
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_tenant_tables_have_isolation_policy_for_eng_app_user():
    """Verify all tenant tables and child partitions have tenant_isolation_policy for eng_app_user.

    Catches regressions where:
    - DROP POLICY tenant_isolation_policy ON <table> was executed.
    """
    try:
        async with async_engine.connect() as conn:
            query = text(
                """
                SELECT tablename, policyname, roles, cmd, qual, with_check
                FROM pg_policies
                WHERE schemaname = 'public'
                ORDER BY tablename;
                """
            )
            result = await conn.execute(query)
            policies = result.fetchall()
            policy_by_table = {p.tablename: p for p in policies}

            table_result = await conn.execute(_TENANT_RELATIONS_SQL)
            tenant_tables = [r[0] for r in table_result.fetchall()]

            missing_policies = []
            for tname in tenant_tables:
                pol = policy_by_table.get(tname)
                if not pol:
                    missing_policies.append(tname)
                else:
                    assert pol.policyname == "tenant_isolation_policy", (
                        f"Unexpected policy {pol.policyname} on {tname}"
                    )
                    assert "eng_app_user" in pol.roles, (
                        f"Policy on {tname} missing eng_app_user role: {pol.roles}"
                    )
                    assert pol.cmd == "ALL", f"Policy on {tname} is not ALL: {pol.cmd}"

            assert not missing_policies, (
                f"Tenant tables/partitions missing tenant_isolation_policy: {missing_policies}"
            )
    finally:
        await async_engine.dispose()
