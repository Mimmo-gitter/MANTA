"""extend_deployments_us25

Revision ID: c7f4e9b2a1d8
Revises: b8e4c1d9a2f3
Create Date: 2026-11-13 11:05:00.000000

Jira Requirements:
- US-25 / Task 25.2: Extend deployments table with actor_pseudonym_id (String(64))
  composite FK to contributors(tenant_id, pseudonym_id), external_deployment_id (String(128))
  partial unique index, canonical outcome/signal CHECK constraints, composite query index
  (project_id, environment, deployed_at DESC), and relax commit_sha FK on commits.sha.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c7f4e9b2a1d8"
down_revision: Union[str, None] = "b8e4c1d9a2f3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("deployments", sa.Column("actor_pseudonym_id", sa.String(length=64), nullable=True))
    op.add_column(
        "deployments", sa.Column("external_deployment_id", sa.String(length=128), nullable=True)
    )
    op.create_foreign_key(
        "fk_deployments_tenant_actor_pseudonym",
        "deployments",
        "contributors",
        ["tenant_id", "actor_pseudonym_id"],
        ["tenant_id", "pseudonym_id"],
    )
    op.drop_constraint("deployments_commit_sha_fkey", "deployments", type_="foreignkey")
    op.create_check_constraint(
        "ck_deployments_status",
        "deployments",
        "status IN ('SUCCESS', 'FAILED', 'ROLLED_BACK')",
    )
    op.create_check_constraint(
        "ck_deployments_signal_source",
        "deployments",
        "authoritative_signal_source IN ('github_deployments_api', 'argocd_postsync_webhook', 'direct_deployment_api')",
    )
    op.create_index(
        "idx_deployments_project_env_deployed_at",
        "deployments",
        ["project_id", "environment", sa.text("deployed_at DESC")],
        unique=False,
    )
    op.create_index(
        "uq_deployments_tenant_proj_source_ext_id",
        "deployments",
        ["tenant_id", "project_id", "authoritative_signal_source", "external_deployment_id"],
        unique=True,
        postgresql_where=sa.text("external_deployment_id IS NOT NULL"),
    )
    op.create_index(
        "idx_deployments_tenant_commit_sha",
        "deployments",
        ["tenant_id", "commit_sha"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_deployments_tenant_commit_sha", table_name="deployments")
    op.drop_index("uq_deployments_tenant_proj_source_ext_id", table_name="deployments")
    op.drop_index("idx_deployments_project_env_deployed_at", table_name="deployments")
    op.drop_constraint("ck_deployments_signal_source", "deployments", type_="check")
    op.drop_constraint("ck_deployments_status", "deployments", type_="check")
    op.create_foreign_key(
        "deployments_commit_sha_fkey",
        "deployments",
        "commits",
        ["commit_sha"],
        ["sha"],
    )
    op.drop_constraint("fk_deployments_tenant_actor_pseudonym", "deployments", type_="foreignkey")
    op.drop_column("deployments", "external_deployment_id")
    op.drop_column("deployments", "actor_pseudonym_id")
