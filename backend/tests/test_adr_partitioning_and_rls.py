"""SCRUM-572 & SCRUM-573 (US-44): Declarative LIST partitioning, dual pgvector HNSW, EXPLAIN pruning, backfill CLI & cross-partition RLS tests."""

from __future__ import annotations

import os
import re
from typing import Any
import uuid

import pytest
from sqlalchemy import delete, pool, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.db.models import AnonymizedADR, Project
from app.db.session import async_engine, get_tenant_db_context
from scripts.backfill_adr_partitions import (
    backfill_tenant_adr_partitions,
    expected_partition_name,
)


def _get_admin_url() -> str:
    admin_url = settings.MIGRATION_DATABASE_URL or os.getenv("MIGRATION_DATABASE_URL")
    if admin_url:
        return admin_url
    admin_user = os.getenv("POSTGRES_USER", "manta_user")
    admin_pass = os.getenv("POSTGRES_PASSWORD", "manta_password")
    return re.sub(r"://[^@]+@", f"://{admin_user}:{admin_pass}@", settings.DATABASE_URL)


def _collect_plan_relations(plan_node: dict[str, Any]) -> set[str]:
    rels: set[str] = set()
    rel = plan_node.get("Relation Name")
    if isinstance(rel, str):
        rels.add(rel)
    for child in plan_node.get("Plans", []) or []:
        if isinstance(child, dict):
            rels.update(_collect_plan_relations(child))
    return rels


async def _provision_isolated_tenant(
    session,
    *,
    tenant_id: uuid.UUID,
    prefix: str,
) -> uuid.UUID:
    suffix = tenant_id.hex[:8]
    result = await session.execute(
        text(
            """
            SELECT user_id FROM provision_tenant(
                p_name => :name,
                p_slug => :slug,
                p_admin_email => :email,
                p_admin_name => :admin_name,
                p_tenant_id => :tenant_id
            )
            """
        ),
        {
            "name": f"Part Test {prefix} {suffix}",
            "slug": f"part-{prefix}-{suffix}",
            "email": f"admin-part-{prefix}-{suffix}@manta.local",
            "admin_name": f"Part Admin {prefix}",
            "tenant_id": tenant_id,
        },
    )
    return result.scalar_one()


async def _cleanup_tenants(tenant_ids: list[uuid.UUID]) -> None:
    admin_engine = create_async_engine(_get_admin_url(), poolclass=pool.NullPool)
    try:
        async with admin_engine.begin() as conn:
            for tid in tenant_ids:
                part_name = expected_partition_name(tid)
                await conn.execute(delete(AnonymizedADR).where(AnonymizedADR.tenant_id == tid))
                await conn.execute(delete(Project).where(Project.tenant_id == tid))
                await conn.execute(text(f'DROP TABLE IF EXISTS public."{part_name}"'))
                await conn.execute(
                    text("DELETE FROM user_roles WHERE tenant_id = :tid"),
                    {"tid": tid},
                )
                await conn.execute(text("DELETE FROM users WHERE tenant_id = :tid"), {"tid": tid})
                await conn.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tid})
    finally:
        await admin_engine.dispose()


def test_anonymized_adr_model_partitioning_and_vector_columns() -> None:
    """Verify AnonymizedADR SQLAlchemy metadata declares LIST partitioning and 1536d/384d vectors."""
    table = AnonymizedADR.__table__
    assert table.dialect_kwargs.get("postgresql_partition_by") == "LIST (tenant_id)"
    assert "embedding_1536" in table.columns
    assert "embedding_384" in table.columns
    assert int(table.columns["embedding_1536"].type.dim) == 1536
    assert int(table.columns["embedding_384"].type.dim) == 384


@pytest.mark.asyncio
async def test_partitioned_pgvector_pruning_backfill_and_cross_partition_rls() -> None:
    """
    Verify US-44 end-to-end against PostgreSQL 16 + pgvector:
    1. `anonymized_adrs` is declarative LIST-partitioned with FORCE RLS.
    2. `provision_tenant` auto-creates child partitions + 1536d/384d HNSW indexes + FORCE RLS.
    3. EXPLAIN (FORMAT JSON) proves partition pruning to Tenant A's partition only.
    4. Direct query by Tenant A targeting Tenant B's child partition returns 0 rows.
    5. `backfill_tenant_adr_partitions` idempotently recreates missing partitions.
    """
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    part_a = expected_partition_name(tenant_a)
    part_b = expected_partition_name(tenant_b)

    try:
        async with get_tenant_db_context(tenant_a) as session:
            user_a = await _provision_isolated_tenant(session, tenant_id=tenant_a, prefix="a")
            proj_a = Project(
                tenant_id=tenant_a,
                created_by=user_a,
                name="Partition Proj A",
                slug=f"part-proj-a-{tenant_a.hex[:6]}",
            )
            session.add(proj_a)
            await session.flush()

            vec_384_a = [0.01] * 384
            vec_1536_a = [0.02] * 1536
            session.add(
                AnonymizedADR(
                    tenant_id=tenant_a,
                    project_id=proj_a.id,
                    title="Tenant A Event Outbox",
                    decision_summary="Use transactional outbox with monotonic UUIDv7.",
                    status="approved",
                    sanitization_verified=True,
                    embedding_384=vec_384_a,
                    embedding_1536=vec_1536_a,
                )
            )

        async with get_tenant_db_context(tenant_b) as session:
            user_b = await _provision_isolated_tenant(session, tenant_id=tenant_b, prefix="b")
            proj_b = Project(
                tenant_id=tenant_b,
                created_by=user_b,
                name="Partition Proj B",
                slug=f"part-proj-b-{tenant_b.hex[:6]}",
            )
            session.add(proj_b)
            await session.flush()

            session.add(
                AnonymizedADR(
                    tenant_id=tenant_b,
                    project_id=proj_b.id,
                    title="Tenant B Secret Pattern",
                    decision_summary="Secret Tenant B architectural decision.",
                    status="approved",
                    sanitization_verified=True,
                    embedding_384=[0.99] * 384,
                    embedding_1536=[0.99] * 1536,
                )
            )

        # 1. Verify catalog relkind, partition inheritance, HNSW indexes, and FORCE RLS
        async with async_engine.connect() as conn:
            rel_rows = (
                await conn.execute(
                    text(
                        """
                        SELECT c.relname, c.relkind::text, c.relispartition,
                               c.relrowsecurity, c.relforcerowsecurity
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public'
                          AND c.relname = ANY(:names)
                        ORDER BY c.relname
                        """
                    ),
                    {"names": ["anonymized_adrs", part_a, part_b]},
                )
            ).fetchall()
            by_name = {r[0]: r for r in rel_rows}
            assert set(by_name.keys()) == {"anonymized_adrs", part_a, part_b}
            assert by_name["anonymized_adrs"][1] == "p"
            assert by_name[part_a][1] == "r" and by_name[part_a][2] is True
            assert by_name[part_b][1] == "r" and by_name[part_b][2] is True
            assert all(r[3] and r[4] for r in rel_rows)

            idx_rows = (
                await conn.execute(
                    text(
                        """
                        SELECT indexname, indexdef
                        FROM pg_indexes
                        WHERE schemaname = 'public' AND tablename = :part
                        """
                    ),
                    {"part": part_a},
                )
            ).fetchall()
            idx_defs = {r[0]: r[1].lower() for r in idx_rows}
            assert any("using hnsw" in d and "embedding_1536" in d for d in idx_defs.values())
            assert any("using hnsw" in d and "embedding_384" in d for d in idx_defs.values())

        # 2. Verify EXPLAIN (FORMAT JSON) partition pruning for 384d & 1536d vector queries
        q384_literal = "[" + ",".join(["0.01"] * 384) + "]"
        q1536_literal = "[" + ",".join(["0.02"] * 1536) + "]"
        async with get_tenant_db_context(tenant_a) as session:
            for col_name, vec_lit in (
                ("embedding_384", q384_literal),
                ("embedding_1536", q1536_literal),
            ):
                explain_json = (
                    await session.execute(
                        text(
                            f"""
                            EXPLAIN (FORMAT JSON)
                            SELECT id, title
                            FROM anonymized_adrs
                            WHERE tenant_id = :tid
                              AND status = 'approved'
                              AND {col_name} IS NOT NULL
                            ORDER BY {col_name} <=> CAST(:qvec AS vector)
                            LIMIT 5
                            """
                        ),
                        {"tid": tenant_a, "qvec": vec_lit},
                    )
                ).scalar_one()
                plan_root = explain_json[0]["Plan"]
                scanned_rels = _collect_plan_relations(plan_root)
                assert part_a in scanned_rels, (
                    f"Expected Tenant A partition {part_a} in plan, got {scanned_rels}"
                )
                assert part_b not in scanned_rels, (
                    f"Tenant B partition {part_b} was NOT pruned: {scanned_rels}"
                )

            # 3. Direct-table cross-partition RLS negative check (Tenant A -> Tenant B partition)
            direct_b_rows = (
                await session.execute(text(f'SELECT id FROM public."{part_b}"'))
            ).fetchall()
            assert direct_b_rows == [], (
                f"Cross-partition direct query leaked rows from {part_b}: {direct_b_rows}"
            )

            direct_a_rows = (
                await session.execute(text(f'SELECT id FROM public."{part_a}"'))
            ).fetchall()
            assert len(direct_a_rows) == 1

            visible_via_orm = (await session.scalars(select(AnonymizedADR))).all()
            assert len(visible_via_orm) == 1
            assert visible_via_orm[0].tenant_id == tenant_a

        # 4. Backfill CLI idempotency verification
        admin_url = _get_admin_url()
        admin_engine = create_async_engine(admin_url, poolclass=pool.NullPool)
        try:
            async with admin_engine.begin() as conn:
                await conn.execute(delete(AnonymizedADR).where(AnonymizedADR.tenant_id == tenant_b))
                await conn.execute(text(f'DROP TABLE public."{part_b}"'))
        finally:
            await admin_engine.dispose()

        dry_summary = await backfill_tenant_adr_partitions(
            admin_url, tenant_id=tenant_b, dry_run=True
        )
        assert dry_summary.tenants_checked == 1
        assert dry_summary.partitions_created == 1
        assert dry_summary.partitions_already_present == 0

        applied_summary = await backfill_tenant_adr_partitions(
            admin_url, tenant_id=tenant_b, dry_run=False
        )
        assert applied_summary.partitions_created == 1
        assert applied_summary.partition_names == (part_b,)

        # Second invocation is a clean no-op
        second_summary = await backfill_tenant_adr_partitions(
            admin_url, tenant_id=tenant_b, dry_run=False
        )
        assert second_summary.partitions_created == 0
        assert second_summary.partitions_already_present == 1

        # Recreated partition retains FORCE RLS and isolation
        async with get_tenant_db_context(tenant_b) as session:
            assert (
                await session.execute(text(f'SELECT id FROM public."{part_b}"'))
            ).fetchall() == []
    finally:
        await _cleanup_tenants([tenant_a, tenant_b])
        await async_engine.dispose()
