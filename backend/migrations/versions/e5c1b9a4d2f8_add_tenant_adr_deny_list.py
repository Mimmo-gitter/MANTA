"""add_tenant_adr_deny_list

Revision ID: e5c1b9a4d2f8
Revises: d4f8a2c6e1b9
Create Date: 2026-11-28 12:30:00.000000

Jira Requirements:
- SCRUM-515 / TASK-315-0 & TASK-315-1 (US-41):
  Add tenant-scoped `adr_deny_list` JSONB column on `tenants` (protected by existing
  ENABLE + FORCE ROW LEVEL SECURITY `tenant_isolation_policy` on `tenants`) so each
  tenant can configure custom company aliases, codenames, and regex patterns for ADR
  Presidio scrubbing.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "e5c1b9a4d2f8"
down_revision: Union[str, Sequence[str], None] = "d4f8a2c6e1b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tenants",
        sa.Column(
            "adr_deny_list",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("tenants", "adr_deny_list")
