"""SCRUM-563 (TASK-314-1): Schema, draft lifecycle & FORCE RLS tests for adr_sources and anonymized_adrs."""

from __future__ import annotations

import os
import re
import uuid

import pytest
from sqlalchemy import delete, pool, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.db.models import ADRSource, AnonymizedADR, Base, Project, Repository
from app.db.session import AsyncSessionLocal, async_engine, get_tenant_db_context


def _get_admin_url() -> str:
    admin_url = settings.MIGRATION_DATABASE_URL or os.getenv("MIGRATION_DATABASE_URL")
    if admin_url:
        return admin_url
    admin_user = os.getenv("POSTGRES_USER", "manta_user")
    admin_pass = os.getenv("POSTGRES_PASSWORD", "manta_password")
    return re.sub(r"://[^@]+@", f"://{admin_user}:{admin_pass}@", settings.DATABASE_URL)


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
            "name": f"ADR Test {prefix} {suffix}",
            "slug": f"adr-{prefix}-{suffix}",
            "email": f"admin-{prefix}-{suffix}@manta.local",
            "admin_name": f"Admin {prefix}",
            "tenant_id": tenant_id,
        },
    )
    return result.scalar_one()


async def _cleanup_tenants(tenant_ids: list[uuid.UUID]) -> None:
    admin_engine = create_async_engine(_get_admin_url(), poolclass=pool.NullPool)
    try:
        async with admin_engine.begin() as conn:
            for tid in tenant_ids:
                await conn.execute(delete(ADRSource).where(ADRSource.tenant_id == tid))
                await conn.execute(delete(AnonymizedADR).where(AnonymizedADR.tenant_id == tid))
                await conn.execute(delete(Repository).where(Repository.tenant_id == tid))
                await conn.execute(delete(Project).where(Project.tenant_id == tid))
                await conn.execute(
                    text("DELETE FROM user_roles WHERE tenant_id = :tid"),
                    {"tid": tid},
                )
                await conn.execute(text("DELETE FROM users WHERE tenant_id = :tid"), {"tid": tid})
                await conn.execute(text("DELETE FROM tenants WHERE id = :tid"), {"tid": tid})
    finally:
        await admin_engine.dispose()


def test_adr_sources_and_anonymized_adrs_model_separation() -> None:
    """
    Verify static SQLAlchemy metadata separation:
    - adr_sources exists and holds raw_markdown, file_path, commit_sha, content_hash, adr_id.
    - anonymized_adrs supports nullable decision_summary & draft status metadata with ZERO raw
      Markdown or file path columns.
    """
    assert "adr_sources" in Base.metadata.tables
    assert "anonymized_adrs" in Base.metadata.tables

    adr_sources_cols = set(Base.metadata.tables["adr_sources"].columns.keys())
    assert {
        "id",
        "tenant_id",
        "project_id",
        "repository_id",
        "adr_id",
        "file_path",
        "commit_sha",
        "raw_markdown",
        "content_hash",
        "extracted_at",
        "created_at",
        "updated_at",
    }.issubset(adr_sources_cols)

    anon_table = Base.metadata.tables["anonymized_adrs"]
    anon_cols = set(anon_table.columns.keys())
    assert {
        "tenant_id",
        "id",
        "project_id",
        "source_repository_id",
        "source_commit_sha",
        "source_content_hash",
        "title",
        "decision_summary",
        "status",
        "sanitization_verified",
        "updated_at",
    }.issubset(anon_cols)
    assert anon_table.columns["decision_summary"].nullable is True
    assert anon_table.columns["status"].default.arg == "draft"

    forbidden_terms = ("raw", "markdown", "path", "file")
    violating_cols = [
        col for col in anon_cols if any(term in col.lower() for term in forbidden_terms)
    ]
    assert not violating_cols, (
        f"anonymized_adrs must not contain raw text or file path columns: {violating_cols}"
    )


@pytest.mark.asyncio
async def test_adr_sources_force_rls_and_cross_tenant_isolation() -> None:
    """
    Verify adr_sources has ENABLE + FORCE ROW LEVEL SECURITY + tenant_isolation_policy
    in PostgreSQL, enforces draft separation & check constraints, and isolates rows per tenant.
    """
    tenant_a = uuid.uuid4()
    tenant_b = uuid.uuid4()
    try:
        async with get_tenant_db_context(tenant_a) as session:
            user_a = await _provision_isolated_tenant(session, tenant_id=tenant_a, prefix="a")
            proj_a = Project(
                tenant_id=tenant_a,
                created_by=user_a,
                name="ADR Project A",
                slug=f"adr-proj-a-{tenant_a.hex[:6]}",
            )
            session.add(proj_a)
            await session.flush()

            repo_a = Repository(
                tenant_id=tenant_a,
                project_id=proj_a.id,
                external_repo_id=int(tenant_a.int % 1_000_000_000),
                full_name="acme/adr-repo-a",
            )
            session.add(repo_a)
            await session.flush()

            draft_adr = AnonymizedADR(
                tenant_id=tenant_a,
                project_id=proj_a.id,
                source_repository_id=repo_a.id,
                source_commit_sha="a" * 40,
                source_content_hash="b" * 64,
                title="Use Outbox Pattern",
                decision_summary=None,
            )
            session.add(draft_adr)
            await session.flush()

            assert draft_adr.status == "draft"
            assert draft_adr.decision_summary is None

            source_a = ADRSource(
                tenant_id=tenant_a,
                project_id=proj_a.id,
                repository_id=repo_a.id,
                adr_id=draft_adr.id,
                file_path="docs/adr/0001-outbox.md",
                commit_sha="a" * 40,
                raw_markdown="# Use Outbox Pattern\nSensitive client ACME Corp.",
                content_hash="b" * 64,
            )
            session.add(source_a)

        async with get_tenant_db_context(tenant_b) as session:
            await _provision_isolated_tenant(session, tenant_id=tenant_b, prefix="b")

        # 1. Verify pg_class & pg_policies on adr_sources
        async with async_engine.connect() as conn:
            rls_row = (
                await conn.execute(
                    text(
                        """
                        SELECT relrowsecurity, relforcerowsecurity
                        FROM pg_class c
                        JOIN pg_namespace n ON n.oid = c.relnamespace
                        WHERE n.nspname = 'public' AND c.relname = 'adr_sources'
                        """
                    )
                )
            ).one()
            assert rls_row.relrowsecurity is True
            assert rls_row.relforcerowsecurity is True

            pol_row = (
                await conn.execute(
                    text(
                        """
                        SELECT policyname, cmd, roles
                        FROM pg_policies
                        WHERE schemaname = 'public' AND tablename = 'adr_sources'
                        """
                    )
                )
            ).one()
            assert pol_row.policyname == "tenant_isolation_policy"
            assert pol_row.cmd == "ALL"
            assert "eng_app_user" in pol_row.roles

        # 2. Tenant A reads own draft ADR and raw ADR source
        async with get_tenant_db_context(tenant_a) as session:
            src_rows = (await session.scalars(select(ADRSource))).all()
            adr_rows = (await session.scalars(select(AnonymizedADR))).all()
            assert len(src_rows) == 1
            assert src_rows[0].file_path == "docs/adr/0001-outbox.md"
            assert len(adr_rows) == 1
            assert adr_rows[0].status == "draft"
            assert adr_rows[0].decision_summary is None

        # 3. Tenant B sees zero rows from Tenant A
        async with get_tenant_db_context(tenant_b) as session:
            assert (await session.scalars(select(ADRSource))).all() == []
            assert (
                await session.scalars(
                    select(AnonymizedADR).where(AnonymizedADR.tenant_id == tenant_a)
                )
            ).all() == []

        # 4. Fail-closed without tenant context
        async with AsyncSessionLocal() as session:
            assert (await session.scalars(select(ADRSource))).all() == []
            with pytest.raises(DBAPIError):
                await session.execute(
                    text(
                        """
                        INSERT INTO adr_sources (
                            id, tenant_id, project_id, repository_id, adr_id,
                            file_path, commit_sha, raw_markdown, content_hash
                        ) VALUES (
                            :id, :tid, :pid, :rid, :aid,
                            'docs/adr/fail.md', :sha, 'raw', :h
                        )
                        """
                    ),
                    {
                        "id": uuid.uuid4(),
                        "tid": tenant_a,
                        "pid": proj_a.id,
                        "rid": repo_a.id,
                        "aid": uuid.uuid4(),
                        "sha": "c" * 40,
                        "h": "d" * 64,
                    },
                )

        # 5. Status check constraint rejects invalid status
        async with get_tenant_db_context(tenant_a) as session:
            bad_adr = AnonymizedADR(
                tenant_id=tenant_a,
                project_id=proj_a.id,
                title="Invalid Status ADR",
                status="bogus_status",
            )
            session.add(bad_adr)
            with pytest.raises(IntegrityError):
                await session.flush()
    finally:
        await _cleanup_tenants([tenant_a, tenant_b])
        await async_engine.dispose()
