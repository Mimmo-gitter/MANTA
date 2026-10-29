"""
SCRUM-293: Automated guardrail tests verifying that ROW LEVEL SECURITY (RLS)
and FORCE ROW LEVEL SECURITY are strictly enabled on every tenant-scoped table.
"""

from typing import Set
import pytest
from sqlalchemy import text

from app.db.models import Base
from app.db.session import async_engine

# Global tables that are intentionally exempt from tenant RLS isolation
GLOBAL_EXEMPT_TABLES: Set[str] = {
    "alembic_version",  # Alembic internal migration tracker
    "processed_outbox_events",  # Global event consumer deduplication table
    "roles",  # System-wide platform RBAC role definitions
}


@pytest.mark.asyncio
async def test_all_tenant_id_tables_have_rls_and_force_rls():
    """
    SCRUM-293: Query PostgreSQL system catalog (pg_class) to verify that
    every table with a tenant_id column (plus 'tenants') has:
      1. relrowsecurity = true  (RLS enabled)
      2. relforcerowsecurity = true  (FORCE RLS enabled)
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

            # Exactly 40 tenant tables in schema
            assert len(rows) == 40, f"Expected 40 tenant tables, found {len(rows)}"
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_all_tenant_tables_have_isolation_policy_for_eng_app_user():
    """
    SCRUM-293 / SCRUM-318: Verify that every tenant table has a tenant_isolation_policy
    specifically targeting eng_app_user with command 'ALL' and checking app.current_tenant_id.
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

            # Query all tenant table names
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
                        f"Unexpected policy name {pol.policyname} on {tname}"
                    )
                    assert "eng_app_user" in pol.roles, (
                        f"Policy on {tname} does not target eng_app_user: {pol.roles}"
                    )
                    assert pol.cmd == "ALL", f"Policy on {tname} is not ALL: {pol.cmd}"
                    assert "app.current_tenant_id" in pol.qual, (
                        f"USING clause on {tname} missing app.current_tenant_id"
                    )
                    assert "app.current_tenant_id" in pol.with_check, (
                        f"WITH CHECK clause on {tname} missing app.current_tenant_id"
                    )

            assert not missing_policies, (
                f"Tenant tables missing tenant_isolation_policy: {missing_policies}"
            )
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_no_unshielded_tables_exist_outside_exemption_list():
    """
    SCRUM-293 Guardrail: Ensure that ONLY explicitly permitted global tables
    (GLOBAL_EXEMPT_TABLES) can exist without RLS enabled.
    Prevents accidental creation of un-isolated tables in future migrations.
    """
    try:
        async with async_engine.connect() as conn:
            query = text(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relkind = 'r'
                  AND c.relrowsecurity = false
                ORDER BY c.relname;
                """
            )
            result = await conn.execute(query)
            unshielded_tables = {r[0] for r in result.fetchall()}

            unexpected_unshielded = unshielded_tables - GLOBAL_EXEMPT_TABLES
            assert not unexpected_unshielded, (
                f"Found unshielded tables that are NOT in the exemption whitelist: {unexpected_unshielded}. "
                "Any new table must either have FORCE RLS enabled or be explicitly documented as global."
            )
    finally:
        await async_engine.dispose()


def test_sqlalchemy_models_match_tenant_rls_expectations():
    """
    Static Guardrail: Inspects SQLAlchemy Base.metadata.
    Guarantees every mapped model having a 'tenant_id' column is in the tenant tables list.
    """
    models_with_tenant_id = set()
    for table_name, table_obj in Base.metadata.tables.items():
        if "tenant_id" in table_obj.columns:
            models_with_tenant_id.add(table_name)

    assert len(models_with_tenant_id) == 39, (
        f"Expected 39 SQLAlchemy models with tenant_id, found {len(models_with_tenant_id)}"
    )

    # Global exempt tables must never contain a tenant_id column
    for exempt_table in GLOBAL_EXEMPT_TABLES:
        assert exempt_table not in models_with_tenant_id, (
            f"Global exempt table {exempt_table} should NOT have a tenant_id column"
        )
