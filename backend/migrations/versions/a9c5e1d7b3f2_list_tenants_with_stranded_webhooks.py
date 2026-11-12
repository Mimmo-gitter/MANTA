"""SCRUM-388: SECURITY DEFINER tenant discovery for staged webhook recovery sweeper.

Revision ID: a9c5e1d7b3f2
Revises: e7d3a2c5b8f1
Create Date: 2026-11-12 10:00:00.000000

Creates a SECURITY DEFINER function with pinned search_path to discover
tenant_ids with stranded 'staged' webhook events older than a cutoff timestamp.
This allows the background recovery sweeper (running under eng_app_user without
a prior tenant context) to discover which tenants require recovery sweeps,
and then execute RLS-isolated batch claims per tenant.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a9c5e1d7b3f2"
down_revision: Union[str, None] = "e7d3a2c5b8f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create SECURITY DEFINER tenant discovery function with pinned search_path
    op.execute(
        """
        CREATE OR REPLACE FUNCTION list_tenants_with_stranded_webhooks(
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
            SELECT w.tenant_id
            FROM webhook_events w
            WHERE w.status = 'staged'
              AND w.received_at <= p_cutoff
            GROUP BY w.tenant_id
            ORDER BY MIN(w.received_at) ASC
            LIMIT p_limit;
        END;
        $$;
        """
    )

    # 2. Revoke from PUBLIC, grant exclusively to eng_app_user
    op.execute(
        """
        REVOKE EXECUTE ON FUNCTION list_tenants_with_stranded_webhooks(TIMESTAMPTZ, INT) FROM PUBLIC;
        """
    )
    op.execute(
        """
        GRANT EXECUTE ON FUNCTION list_tenants_with_stranded_webhooks(TIMESTAMPTZ, INT) TO eng_app_user;
        """
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS list_tenants_with_stranded_webhooks(TIMESTAMPTZ, INT);")
