"""
SCRUM-387: Partial index on webhook_events for stranded event sweeper recovery.

Revision ID: e7d3a2c5b8f1
Revises: c4b1a8d3e5f7
Create Date: 2026-11-11 15:45:00.000000

Adds partial index:
idx_webhook_events_staged_received ON webhook_events (received_at) WHERE status = 'staged'
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e7d3a2c5b8f1"
down_revision: Union[str, None] = "c4b1a8d3e5f7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # SCRUM-387: Partial index for non-blocking FOR UPDATE SKIP LOCKED sweeper
    # -------------------------------------------------------------------------
    op.create_index(
        "idx_webhook_events_staged_received",
        "webhook_events",
        ["received_at"],
        unique=False,
        postgresql_where=sa.text("status = 'staged'"),
    )


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # Drop partial index
    # -------------------------------------------------------------------------
    op.drop_index(
        "idx_webhook_events_staged_received",
        table_name="webhook_events",
    )
