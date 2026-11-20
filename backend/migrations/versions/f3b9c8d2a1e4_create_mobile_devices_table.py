"""create_mobile_devices_table

Revision ID: f3b9c8d2a1e4
Revises: e4a9c2f7b1d6
Create Date: 2026-11-19 17:30:00.000000

Jira Requirements:
- SCRUM-494 / Task 72.1 & SCRUM-495 / Task 72.2 (US-72): Create mobile_devices table
  for FCM device registration with (tenant_id, user_id, fcm_token) uniqueness,
  ENABLE/FORCE ROW LEVEL SECURITY, tenant_isolation_policy, and eng_app_user grants.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f3b9c8d2a1e4"
down_revision: Union[str, Sequence[str], None] = "e4a9c2f7b1d6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mobile_devices",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("fcm_token", sa.String(length=512), nullable=False),
        sa.Column(
            "platform",
            sa.String(length=32),
            server_default=sa.text("'android'"),
            nullable=False,
        ),
        sa.Column("device_name", sa.String(length=128), nullable=True),
        sa.Column("app_version", sa.String(length=32), nullable=True),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "user_id", "fcm_token", name="uq_tenant_user_fcm_token"),
    )
    op.create_index("idx_mobile_devices_tenant_id", "mobile_devices", ["tenant_id"], unique=False)

    op.execute("ALTER TABLE mobile_devices ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE mobile_devices FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON mobile_devices;")
    op.execute(
        """
        CREATE POLICY tenant_isolation_policy ON mobile_devices
            AS PERMISSIVE FOR ALL
            TO eng_app_user
            USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON mobile_devices TO eng_app_user;")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON mobile_devices;")
    op.drop_index("idx_mobile_devices_tenant_id", table_name="mobile_devices")
    op.drop_table("mobile_devices")
