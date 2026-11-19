"""fencing_versioning_and_dora_indexes

Revision ID: a2c4e6f8b0d1
Revises: f3b9c8d2a1e4
Create Date: 2026-11-19 23:45:00.000000

Jira Requirements:
- SCRUM-412 (Task 22.4): Add `active_fencing_token` (BigInteger, server_default 0) to
  `webhook_events` and `deployments` for uniform PostgreSQL fencing token enforcement.
- SCRUM-423 (Task 24.2): Add `entity_version` (BigInteger, server_default 1) to
  `communication_events` for out-of-order Slack event rejection.
- SCRUM-459 (Task 30.3): Create composite DORA lookup indexes on `deployments`,
  `deployment_pull_requests`, and `production_failures` so consolidated generate_series
  trend aggregation maintains p95 < 300ms under tenant RLS.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a2c4e6f8b0d1"
down_revision: Union[str, Sequence[str], None] = "f3b9c8d2a1e4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # SCRUM-412: active_fencing_token on webhook_events and deployments
    op.add_column(
        "webhook_events",
        sa.Column(
            "active_fencing_token",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.add_column(
        "deployments",
        sa.Column(
            "active_fencing_token",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )

    # SCRUM-423: entity_version on communication_events
    op.add_column(
        "communication_events",
        sa.Column(
            "entity_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )

    # SCRUM-459: composite indexes for DORA date-series query optimization
    op.create_index(
        "idx_deployments_dora_lookup",
        "deployments",
        ["tenant_id", "project_id", "environment", "authoritative_signal_source", "deployed_at"],
        unique=False,
    )
    op.create_index(
        "idx_deployment_pull_requests_tenant_dep_pr",
        "deployment_pull_requests",
        ["tenant_id", "deployment_id", "pull_request_id"],
        unique=False,
    )
    op.create_index(
        "idx_production_failures_dora_mttr",
        "production_failures",
        ["tenant_id", "project_id", "status", "resolved_at", "started_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_production_failures_dora_mttr", table_name="production_failures")
    op.drop_index(
        "idx_deployment_pull_requests_tenant_dep_pr", table_name="deployment_pull_requests"
    )
    op.drop_index("idx_deployments_dora_lookup", table_name="deployments")
    op.drop_column("communication_events", "entity_version")
    op.drop_column("deployments", "active_fencing_token")
    op.drop_column("webhook_events", "active_fencing_token")
