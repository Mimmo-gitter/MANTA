"""add_test_run_sandbox_outcome_columns

Revision ID: f7a2c8e1b4d9
Revises: e5c1b9a4d2f8
Create Date: 2026-11-28 13:45:00.000000

Jira Requirements:
- SCRUM-522 / TASK-322-0 & TASK-322-1 (US-48):
  Extend `test_runs` (already protected by ENABLE + FORCE ROW LEVEL SECURITY
  `tenant_isolation_policy`) with sandbox runtime/digest audit metadata and
  structured `VERIFICATION_FAILED` (`SANDBOX_TIMEOUT`) failure columns.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f7a2c8e1b4d9"
down_revision: Union[str, Sequence[str], None] = "e5c1b9a4d2f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("test_runs", sa.Column("sandbox_runtime", sa.String(length=32), nullable=True))
    op.add_column(
        "test_runs", sa.Column("sandbox_image_digest", sa.String(length=255), nullable=True)
    )
    op.add_column("test_runs", sa.Column("exit_code", sa.Integer(), nullable=True))
    op.add_column("test_runs", sa.Column("failure_code", sa.String(length=64), nullable=True))
    op.add_column(
        "test_runs",
        sa.Column("failure_details", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.add_column(
        "test_runs", sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.create_index(
        "idx_test_runs_tenant_repo_created",
        "test_runs",
        ["tenant_id", "repository_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_test_runs_tenant_repo_created", table_name="test_runs")
    op.drop_column("test_runs", "completed_at")
    op.drop_column("test_runs", "failure_details")
    op.drop_column("test_runs", "failure_code")
    op.drop_column("test_runs", "exit_code")
    op.drop_column("test_runs", "sandbox_image_digest")
    op.drop_column("test_runs", "sandbox_runtime")
