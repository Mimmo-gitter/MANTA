"""production_failures_and_project_health_config

Revision ID: e4a9c2f7b1d6
Revises: d8a2f4c9e1b3
Create Date: 2026-11-18 10:50:00.000000

Jira Requirements:
- SCRUM-452 / Task 68.3 (US-68): Create production_failures table under tenant
  FORCE ROW LEVEL SECURITY (eng_app_user), add health probe configuration
  fields on projects, and expose SECURITY DEFINER route lookup
  get_project_health_webhook_route(UUID) for authenticated webhook intake.
- SCRUM-453 / Task 68.4 (US-68): Enforce partial unique index
  uq_production_failures_one_open_per_project guaranteeing at most one OPEN
  failure session per project at the storage layer.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e4a9c2f7b1d6"
down_revision: Union[str, Sequence[str], None] = "d8a2f4c9e1b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Extend projects with health probe configuration and live health status
    # -------------------------------------------------------------------------
    op.add_column(
        "projects",
        sa.Column(
            "health_probe_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "health_probe_source",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'webhook'"),
        ),
    )
    op.add_column(
        "projects",
        sa.Column("health_webhook_secret_hash", sa.Text(), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column("health_probe_url", sa.String(length=1024), nullable=True),
    )
    op.add_column(
        "projects",
        sa.Column(
            "health_probe_interval_seconds",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("60"),
        ),
    )
    op.add_column(
        "projects",
        sa.Column(
            "health_status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'HEALTHY'"),
        ),
    )

    # -------------------------------------------------------------------------
    # 2. Create production_failures table for US-68 / US-29 MTTR sessions
    # -------------------------------------------------------------------------
    op.create_table(
        "production_failures",
        sa.Column("id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("incident_id", sa.UUID(as_uuid=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_time_seconds", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'OPEN'"),
        ),
        sa.Column(
            "probe_source",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("'webhook'"),
        ),
        sa.Column(
            "severity",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'SEV-1'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "status IN ('OPEN', 'RESOLVED')",
            name="ck_production_failures_status",
        ),
        sa.CheckConstraint(
            "resolved_at IS NULL OR resolved_at >= started_at",
            name="ck_production_failures_recovery_non_negative",
        ),
    )

    op.create_index(
        "idx_production_failures_tenant_id",
        "production_failures",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "idx_production_failures_project_status",
        "production_failures",
        ["tenant_id", "project_id", "status"],
        unique=False,
    )
    op.create_index(
        "idx_production_failures_project_resolved_at",
        "production_failures",
        ["tenant_id", "project_id", "resolved_at"],
        unique=False,
    )
    op.create_index(
        "uq_production_failures_one_open_per_project",
        "production_failures",
        ["tenant_id", "project_id"],
        unique=True,
        postgresql_where=sa.text("status = 'OPEN'"),
    )

    # -------------------------------------------------------------------------
    # 3. Enforce ENABLE + FORCE ROW LEVEL SECURITY for eng_app_user
    # -------------------------------------------------------------------------
    op.execute("ALTER TABLE production_failures ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE production_failures FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON production_failures;")
    op.execute(
        """
        CREATE POLICY tenant_isolation_policy ON production_failures
            AS PERMISSIVE FOR ALL
            TO eng_app_user
            USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON production_failures TO eng_app_user;")

    # -------------------------------------------------------------------------
    # 4. SECURITY DEFINER project health webhook route lookup function
    # -------------------------------------------------------------------------
    op.execute(
        """
        CREATE OR REPLACE FUNCTION get_project_health_webhook_route(p_project_id UUID)
        RETURNS TABLE (
            id UUID,
            tenant_id UUID,
            health_probe_enabled BOOLEAN,
            health_probe_source VARCHAR(64),
            health_webhook_secret_hash TEXT,
            health_status VARCHAR(32),
            is_active BOOLEAN,
            deleted_at TIMESTAMPTZ
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        BEGIN
            RETURN QUERY
            SELECT
                p.id,
                p.tenant_id,
                p.health_probe_enabled,
                p.health_probe_source::VARCHAR(64),
                p.health_webhook_secret_hash,
                p.health_status::VARCHAR(32),
                p.is_active,
                p.deleted_at
            FROM projects p
            WHERE p.id = p_project_id;
        END;
        $$;
        """
    )
    op.execute(
        "REVOKE EXECUTE ON FUNCTION get_project_health_webhook_route(UUID) FROM PUBLIC;"
    )
    op.execute(
        "GRANT EXECUTE ON FUNCTION get_project_health_webhook_route(UUID) TO eng_app_user;"
    )


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS get_project_health_webhook_route(UUID);")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON production_failures;")
    op.drop_index(
        "uq_production_failures_one_open_per_project",
        table_name="production_failures",
    )
    op.drop_index(
        "idx_production_failures_project_resolved_at",
        table_name="production_failures",
    )
    op.drop_index(
        "idx_production_failures_project_status",
        table_name="production_failures",
    )
    op.drop_index(
        "idx_production_failures_tenant_id",
        table_name="production_failures",
    )
    op.drop_table("production_failures")

    op.drop_column("projects", "health_status")
    op.drop_column("projects", "health_probe_interval_seconds")
    op.drop_column("projects", "health_probe_url")
    op.drop_column("projects", "health_webhook_secret_hash")
    op.drop_column("projects", "health_probe_source")
    op.drop_column("projects", "health_probe_enabled")
