"""create_adr_sources_and_update_anonymized_adr_draft

Revision ID: c9a3f7e1d2b4
Revises: b4d7e2f9c1a3
Create Date: 2026-11-26 21:05:00.000000

Jira Requirements:
- SCRUM-563 / TASK-314-1 (US-40):
  1. Create tenant-only `adr_sources` table with ENABLE + FORCE ROW LEVEL SECURITY,
     `tenant_isolation_policy`, and `eng_app_user` DML grants to keep raw ADR
     Markdown and repository file paths strictly isolated from `anonymized_adrs`.
  2. Update `anonymized_adrs` for the `draft` extraction lifecycle metadata
     (nullable `decision_summary`, `draft` default status, status check constraint,
     and source commit/content-hash metadata with zero raw source text or paths).
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c9a3f7e1d2b4"
down_revision: Union[str, Sequence[str], None] = "b4d7e2f9c1a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Create tenant-only adr_sources table
    op.create_table(
        "adr_sources",
        sa.Column(
            "id",
            sa.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("tenant_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("repository_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("adr_id", sa.UUID(as_uuid=True), nullable=False),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("raw_markdown", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "extracted_at",
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
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["repository_id"], ["repositories.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "adr_id", name="uq_adr_sources_tenant_adr"),
        sa.UniqueConstraint(
            "tenant_id",
            "repository_id",
            "file_path",
            name="uq_adr_sources_tenant_repo_path",
        ),
    )
    op.create_index("idx_adr_sources_tenant_id", "adr_sources", ["tenant_id"], unique=False)
    op.create_index(
        "idx_adr_sources_tenant_adr_id",
        "adr_sources",
        ["tenant_id", "adr_id"],
        unique=False,
    )

    op.execute("ALTER TABLE adr_sources ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE adr_sources FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON adr_sources;")
    op.execute(
        """
        CREATE POLICY tenant_isolation_policy ON adr_sources
            AS PERMISSIVE FOR ALL
            TO eng_app_user
            USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON adr_sources TO eng_app_user;")

    # 2. Update anonymized_adrs for draft lifecycle metadata (zero raw Markdown/file paths)
    op.alter_column(
        "anonymized_adrs",
        "decision_summary",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.alter_column(
        "anonymized_adrs",
        "status",
        existing_type=sa.String(length=32),
        server_default=sa.text("'draft'"),
        existing_nullable=False,
    )
    op.add_column(
        "anonymized_adrs",
        sa.Column("source_repository_id", sa.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "anonymized_adrs",
        sa.Column("source_commit_sha", sa.String(length=40), nullable=True),
    )
    op.add_column(
        "anonymized_adrs",
        sa.Column("source_content_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "anonymized_adrs",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_anonymized_adrs_status",
        "anonymized_adrs",
        "status IN ('draft', 'pending_review', 'approved', 'quarantined', 'rejected')",
    )
    op.create_index(
        "idx_anonymized_adrs_tenant_status",
        "anonymized_adrs",
        ["tenant_id", "status"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("idx_anonymized_adrs_tenant_status", table_name="anonymized_adrs")
    op.drop_constraint("ck_anonymized_adrs_status", "anonymized_adrs", type_="check")
    op.drop_column("anonymized_adrs", "updated_at")
    op.drop_column("anonymized_adrs", "source_content_hash")
    op.drop_column("anonymized_adrs", "source_commit_sha")
    op.drop_column("anonymized_adrs", "source_repository_id")
    op.alter_column(
        "anonymized_adrs",
        "status",
        existing_type=sa.String(length=32),
        server_default=None,
        existing_nullable=False,
    )
    op.execute(
        "UPDATE anonymized_adrs SET decision_summary = '' WHERE decision_summary IS NULL;"
    )
    op.alter_column(
        "anonymized_adrs",
        "decision_summary",
        existing_type=sa.Text(),
        nullable=False,
    )

    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON adr_sources;")
    op.drop_index("idx_adr_sources_tenant_adr_id", table_name="adr_sources")
    op.drop_index("idx_adr_sources_tenant_id", table_name="adr_sources")
    op.drop_table("adr_sources")
