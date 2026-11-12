"""
Task 23.3: Composite index on dead_letter_events for unresolved quarantine lookups.

Revision ID: b3e9f2a6d4c1
Revises: a9c5e1d7b3f2
Create Date: 2026-11-12 12:50:00.000000

Adds:
idx_dead_letter_events_unresolved ON dead_letter_events
    (tenant_id, is_resolved, quarantined_at DESC)
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b3e9f2a6d4c1"
down_revision: Union[str, None] = "a9c5e1d7b3f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "idx_dead_letter_events_unresolved",
        "dead_letter_events",
        ["tenant_id", "is_resolved", sa.text("quarantined_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_dead_letter_events_unresolved",
        table_name="dead_letter_events",
    )
