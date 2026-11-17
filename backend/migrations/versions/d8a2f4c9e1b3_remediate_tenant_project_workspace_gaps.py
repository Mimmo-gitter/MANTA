"""remediate_tenant_project_workspace_gaps

Revision ID: d8a2f4c9e1b3
Revises: c7f4e9b2a1d8
Create Date: 2026-11-14 23:25:00.000000

Adds:
- tenants.industry (VARCHAR(255)) for onboarding wizard metadata persistence
- projects.description (TEXT) to eliminate browser-local project description storage
- Updated provision_tenant(..., p_industry VARCHAR(255) DEFAULT NULL) SECURITY DEFINER function
- list_workspaces_for_email(p_email VARCHAR) SECURITY DEFINER function for workspace switching
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d8a2f4c9e1b3"
down_revision: Union[str, Sequence[str], None] = "c7f4e9b2a1d8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Add nullable industry column to tenants and description column to projects
    op.add_column("tenants", sa.Column("industry", sa.String(length=255), nullable=True))
    op.add_column("projects", sa.Column("description", sa.Text(), nullable=True))

    # 2. Replace provision_tenant (drop 10-arg signature to avoid overload ambiguity)
    op.execute(
        """
        DROP FUNCTION IF EXISTS provision_tenant(
            VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, UUID, UUID
        );
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION provision_tenant(
            p_name VARCHAR(255),
            p_slug VARCHAR(64),
            p_admin_email VARCHAR(255),
            p_admin_name VARCHAR(255),
            p_allowed_domain VARCHAR(255) DEFAULT NULL,
            p_tier VARCHAR(32) DEFAULT 'starter',
            p_ai_processing_mode VARCHAR(32) DEFAULT 'cloud_zdr',
            p_google_sub VARCHAR(255) DEFAULT NULL,
            p_tenant_id UUID DEFAULT NULL,
            p_user_id UUID DEFAULT NULL,
            p_industry VARCHAR(255) DEFAULT NULL
        )
        RETURNS TABLE (
            tenant_id UUID,
            user_id UUID,
            slug VARCHAR(64),
            admin_email VARCHAR(255)
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            v_tenant_id UUID;
            v_user_id UUID;
            v_sub VARCHAR(255);
        BEGIN
            IF p_name IS NULL OR length(trim(p_name)) = 0 THEN
                RAISE EXCEPTION 'Tenant name cannot be empty' USING ERRCODE = 'check_violation';
            END IF;

            IF p_slug IS NULL OR length(trim(p_slug)) = 0 THEN
                RAISE EXCEPTION 'Tenant slug cannot be empty' USING ERRCODE = 'check_violation';
            END IF;

            IF p_admin_email IS NULL OR length(trim(p_admin_email)) = 0 THEN
                RAISE EXCEPTION 'Administrator email cannot be empty' USING ERRCODE = 'check_violation';
            END IF;

            IF p_admin_name IS NULL OR length(trim(p_admin_name)) = 0 THEN
                RAISE EXCEPTION 'Administrator name cannot be empty' USING ERRCODE = 'check_violation';
            END IF;

            v_tenant_id := COALESCE(p_tenant_id, gen_random_uuid());
            v_user_id := COALESCE(p_user_id, gen_random_uuid());
            v_sub := COALESCE(NULLIF(trim(p_google_sub), ''), 'admin:' || lower(trim(p_admin_email)));

            INSERT INTO tenants (
                id,
                slug,
                name,
                allowed_domain,
                industry,
                tier,
                status,
                max_projects,
                ai_processing_mode,
                retention_days,
                created_at,
                updated_at
            ) VALUES (
                v_tenant_id,
                lower(trim(p_slug)),
                trim(p_name),
                NULLIF(trim(p_allowed_domain), ''),
                NULLIF(trim(p_industry), ''),
                COALESCE(p_tier, 'starter'),
                'active',
                5,
                COALESCE(p_ai_processing_mode, 'cloud_zdr'),
                90,
                now(),
                now()
            );

            INSERT INTO users (
                id,
                tenant_id,
                email,
                full_name,
                google_sub,
                role,
                is_active,
                created_at,
                updated_at
            ) VALUES (
                v_user_id,
                v_tenant_id,
                lower(trim(p_admin_email)),
                trim(p_admin_name),
                v_sub,
                'TENANT_ADMIN',
                TRUE,
                now(),
                now()
            );

            IF EXISTS (SELECT 1 FROM roles WHERE id = 'TENANT_ADMIN') THEN
                INSERT INTO user_roles (user_id, tenant_id, role_id, assigned_at)
                VALUES (v_user_id, v_tenant_id, 'TENANT_ADMIN', now())
                ON CONFLICT DO NOTHING;
            END IF;

            RETURN QUERY
            SELECT
                v_tenant_id,
                v_user_id,
                lower(trim(p_slug))::VARCHAR(64),
                lower(trim(p_admin_email))::VARCHAR(255);
        END;
        $$;
        """
    )

    op.execute(
        """
        REVOKE EXECUTE ON FUNCTION provision_tenant(
            VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, UUID, UUID, VARCHAR
        ) FROM PUBLIC;
        """
    )
    op.execute(
        """
        GRANT EXECUTE ON FUNCTION provision_tenant(
            VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, UUID, UUID, VARCHAR
        ) TO eng_app_user;
        """
    )

    # 3. Read-only SECURITY DEFINER helper to discover workspaces for a verified email without weakening RLS
    op.execute(
        """
        CREATE OR REPLACE FUNCTION list_workspaces_for_email(p_email VARCHAR(320))
        RETURNS TABLE (
            tenant_id UUID,
            tenant_name VARCHAR(255),
            tenant_slug VARCHAR(64),
            user_id UUID,
            user_role VARCHAR(50)
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        BEGIN
            IF p_email IS NULL OR length(trim(p_email)) = 0 THEN
                RETURN;
            END IF;

            RETURN QUERY
            SELECT
                t.id,
                t.name,
                t.slug,
                u.id,
                u.role::VARCHAR(50)
            FROM users u
            INNER JOIN tenants t ON t.id = u.tenant_id
            WHERE lower(trim(u.email)) = lower(trim(p_email))
              AND u.is_active = TRUE
              AND u.deleted_at IS NULL
              AND t.status = 'active'
            ORDER BY t.name ASC, t.created_at ASC;
        END;
        $$;
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION list_workspaces_for_email(VARCHAR) FROM PUBLIC;")
    op.execute("GRANT EXECUTE ON FUNCTION list_workspaces_for_email(VARCHAR) TO eng_app_user;")


def downgrade() -> None:
    op.execute("DROP FUNCTION IF EXISTS list_workspaces_for_email(VARCHAR);")

    op.execute(
        """
        DROP FUNCTION IF EXISTS provision_tenant(
            VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, UUID, UUID, VARCHAR
        );
        """
    )

    op.execute(
        """
        CREATE OR REPLACE FUNCTION provision_tenant(
            p_name VARCHAR(255),
            p_slug VARCHAR(64),
            p_admin_email VARCHAR(255),
            p_admin_name VARCHAR(255),
            p_allowed_domain VARCHAR(255) DEFAULT NULL,
            p_tier VARCHAR(32) DEFAULT 'starter',
            p_ai_processing_mode VARCHAR(32) DEFAULT 'cloud_zdr',
            p_google_sub VARCHAR(255) DEFAULT NULL,
            p_tenant_id UUID DEFAULT NULL,
            p_user_id UUID DEFAULT NULL
        )
        RETURNS TABLE (
            tenant_id UUID,
            user_id UUID,
            slug VARCHAR(64),
            admin_email VARCHAR(255)
        )
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            v_tenant_id UUID;
            v_user_id UUID;
            v_sub VARCHAR(255);
        BEGIN
            IF p_name IS NULL OR length(trim(p_name)) = 0 THEN
                RAISE EXCEPTION 'Tenant name cannot be empty' USING ERRCODE = 'check_violation';
            END IF;

            IF p_slug IS NULL OR length(trim(p_slug)) = 0 THEN
                RAISE EXCEPTION 'Tenant slug cannot be empty' USING ERRCODE = 'check_violation';
            END IF;

            IF p_admin_email IS NULL OR length(trim(p_admin_email)) = 0 THEN
                RAISE EXCEPTION 'Administrator email cannot be empty' USING ERRCODE = 'check_violation';
            END IF;

            IF p_admin_name IS NULL OR length(trim(p_admin_name)) = 0 THEN
                RAISE EXCEPTION 'Administrator name cannot be empty' USING ERRCODE = 'check_violation';
            END IF;

            v_tenant_id := COALESCE(p_tenant_id, gen_random_uuid());
            v_user_id := COALESCE(p_user_id, gen_random_uuid());
            v_sub := COALESCE(p_google_sub, 'admin:' || lower(trim(p_admin_email)));

            INSERT INTO tenants (
                id,
                slug,
                name,
                allowed_domain,
                tier,
                status,
                max_projects,
                ai_processing_mode,
                retention_days,
                created_at,
                updated_at
            ) VALUES (
                v_tenant_id,
                lower(trim(p_slug)),
                trim(p_name),
                NULLIF(trim(p_allowed_domain), ''),
                COALESCE(p_tier, 'starter'),
                'active',
                5,
                COALESCE(p_ai_processing_mode, 'cloud_zdr'),
                90,
                now(),
                now()
            );

            INSERT INTO users (
                id,
                tenant_id,
                email,
                full_name,
                google_sub,
                role,
                is_active,
                created_at,
                updated_at
            ) VALUES (
                v_user_id,
                v_tenant_id,
                lower(trim(p_admin_email)),
                trim(p_admin_name),
                v_sub,
                'TENANT_ADMIN',
                TRUE,
                now(),
                now()
            );

            IF EXISTS (SELECT 1 FROM roles WHERE id = 'TENANT_ADMIN') THEN
                INSERT INTO user_roles (user_id, tenant_id, role_id, assigned_at)
                VALUES (v_user_id, v_tenant_id, 'TENANT_ADMIN', now())
                ON CONFLICT DO NOTHING;
            END IF;

            RETURN QUERY
            SELECT
                v_tenant_id,
                v_user_id,
                lower(trim(p_slug))::VARCHAR(64),
                lower(trim(p_admin_email))::VARCHAR(255);
        END;
        $$;
        """
    )

    op.execute(
        """
        REVOKE EXECUTE ON FUNCTION provision_tenant(
            VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, UUID, UUID
        ) FROM PUBLIC;
        """
    )
    op.execute(
        """
        GRANT EXECUTE ON FUNCTION provision_tenant(
            VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, UUID, UUID
        ) TO eng_app_user;
        """
    )

    op.drop_column("projects", "description")
    op.drop_column("tenants", "industry")
