"""contributors_jira_and_deployment_incidents

Revision ID: c4b1a8d3e5f7
Revises: f1b8a4c9d7e2
Create Date: 2026-11-11 15:10:00.000000

Jira Requirements:
- SCRUM-393 / US-20: Contributors table, pull_requests/commits extensions with FORCE RLS
- SCRUM-399 / US-67: Jira issues extensions, sprints extensions, and deployment_incidents with FORCE RLS
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c4b1a8d3e5f7"
down_revision: Union[str, None] = "f1b8a4c9d7e2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. SCRUM-393: Create contributors table with FORCE ROW LEVEL SECURITY
    # -------------------------------------------------------------------------
    op.create_table(
        "contributors",
        sa.Column("pseudonym_id", sa.String(length=64), nullable=False),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("external_actor_id", sa.String(length=128), nullable=False),
        sa.Column("raw_name", sa.Text(), nullable=True),
        sa.Column("raw_email", sa.Text(), nullable=True),
        sa.Column("raw_handle", sa.String(length=255), nullable=True),
        sa.Column("salt_secret", sa.Text(), nullable=False),
        sa.Column("is_erased", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("erased_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("tenant_id", "pseudonym_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "provider",
            "external_actor_id",
            name="uq_contributors_tenant_provider_actor",
        ),
    )
    op.create_index("idx_contributors_tenant_id", "contributors", ["tenant_id"], unique=False)
    op.create_index("idx_contributors_pseudonym", "contributors", ["pseudonym_id"], unique=False)

    # Enable and FORCE RLS on contributors
    op.execute("ALTER TABLE contributors ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE contributors FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON contributors;")
    op.execute(
        """
        CREATE POLICY tenant_isolation_policy ON contributors
            AS PERMISSIVE FOR ALL
            TO eng_app_user
            USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON contributors TO eng_app_user;")

    # -------------------------------------------------------------------------
    # 2. SCRUM-393: Extend pull_requests
    # -------------------------------------------------------------------------
    op.add_column("pull_requests", sa.Column("merge_commit_sha", sa.String(length=40), nullable=True))
    op.add_column(
        "pull_requests",
        sa.Column(
            "active_fencing_token", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.create_index(
        "idx_pull_requests_merge_commit",
        "pull_requests",
        ["tenant_id", "merge_commit_sha"],
        unique=False,
    )

    # -------------------------------------------------------------------------
    # 3. SCRUM-393: Extend commits
    # -------------------------------------------------------------------------
    op.add_column(
        "commits",
        sa.Column(
            "committer_pseudonym_id",
            sa.String(length=64),
            nullable=False,
            server_default=sa.text("''"),
        ),
    )
    op.add_column(
        "commits",
        sa.Column(
            "parent_shas",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("commits", sa.Column("pull_request_id", sa.UUID(as_uuid=True), nullable=True))
    op.add_column(
        "commits",
        sa.Column(
            "active_fencing_token", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.create_foreign_key(
        "fk_commits_pull_request",
        "commits",
        "pull_requests",
        ["pull_request_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "idx_commits_pull_request_id",
        "commits",
        ["tenant_id", "pull_request_id"],
        unique=False,
    )

    # -------------------------------------------------------------------------
    # 4. SCRUM-399: Extend sprints
    # -------------------------------------------------------------------------
    op.add_column(
        "sprints",
        sa.Column(
            "active_fencing_token", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column("sprints", sa.Column("complete_date", sa.DateTime(timezone=True), nullable=True))

    # -------------------------------------------------------------------------
    # 5. SCRUM-399: Extend issues
    # -------------------------------------------------------------------------
    op.add_column("issues", sa.Column("external_issue_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "issues",
        sa.Column(
            "issue_links",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column("issues", sa.Column("changelog_id", sa.BigInteger(), nullable=True))
    op.add_column(
        "issues",
        sa.Column(
            "active_fencing_token", sa.BigInteger(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column("issues", sa.Column("acceptance_criteria", sa.Text(), nullable=True))
    op.add_column(
        "issues", sa.Column("acceptance_criteria_hash", sa.String(length=64), nullable=True)
    )
    op.add_column("issues", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "idx_issues_tenant_sprint_status",
        "issues",
        ["tenant_id", "sprint_id", "status"],
        unique=False,
    )
    op.create_index(
        "idx_issues_changelog_precedence",
        "issues",
        ["tenant_id", "external_key", "changelog_id"],
        unique=False,
    )
    op.create_index(
        "idx_issues_resolved_at", "issues", ["tenant_id", "resolved_at"], unique=False
    )

    # -------------------------------------------------------------------------
    # 6. SCRUM-399: Create deployment_incidents with FORCE ROW LEVEL SECURITY
    # -------------------------------------------------------------------------
    op.create_table(
        "deployment_incidents",
        sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("deployment_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("issue_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'CONFIRMED'"),
        ),
        sa.Column("correlation_source", sa.String(length=64), nullable=False),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("recovery_time_seconds", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["deployment_id"], ["deployments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["issue_id"], ["issues.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "deployment_id", "issue_id", name="uq_deployment_incidents_deployment_issue"
        ),
        sa.CheckConstraint(
            "status IN ('CONFIRMED', 'CANCELLED', 'FALSE_POSITIVE')",
            name="ck_deployment_incidents_status",
        ),
    )
    op.create_index(
        "idx_deployment_incidents_tenant_id",
        "deployment_incidents",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        "idx_deployment_incidents_deployment_id",
        "deployment_incidents",
        ["deployment_id"],
        unique=False,
    )
    op.create_index(
        "idx_deployment_incidents_issue_id",
        "deployment_incidents",
        ["issue_id"],
        unique=False,
    )
    op.create_index(
        "idx_deployment_incidents_window",
        "deployment_incidents",
        ["tenant_id", "detected_at"],
        unique=False,
    )

    # Enable and FORCE RLS on deployment_incidents
    op.execute("ALTER TABLE deployment_incidents ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE deployment_incidents FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON deployment_incidents;")
    op.execute(
        """
        CREATE POLICY tenant_isolation_policy ON deployment_incidents
            AS PERMISSIVE FOR ALL
            TO eng_app_user
            USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON deployment_incidents TO eng_app_user;")


def downgrade() -> None:
    # 1. Drop deployment_incidents
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON deployment_incidents;")
    op.drop_index("idx_deployment_incidents_window", table_name="deployment_incidents")
    op.drop_index("idx_deployment_incidents_issue_id", table_name="deployment_incidents")
    op.drop_index("idx_deployment_incidents_deployment_id", table_name="deployment_incidents")
    op.drop_index("idx_deployment_incidents_tenant_id", table_name="deployment_incidents")
    op.drop_table("deployment_incidents")

    # 2. Revert issues extensions
    op.drop_index("idx_issues_resolved_at", table_name="issues")
    op.drop_index("idx_issues_changelog_precedence", table_name="issues")
    op.drop_index("idx_issues_tenant_sprint_status", table_name="issues")
    op.drop_column("issues", "resolved_at")
    op.drop_column("issues", "acceptance_criteria_hash")
    op.drop_column("issues", "acceptance_criteria")
    op.drop_column("issues", "active_fencing_token")
    op.drop_column("issues", "changelog_id")
    op.drop_column("issues", "issue_links")
    op.drop_column("issues", "external_issue_id")

    # 3. Revert sprints extensions
    op.drop_column("sprints", "complete_date")
    op.drop_column("sprints", "active_fencing_token")

    # 4. Revert commits extensions
    op.drop_index("idx_commits_pull_request_id", table_name="commits")
    op.drop_constraint("fk_commits_pull_request", "commits", type_="foreignkey")
    op.drop_column("commits", "active_fencing_token")
    op.drop_column("commits", "pull_request_id")
    op.drop_column("commits", "parent_shas")
    op.drop_column("commits", "committer_pseudonym_id")

    # 5. Revert pull_requests extensions
    op.drop_index("idx_pull_requests_merge_commit", table_name="pull_requests")
    op.drop_column("pull_requests", "active_fencing_token")
    op.drop_column("pull_requests", "merge_commit_sha")

    # 6. Drop contributors
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON contributors;")
    op.drop_index("idx_contributors_pseudonym", table_name="contributors")
    op.drop_index("idx_contributors_tenant_id", table_name="contributors")
    op.drop_table("contributors")
