"""
SCRUM-293: Automated guardrail tests verifying that ROW LEVEL SECURITY (RLS)
and FORCE ROW LEVEL SECURITY are strictly enabled on every tenant-scoped table,
including partitioned parent tables (relkind='p') and child partitions.
"""

import os
import re
from typing import Set

import pytest
from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.db.models import Base
from app.db.session import async_engine

# Global tables that are intentionally exempt from tenant RLS isolation
GLOBAL_EXEMPT_TABLES: Set[str] = {
    "alembic_version",  # Alembic internal migration tracker
    "roles",  # System-wide platform RBAC role definitions
}

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


def _expected_tenant_base_tables() -> Set[str]:
    """Derive expected tenant-scoped base/parent tables dynamically from SQLAlchemy metadata."""
    tables = {
        table_name
        for table_name, table_obj in Base.metadata.tables.items()
        if "tenant_id" in table_obj.columns
    }
    if "tenants" in Base.metadata.tables:
        tables.add("tenants")
    return tables


@pytest.mark.asyncio
async def test_all_tenant_id_tables_have_rls_and_force_rls():
    """
    SCRUM-293 / US-44: Query PostgreSQL system catalog (pg_class) to verify that
    every tenant table (regular relkind='r', partitioned parent relkind='p', and
    any child partitions relispartition=true) has:
      1. relrowsecurity = true  (RLS enabled)
      2. relforcerowsecurity = true  (FORCE RLS enabled)
    and that non-partition base/parent tables match SQLAlchemy Base.metadata exactly.
    """
    expected_base_tables = _expected_tenant_base_tables()
    try:
        async with async_engine.connect() as conn:
            result = await conn.execute(_TENANT_RELATIONS_SQL)
            rows = result.fetchall()

            assert len(rows) > 0, "No tenant tables found in PostgreSQL public schema"

            unprotected = []
            base_tables: Set[str] = set()
            child_partitions: Set[str] = set()

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
                f"PostgreSQL tenant base/parent tables drifted from SQLAlchemy metadata. "
                f"Missing in DB: {sorted(missing_from_db)}, Unexpected in DB: {sorted(unexpected_in_db)}"
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
                inh_rows = (await conn.execute(inh_query)).fetchall()
                for child_name, parent_name, parent_rls, parent_force_rls in inh_rows:
                    if parent_name in expected_base_tables:
                        assert child_name in child_partitions, (
                            f"Child partition '{child_name}' of '{parent_name}' missing from RLS check"
                        )
                        assert parent_rls and parent_force_rls, (
                            f"Parent table '{parent_name}' of partition '{child_name}' lacks FORCE RLS"
                        )
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_all_tenant_tables_have_isolation_policy_for_eng_app_user():
    """
    SCRUM-293 / SCRUM-318 / US-44: Verify that every tenant table and child
    partition has a tenant_isolation_policy targeting eng_app_user with command
    'ALL' and checking app.current_tenant_id.
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
                f"Tenant tables/partitions missing tenant_isolation_policy: {missing_policies}"
            )
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_no_unshielded_tables_exist_outside_exemption_list():
    """
    SCRUM-293 Guardrail: Ensure that ONLY explicitly permitted global tables
    (GLOBAL_EXEMPT_TABLES) can exist without RLS enabled (including partitioned
    parents and child partitions).
    """
    try:
        async with async_engine.connect() as conn:
            query = text(
                """
                SELECT c.relname
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = 'public'
                  AND c.relkind IN ('r', 'p')
                  AND c.relrowsecurity = false
                ORDER BY c.relname;
                """
            )
            result = await conn.execute(query)
            unshielded_tables = {r[0] for r in result.fetchall()}

            unexpected_unshielded = unshielded_tables - GLOBAL_EXEMPT_TABLES
            assert not unexpected_unshielded, (
                f"Found unshielded tables that are NOT in the exemption whitelist: {unexpected_unshielded}. "
                "Any new table or partition must either have FORCE RLS enabled or be explicitly documented as global."
            )
    finally:
        await async_engine.dispose()


@pytest.mark.asyncio
async def test_partitioned_tenant_table_and_child_partition_rls_guardrail():
    """
    US-44 Guardrail Verification: Inside a rolled-back admin transaction, verify that:
    1. A partitioned tenant table (relkind='p') and child partition (relispartition=true)
       without RLS are detected by the catalog query.
    2. Once ENABLE + FORCE ROW LEVEL SECURITY and tenant_isolation_policy are applied
       to both parent and child partition, catalog + pg_inherits checks pass cleanly.
    """
    admin_url = settings.MIGRATION_DATABASE_URL or os.getenv("MIGRATION_DATABASE_URL")
    if not admin_url:
        admin_user = os.getenv("POSTGRES_USER", "manta_user")
        admin_pass = os.getenv("POSTGRES_PASSWORD", "manta_password")
        admin_url = re.sub(r"://[^@]+@", f"://{admin_user}:{admin_pass}@", settings.DATABASE_URL)

    admin_engine = create_async_engine(admin_url, poolclass=pool.NullPool)
    parent_name = "_rls_partition_guardrail_probe"
    child_name = "_rls_partition_guardrail_probe_t1"
    try:
        async with admin_engine.connect() as conn:
            trans = await conn.begin()
            try:
                await conn.execute(
                    text(
                        f"""
                        CREATE TABLE {parent_name} (
                            id uuid NOT NULL,
                            tenant_id uuid NOT NULL
                        ) PARTITION BY LIST (tenant_id);
                        """
                    )
                )
                await conn.execute(
                    text(
                        f"""
                        CREATE TABLE {child_name}
                            PARTITION OF {parent_name}
                            FOR VALUES IN ('11111111-1111-1111-1111-111111111111');
                        """
                    )
                )

                before_rows = (await conn.execute(_TENANT_RELATIONS_SQL)).fetchall()
                probe_before = {r[0]: r for r in before_rows if r[0] in {parent_name, child_name}}
                assert set(probe_before.keys()) == {parent_name, child_name}
                assert probe_before[parent_name][1] == "p" and not probe_before[parent_name][2]
                assert probe_before[child_name][1] == "r" and probe_before[child_name][2]
                assert not probe_before[parent_name][3] and not probe_before[child_name][3]

                for rel in (parent_name, child_name):
                    await conn.execute(text(f"ALTER TABLE {rel} ENABLE ROW LEVEL SECURITY;"))
                    await conn.execute(text(f"ALTER TABLE {rel} FORCE ROW LEVEL SECURITY;"))
                    await conn.execute(
                        text(
                            f"""
                            CREATE POLICY tenant_isolation_policy ON {rel}
                                AS PERMISSIVE FOR ALL
                                TO eng_app_user
                                USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
                                WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
                            """
                        )
                    )

                after_rows = (await conn.execute(_TENANT_RELATIONS_SQL)).fetchall()
                probe_after = {r[0]: r for r in after_rows if r[0] in {parent_name, child_name}}
                assert all(r[3] and r[4] for r in probe_after.values())

                pol_rows = (
                    await conn.execute(
                        text(
                            "SELECT tablename, policyname FROM pg_policies "
                            "WHERE schemaname = 'public' AND tablename = ANY(:names)"
                        ),
                        {"names": [parent_name, child_name]},
                    )
                ).fetchall()
                assert {r[0]: r[1] for r in pol_rows} == {
                    parent_name: "tenant_isolation_policy",
                    child_name: "tenant_isolation_policy",
                }
            finally:
                await trans.rollback()
    finally:
        await admin_engine.dispose()


def test_sqlalchemy_models_match_tenant_rls_expectations():
    """
    Static Guardrail: Inspects SQLAlchemy Base.metadata.
    Guarantees every mapped table is either tenant-scoped (has 'tenant_id' or is 'tenants')
    or explicitly listed in GLOBAL_EXEMPT_TABLES.
    """
    models_with_tenant_id = {
        table_name
        for table_name, table_obj in Base.metadata.tables.items()
        if "tenant_id" in table_obj.columns
    }
    expected_base_tables = _expected_tenant_base_tables()
    expected_metadata_tables = expected_base_tables | (
        GLOBAL_EXEMPT_TABLES & set(Base.metadata.tables.keys())
    )

    assert len(models_with_tenant_id) > 0, "Expected at least one model with tenant_id"
    assert set(Base.metadata.tables.keys()) == expected_metadata_tables, (
        "SQLAlchemy models found that are neither tenant-scoped nor in GLOBAL_EXEMPT_TABLES: "
        f"{sorted(set(Base.metadata.tables.keys()) - expected_metadata_tables)}"
    )

    # Global exempt tables must never contain a tenant_id column
    for exempt_table in GLOBAL_EXEMPT_TABLES:
        assert exempt_table not in models_with_tenant_id, (
            f"Global exempt table {exempt_table} should NOT have a tenant_id column"
        )
