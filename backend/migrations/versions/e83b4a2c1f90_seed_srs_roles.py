"""Seed SRS canonical roles and deprecate DELIVERY_EXEC alias (SCRUM-306)

Revision ID: e83b4a2c1f90
Revises: 8d7a12b4e5f6
Create Date: 2026-10-30 20:50:00.000000

Jira Requirements:
- SCRUM-306 / US-11: Seed SRS §2.3 canonical roles (PROJECT_MANAGER, DELIVERY_DIRECTOR, TOP_MANAGEMENT)
- Update DELIVERY_EXEC description to denote deprecated alias of DELIVERY_DIRECTOR
- Preserve DELIVERY_EXEC in roles table for foreign key integrity with user_roles
"""

from typing import Sequence, Union
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e83b4a2c1f90"
down_revision: Union[str, None] = "8d7a12b4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Seed canonical SRS roles in `roles` table
    op.execute(
        """
        INSERT INTO roles (id, description) VALUES
            ('PROJECT_MANAGER', 'Project Manager responsible for sprint planning and scope creep alerts'),
            ('DELIVERY_DIRECTOR', 'Delivery Director responsible for portfolio delivery health and roadblock intervention'),
            ('TOP_MANAGEMENT', 'Top Management executive with strategic read-only portfolio governance')
        ON CONFLICT (id) DO NOTHING;
        """
    )

    # 2. Update description of legacy DELIVERY_EXEC role to indicate deprecated alias status
    op.execute(
        """
        UPDATE roles
        SET description = 'DEPRECATED alias of DELIVERY_DIRECTOR; legacy executive oversight role'
        WHERE id = 'DELIVERY_EXEC';
        """
    )


def downgrade() -> None:
    # 1. Restore legacy description for DELIVERY_EXEC
    op.execute(
        """
        UPDATE roles
        SET description = 'Delivery Executive / Management oversight role'
        WHERE id = 'DELIVERY_EXEC';
        """
    )

    # 2. Remove seeded roles only if not referenced by user_roles
    op.execute(
        """
        DELETE FROM roles
        WHERE id IN ('PROJECT_MANAGER', 'DELIVERY_DIRECTOR', 'TOP_MANAGEMENT')
          AND id NOT IN (SELECT DISTINCT role_id FROM user_roles);
        """
    )
