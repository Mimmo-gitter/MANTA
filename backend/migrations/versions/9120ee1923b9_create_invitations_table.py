"""create_invitations_table

Revision ID: 9120ee1923b9
Revises: e83b4a2c1f90
Create Date: 2026-11-02 08:44:58.406019

Jira Requirements:
- SCRUM-248 / US-12: Single-use expiring invitation tokens for member onboarding
- Enforces RLS and least-privilege eng_app_user grants
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "9120ee1923b9"
down_revision: Union[str, Sequence[str], None] = "e83b4a2c1f90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create `invitations` table
    op.create_table(
        "invitations",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["role"], ["roles.id"]),
        sa.ForeignKeyConstraint(["invited_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_invitations_token_hash"),
    )

    # 2. Secondary lookup index on tenant_id for RLS query efficiency
    op.create_index("idx_invitations_tenant_id", "invitations", ["tenant_id"], unique=False)

    # 3. Enable and force Row Level Security
    op.execute("ALTER TABLE invitations ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE invitations FORCE ROW LEVEL SECURITY;")

    # 4. Create tenant isolation policy bound to eng_app_user with WITH CHECK
    op.execute(
        """
        CREATE POLICY tenant_isolation_policy ON invitations
            AS PERMISSIVE FOR ALL
            TO eng_app_user
            USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
        """
    )

    # 5. Grant access permissions to eng_app_user
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON invitations TO eng_app_user;")


def downgrade() -> None:
    # 1. Drop RLS policy
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON invitations CASCADE;")

    # 2. Drop index
    op.drop_index("idx_invitations_tenant_id", table_name="invitations")

    # 3. Drop table
    op.drop_table("invitations")
