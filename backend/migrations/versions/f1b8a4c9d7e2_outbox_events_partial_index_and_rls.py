"""outbox_events_partial_index_and_rls

Revision ID: f1b8a4c9d7e2
Revises: 9120ee1923b9
Create Date: 2026-11-11 13:50:00.000000

Jira Requirements:
- SCRUM-476 / US-32: Reconcile outbox_events partial index and relay query requirements
- Adds partial index ix_outbox_events_unpublished ON outbox_events (created_at ASC) WHERE published_at IS NULL;
- Adds fast tenant polling index ix_outbox_events_tenant_published ON outbox_events (tenant_id, published_at);
- Enforces ENABLE and FORCE ROW LEVEL SECURITY and eng_app_user grants on outbox_events and processed_outbox_events
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f1b8a4c9d7e2"
down_revision: Union[str, None] = "9120ee1923b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create partial index for unpublished outbox events relay polling
    op.create_index(
        "ix_outbox_events_unpublished",
        "outbox_events",
        ["created_at"],
        unique=False,
        postgresql_where=sa.text("published_at IS NULL"),
    )

    # 2. Create composite tenant + publication state index for filtered queries
    op.create_index(
        "ix_outbox_events_tenant_published",
        "outbox_events",
        ["tenant_id", "published_at"],
        unique=False,
    )

    # 3. Ensure RLS and privileges for eng_app_user (separate statements for asyncpg)
    op.execute("ALTER TABLE outbox_events ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE outbox_events FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE processed_outbox_events ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE processed_outbox_events FORCE ROW LEVEL SECURITY;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON outbox_events TO eng_app_user;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON processed_outbox_events TO eng_app_user;")


def downgrade() -> None:
    # 1. Drop created indexes
    op.drop_index("ix_outbox_events_tenant_published", table_name="outbox_events")
    op.drop_index("ix_outbox_events_unpublished", table_name="outbox_events")
