"""Tenant RLS Policies and eng_app_user Role

Revision ID: 5b1a7d8e2093
Revises: 02ec69277327
Create Date: 2026-10-29 11:55:00.000000

Jira Requirements:
- SCRUM-292: Create eng_app_user role (NOSUPERUSER, NOBYPASSRLS) and grants
- SCRUM-318: Write RLS policies and enable FORCE ROW LEVEL SECURITY on tenant tables
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = '5b1a7d8e2093'
down_revision: Union[str, None] = '02ec69277327'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 39 tables with column `tenant_id`
TENANT_ID_TABLES = [
    "acceptance_criteria_versions",
    "adr_sanitization_logs",
    "anonymized_adrs",
    "audit_log",
    "code_analyses",
    "commits",
    "communication_events",
    "contributor_pseudonym_salts",
    "dead_letter_events",
    "deployment_pull_requests",
    "deployments",
    "flaky_tests",
    "incidents",
    "integrations",
    "issue_transitions",
    "issues",
    "job_leases",
    "metric_snapshots",
    "notifications",
    "outbox_events",
    "project_links",
    "project_members",
    "projects",
    "pull_request_reviews",
    "pull_requests",
    "refactoring_suggestions",
    "repositories",
    "requirement_changes",
    "risks",
    "scip_indexes",
    "sprints",
    "test_results",
    "test_runs",
    "user_identities",
    "user_roles",
    "user_sessions",
    "users",
    "webhook_events",
    "wip_limits",
]


def upgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. SCRUM-292: Create eng_app_user role with least privilege
    # -------------------------------------------------------------------------
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'eng_app_user') THEN
                CREATE ROLE eng_app_user WITH 
                    LOGIN 
                    PASSWORD 'eng_app_pass_change_in_prod' 
                    NOSUPERUSER 
                    NOBYPASSRLS 
                    NOCREATEDB 
                    NOCREATEROLE;
            ELSE
                ALTER ROLE eng_app_user WITH 
                    LOGIN 
                    PASSWORD 'eng_app_pass_change_in_prod' 
                    NOSUPERUSER 
                    NOBYPASSRLS 
                    NOCREATEDB 
                    NOCREATEROLE;
            END IF;
        END
        $$;
        """
    )

    # Grant standard application DML privileges to eng_app_user
    op.execute("GRANT USAGE ON SCHEMA public TO eng_app_user;")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO eng_app_user;")
    op.execute("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO eng_app_user;")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO eng_app_user;")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO eng_app_user;")

    # -------------------------------------------------------------------------
    # 2. SCRUM-318: Enable and FORCE Row-Level Security on all tenant tables
    # -------------------------------------------------------------------------
    # Policy for tables with `tenant_id`
    for table_name in TENANT_ID_TABLES:
        op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY;")
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table_name};")
        op.execute(
            f"""
            CREATE POLICY tenant_isolation_policy ON {table_name}
                AS PERMISSIVE FOR ALL
                TO eng_app_user
                USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
                WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
            """
        )

    # Policy for `tenants` table itself (tenant sees only their own record by `id`)
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE tenants FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON tenants;")
    op.execute(
        """
        CREATE POLICY tenant_isolation_policy ON tenants
            AS PERMISSIVE FOR ALL
            TO eng_app_user
            USING (id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
            WITH CHECK (id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
        """
    )


def downgrade() -> None:
    # -------------------------------------------------------------------------
    # 1. Drop RLS policies and disable RLS
    # -------------------------------------------------------------------------
    for table_name in TENANT_ID_TABLES:
        op.execute(f"DROP POLICY IF EXISTS tenant_isolation_policy ON {table_name};")
        op.execute(f"ALTER TABLE {table_name} NO FORCE ROW LEVEL SECURITY;")
        op.execute(f"ALTER TABLE {table_name} DISABLE ROW LEVEL SECURITY;")

    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON tenants;")
    op.execute("ALTER TABLE tenants NO FORCE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE tenants DISABLE ROW LEVEL SECURITY;")

    # -------------------------------------------------------------------------
    # 2. Revoke privileges and drop eng_app_user role
    # -------------------------------------------------------------------------
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE SELECT, INSERT, UPDATE, DELETE ON TABLES FROM eng_app_user;")
    op.execute("ALTER DEFAULT PRIVILEGES IN SCHEMA public REVOKE USAGE, SELECT ON SEQUENCES FROM eng_app_user;")
    op.execute("REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM eng_app_user;")
    op.execute("REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM eng_app_user;")
    op.execute("REVOKE USAGE ON SCHEMA public FROM eng_app_user;")
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT FROM pg_catalog.pg_roles WHERE rolname = 'eng_app_user') THEN
                DROP ROLE eng_app_user;
            END IF;
        END
        $$;
        """
    )
