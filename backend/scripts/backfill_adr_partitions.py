"""SCRUM-573 (TASK-317-2): Idempotent tenant ADR partition backfill CLI and verification helper."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import re
import sys
import uuid

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import pool, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine  # noqa: E402

from app.config import settings  # noqa: E402


def expected_partition_name(tenant_id: uuid.UUID) -> str:
    """Return canonical child partition name for a tenant UUID."""
    return f"anonymized_adrs_t_{tenant_id.hex}"


@dataclass(frozen=True)
class BackfillSummary:
    tenants_checked: int
    partitions_created: int
    partitions_already_present: int
    partition_names: tuple[str, ...]


def resolve_admin_database_url(explicit_url: str | None = None) -> str:
    """Resolve administrative PostgreSQL URL required for DDL partition management."""
    if explicit_url:
        return explicit_url
    env_url = os.getenv("MIGRATION_DATABASE_URL") or getattr(
        settings, "MIGRATION_DATABASE_URL", None
    )
    if env_url:
        return env_url
    admin_user = os.getenv("POSTGRES_USER", "manta_user")
    admin_pass = os.getenv("POSTGRES_PASSWORD", "manta_password")
    return re.sub(r"://[^@]+@", f"://{admin_user}:{admin_pass}@", settings.DATABASE_URL)


async def backfill_tenant_adr_partitions_with_connection(
    conn: AsyncConnection,
    *,
    tenant_id: uuid.UUID | None = None,
    dry_run: bool = False,
) -> BackfillSummary:
    """Ensure every (or target) tenant owns a protected child partition on `anonymized_adrs`."""
    if tenant_id is not None:
        rows = (
            await conn.execute(
                text("SELECT id FROM tenants WHERE id = :tid"),
                {"tid": tenant_id},
            )
        ).fetchall()
    else:
        rows = (
            await conn.execute(text("SELECT id FROM tenants ORDER BY created_at ASC"))
        ).fetchall()

    tenant_ids = [r[0] for r in rows]
    existing_parts = {
        r[0]
        for r in (
            await conn.execute(
                text(
                    """
                    SELECT child.relname
                    FROM pg_inherits inh
                    JOIN pg_class child ON child.oid = inh.inhrelid
                    JOIN pg_class parent ON parent.oid = inh.inhparent
                    JOIN pg_namespace n ON n.oid = child.relnamespace
                    WHERE n.nspname = 'public'
                      AND parent.relname = 'anonymized_adrs'
                      AND child.relispartition = true
                    """
                )
            )
        ).fetchall()
    }

    created = 0
    already = 0
    names: list[str] = []

    for tid in tenant_ids:
        part_name = expected_partition_name(tid)
        names.append(part_name)
        if part_name in existing_parts:
            already += 1
        else:
            created += 1

        if not dry_run:
            # Always invoke idempotent SQL helper so HNSW indexes, FORCE RLS & policy are verified
            await conn.execute(
                text("SELECT ensure_tenant_adr_partition(:tid)"),
                {"tid": tid},
            )

    return BackfillSummary(
        tenants_checked=len(tenant_ids),
        partitions_created=created,
        partitions_already_present=already,
        partition_names=tuple(names),
    )


async def backfill_tenant_adr_partitions(
    database_url: str | None = None,
    *,
    tenant_id: uuid.UUID | None = None,
    dry_run: bool = False,
) -> BackfillSummary:
    """Open an admin database connection and backfill tenant ADR partitions idempotently."""
    url = resolve_admin_database_url(database_url)
    engine = create_async_engine(url, poolclass=pool.NullPool)
    try:
        if dry_run:
            async with engine.connect() as conn:
                return await backfill_tenant_adr_partitions_with_connection(
                    conn, tenant_id=tenant_id, dry_run=True
                )
        async with engine.begin() as conn:
            return await backfill_tenant_adr_partitions_with_connection(
                conn, tenant_id=tenant_id, dry_run=False
            )
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Idempotently backfill anonymized_adrs tenant partitions, HNSW indexes, and FORCE RLS."
    )
    parser.add_argument("--tenant-id", type=str, default=None, help="Optional single tenant UUID.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report missing partitions without modifying PostgreSQL.",
    )
    parser.add_argument("--database-url", type=str, default=None, help="Optional admin DB URL.")
    args = parser.parse_args(argv)

    target_tid = uuid.UUID(args.tenant_id) if args.tenant_id else None
    summary = asyncio.run(
        backfill_tenant_adr_partitions(
            args.database_url,
            tenant_id=target_tid,
            dry_run=bool(args.dry_run),
        )
    )
    mode = "DRY-RUN" if args.dry_run else "APPLIED"
    print(
        f"[backfill_adr_partitions] {mode}: checked={summary.tenants_checked}, "
        f"created={summary.partitions_created}, existing={summary.partitions_already_present}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
