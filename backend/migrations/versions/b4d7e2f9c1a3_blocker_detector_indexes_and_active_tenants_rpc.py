"""blocker_detector_indexes_and_active_tenants_rpc

Revision ID: b4d7e2f9c1a3
Revises: a2c4e6f8b0d1
Create Date: 2026-11-25 12:00:00.000000

Jira Requirements:
- TASK-307-1 / SCRUM-543 (STORY-307 — US-71):
  1. Partial unique index `uq_risks_active_condition` on
     `risks (tenant_id, project_id, type, subject_type, subject_id)`
     `WHERE status IN ('OPEN', 'ACKNOWLEDGED')` guaranteeing zero duplicate
     active Risks even under concurrent ARQ blocker detector sweeps.
  2. Composite lookup indexes supporting 15-minute blocker sweeps across
     `risks`, `pull_requests`, `pull_request_reviews`, and `incidents`.
  3. SECURITY DEFINER tenant discovery RPC `list_active_tenants(INT, UUID)`
     with pinned `search_path = public, pg_temp` granted exclusively to
     `eng_app_user` for RLS-safe active tenant pagination.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b4d7e2f9c1a3"
down_revision: Union[str, Sequence[str], None] = "a2c4e6f8b0d1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Partial unique index enforcing at most one active Risk per condition
    op.create_index(
        "uq_risks_active_condition",
        "risks",
        ["tenant_id", "project_id", "type", "subject_type", "subject_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('OPEN', 'ACKNOWLEDGED')"),
    )

    # 2. Composite lookup indexes for 15-minute blocker detector queries
    op.create_index(
        "idx_risks_project_type_status",
        "risks",
        ["tenant_id", "project_id", "type", "status"],
        unique=False,
    )
    op.create_index(
        "idx_pull_requests_blocker_sweep",
        "pull_requests",
        ["tenant_id", "project_id", "state", "is_stale"],
        unique=False,
    )
    op.create_index(
        "idx_pull_request_reviews_pr_submitted_at",
        "pull_request_reviews",
        ["tenant_id", "pull_request_id", "submitted_at"],
        unique=False,
    )
    op.create_index(
        "idx_incidents_project_started_at",
        "incidents",
        ["tenant_id", "project_id", "started_at"],
        unique=False,
    )

    # 3. SECURITY DEFINER active tenant discovery RPC with pinned search_path
    op.execute(
        """
        CREATE OR REPLACE FUNCTION list_active_tenants(
            p_limit INT DEFAULT 1000,
            p_after_id UUID DEFAULT NULL
        )
        RETURNS TABLE (tenant_id UUID)
        LANGUAGE plpgsql
        STABLE
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        BEGIN
            RETURN QUERY
            SELECT t.id
            FROM tenants t
            WHERE LOWER(t.status) = 'active'
              AND (p_after_id IS NULL OR t.id > p_after_id)
            ORDER BY t.id ASC
            LIMIT p_limit;
        END;
        $$;
        """
    )
    op.execute("REVOKE ALL ON FUNCTION list_active_tenants(INT, UUID) FROM PUBLIC;")
    op.execute("GRANT EXECUTE ON FUNCTION list_active_tenants(INT, UUID) TO eng_app_user;")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS list_active_tenants(INT, UUID);")
    op.drop_index("idx_incidents_project_started_at", table_name="incidents")
    op.drop_index(
        "idx_pull_request_reviews_pr_submitted_at",
        table_name="pull_request_reviews",
    )
    op.drop_index("idx_pull_requests_blocker_sweep", table_name="pull_requests")
    op.drop_index("idx_risks_project_type_status", table_name="risks")
    op.drop_index("uq_risks_active_condition", table_name="risks")
