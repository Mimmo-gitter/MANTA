"""SCRUM-246 / US-10 (SCRUM-305): Database RLS & FORCE RLS Security Invariants.

Verifies that PostgreSQL Row-Level Security (RLS) and FORCE ROW LEVEL SECURITY
are strictly enabled and enforced across all tenant tables:
1. Every tenant table must have relrowsecurity = true (RLS enabled).
2. Every tenant table must have relforcerowsecurity = true (FORCE RLS enabled).
3. Every tenant table must have tenant_isolation_policy defined for eng_app_user.

If any table has RLS disabled (DISABLE ROW LEVEL SECURITY), FORCE RLS removed
(NO FORCE ROW LEVEL SECURITY), or its isolation policy dropped, this test suite
fails immediately, ensuring the security stage catches regressions.
"""

import pytest
from sqlalchemy import text

from app.db.session import async_engine


@pytest.mark.asyncio
async def test_tenant_tables_have_rls_and_force_rls_active():
    """Verify all tenant tables in public schema have both RLS and FORCE RLS enabled.

    Catches regressions where:
    - ALTER TABLE <table> DISABLE ROW LEVEL SECURITY was executed.
    - ALTER TABLE <table> NO FORCE ROW LEVEL SECURITY was executed.
    """
    try:
        async with async_engine.connect() as conn:
            query = text(
                """
                SELECT DISTINCT c.relname, c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_attribute a ON a.attrelid = c.oid
                WHERE n.nspname = 'public'
                  AND c.relkind = 'r'
                  AND (a.attname = 'tenant_id' OR c.relname = 'tenants')
                  AND a.attnum > 0
                  AND NOT a.attisdropped
                ORDER BY c.relname;
                """
            )
            result = await conn.execute(query)
            rows = result.fetchall()

            assert len(rows) > 0, "No tenant tables found in PostgreSQL public schema"

            unprotected = []
            for relname, relrowsecurity, relforcerowsecurity in rows:
                if not relrowsecurity or not relforcerowsecurity:
                    unprotected.append(
                        f"{relname} (rls={relrowsecurity}, force_rls={relforcerowsecurity})"
                    )

            assert not unprotected, (
                f"Found tenant tables without FORCE RLS enabled: {', '.join(unprotected)}"
            )
            assert len(rows) == 41, f"Expected 41 tenant tables, found {len(rows)}"
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_tenant_tables_have_isolation_policy_for_eng_app_user():
    """Verify all tenant tables have tenant_isolation_policy for eng_app_user.

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

            table_query = text(
                """
                SELECT DISTINCT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_attribute a ON a.attrelid = c.oid
                WHERE n.nspname = 'public'
                  AND c.relkind = 'r'
                  AND (a.attname = 'tenant_id' OR c.relname = 'tenants')
                  AND a.attnum > 0
                  AND NOT a.attisdropped;
                """
            )
            table_result = await conn.execute(table_query)
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
                f"Tenant tables missing tenant_isolation_policy: {missing_policies}"
            )
    finally:
        await async_engine.dispose()
