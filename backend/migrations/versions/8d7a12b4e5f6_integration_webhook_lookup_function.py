"""SCRUM-327: SECURITY DEFINER integration lookup for webhook intake.

Revision ID: 8d7a12b4e5f6
Revises: c74f19b28a01
Create Date: 2026-10-30 19:55:00.000000

Creates a SECURITY DEFINER function with pinned search_path to resolve
an integration's routing metadata (tenant_id, provider, webhook_secret_hash)
by its high-entropy unguessable integration_id before the tenant context is known.
"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8d7a12b4e5f6"
down_revision: Union[str, None] = "c74f19b28a01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create SECURITY DEFINER lookup function with pinned search_path
    op.execute(
        """
        CREATE OR REPLACE FUNCTION get_webhook_integration_route(p_integration_id UUID)
        RETURNS TABLE (
            id UUID,
            tenant_id UUID,
            provider VARCHAR(32),
            webhook_secret_hash TEXT,
            is_active BOOLEAN
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        BEGIN
            RETURN QUERY
            SELECT
                i.id,
                i.tenant_id,
                i.provider::VARCHAR(32),
                i.webhook_secret_hash,
                i.is_active
            FROM integrations i
            WHERE i.id = p_integration_id;
        END;
        $$;
        """
    )

    # 2. Revoke from PUBLIC, grant exclusively to eng_app_user
    op.execute(
        """
        REVOKE EXECUTE ON FUNCTION get_webhook_integration_route(UUID) FROM PUBLIC;
        """
    )
    op.execute(
        """
        GRANT EXECUTE ON FUNCTION get_webhook_integration_route(UUID) TO eng_app_user;
        """
    )


def downgrade() -> None:
    # Drop lookup function
    op.execute("DROP FUNCTION IF EXISTS get_webhook_integration_route(UUID);")
