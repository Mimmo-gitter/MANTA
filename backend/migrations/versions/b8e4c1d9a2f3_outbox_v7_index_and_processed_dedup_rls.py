"""outbox_v7_index_and_processed_dedup_rls

Revision ID: b8e4c1d9a2f3
Revises: b3e9f2a6d4c1
Create Date: 2026-11-12 21:15:00.000000

Jira Requirements:
- US-33 / Task 33.2: Add partial index ix_outbox_events_unpublished_id ON outbox_events (id)
  WHERE published_at IS NULL for monotonic UUIDv7 ORDER BY id ASC LIMIT 100 relay polling,
  plus SECURITY DEFINER tenant discovery RPC list_tenants_with_unpublished_outbox_events.
- US-33 / Task 33.3: Reconcile processed_outbox_events to (event_id PK, tenant_id FK NOT NULL,
  processed_at TIMESTAMPTZ DEFAULT now()), add tenant_isolation_policy for eng_app_user
  (resolving zero-policy FORCE RLS lockout), add 30-day purge index and discovery RPC.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b8e4c1d9a2f3"
down_revision: Union[str, None] = "b3e9f2a6d4c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Partial index supporting Task 33.2 ORDER BY id ASC LIMIT 100 relay polling
    op.create_index(
        "ix_outbox_events_unpublished_id",
        "outbox_events",
        ["id"],
        unique=False,
        postgresql_where=sa.text("published_at IS NULL"),
    )

    # 2. Reconcile processed_outbox_events to (event_id PK, tenant_id, processed_at)
    op.drop_constraint("processed_outbox_events_pkey", "processed_outbox_events", type_="primary")
    op.alter_column(
        "processed_outbox_events",
        "outbox_event_id",
        new_column_name="event_id",
        existing_type=sa.UUID(),
        existing_nullable=False,
    )
    op.drop_column("processed_outbox_events", "consumer_name")
    op.add_column(
        "processed_outbox_events",
        sa.Column("tenant_id", sa.UUID(), nullable=False),
    )
    op.alter_column(
        "processed_outbox_events",
        "processed_at",
        server_default=sa.text("now()"),
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )
    op.create_primary_key("pk_processed_outbox_events", "processed_outbox_events", ["event_id"])
    op.create_foreign_key(
        "processed_outbox_events_tenant_id_fkey",
        "processed_outbox_events",
        "tenants",
        ["tenant_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "idx_processed_outbox_events_tenant_id",
        "processed_outbox_events",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "idx_processed_outbox_events_processed_at",
        "processed_outbox_events",
        ["processed_at"],
        unique=False,
    )

    # 3. Enforce RLS + tenant_isolation_policy for eng_app_user on processed_outbox_events
    op.execute("ALTER TABLE processed_outbox_events ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE processed_outbox_events FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON processed_outbox_events;")
    op.execute(
        """
        CREATE POLICY tenant_isolation_policy ON processed_outbox_events
            AS PERMISSIVE
            FOR ALL
            TO eng_app_user
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid);
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON processed_outbox_events TO eng_app_user;")

    # 4. SECURITY DEFINER tenant discovery RPCs for RLS-safe relay & 30-day purge
    op.execute(
        """
        CREATE OR REPLACE FUNCTION list_tenants_with_unpublished_outbox_events(
            p_limit INT DEFAULT 100
        )
        RETURNS TABLE (tenant_id UUID)
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        BEGIN
            RETURN QUERY
            SELECT o.tenant_id
            FROM outbox_events o
            WHERE o.published_at IS NULL
            GROUP BY o.tenant_id
            ORDER BY MIN(o.id) ASC
            LIMIT p_limit;
        END;
        $$;
        """
    )
    op.execute(
        "REVOKE EXECUTE ON FUNCTION list_tenants_with_unpublished_outbox_events(INT) FROM PUBLIC;"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION list_tenants_with_unpublished_outbox_events(INT) "
        "TO eng_app_user;"
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION list_tenants_with_expired_processed_outbox_events(
            p_cutoff TIMESTAMPTZ,
            p_limit INT DEFAULT 100
        )
        RETURNS TABLE (tenant_id UUID)
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        BEGIN
            RETURN QUERY
            SELECT p.tenant_id
            FROM processed_outbox_events p
            WHERE p.processed_at < p_cutoff
            GROUP BY p.tenant_id
            ORDER BY MIN(p.processed_at) ASC
            LIMIT p_limit;
        END;
        $$;
        """
    )
    op.execute(
        "REVOKE EXECUTE ON FUNCTION "
        "list_tenants_with_expired_processed_outbox_events(TIMESTAMPTZ, INT) FROM PUBLIC;"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION "
        "list_tenants_with_expired_processed_outbox_events(TIMESTAMPTZ, INT) TO eng_app_user;"
    )


def downgrade() -> None:
    op.execute(
        "DROP FUNCTION IF EXISTS "
        "list_tenants_with_expired_processed_outbox_events(TIMESTAMPTZ, INT);"
    )
    op.execute("DROP FUNCTION IF EXISTS list_tenants_with_unpublished_outbox_events(INT);")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON processed_outbox_events;")

    op.drop_index("idx_processed_outbox_events_processed_at", table_name="processed_outbox_events")
    op.drop_index("idx_processed_outbox_events_tenant_id", table_name="processed_outbox_events")
    op.drop_constraint(
        "processed_outbox_events_tenant_id_fkey",
        "processed_outbox_events",
        type_="foreignkey",
    )
    op.drop_constraint("pk_processed_outbox_events", "processed_outbox_events", type_="primary")

    op.alter_column(
        "processed_outbox_events",
        "processed_at",
        server_default=None,
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )
    op.drop_column("processed_outbox_events", "tenant_id")
    op.add_column(
        "processed_outbox_events",
        sa.Column("consumer_name", sa.String(length=128), nullable=False, server_default="default"),
    )
    op.alter_column(
        "processed_outbox_events",
        "consumer_name",
        server_default=None,
        existing_type=sa.String(length=128),
        existing_nullable=False,
    )
    op.alter_column(
        "processed_outbox_events",
        "event_id",
        new_column_name="outbox_event_id",
        existing_type=sa.UUID(),
        existing_nullable=False,
    )
    op.create_primary_key(
        "processed_outbox_events_pkey",
        "processed_outbox_events",
        ["outbox_event_id", "consumer_name"],
    )

    op.drop_index("ix_outbox_events_unpublished_id", table_name="outbox_events")
