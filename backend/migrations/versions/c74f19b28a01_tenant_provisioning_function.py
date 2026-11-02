"""SECURITY DEFINER provisioning function for tenant registration (SCRUM-288)

Revision ID: c74f19b28a01
Revises: 5b1a7d8e2093
Create Date: 2026-10-29 20:00:00.000000

Jira Requirements:
- SCRUM-288: Implement SECURITY DEFINER provisioning function for tenant and first administrator
- Revoke direct INSERT on tenants table from eng_app_user
- Grant execute on provision_tenant exclusively to eng_app_user
"""

from typing import Sequence, Union
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c74f19b28a01"
down_revision: Union[str, None] = "5b1a7d8e2093"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Seed standard platform roles in `roles` table if not already present
    op.execute(
        """
        INSERT INTO roles (id, description) VALUES
            ('TENANT_ADMIN', 'Organization Administrator with full access to tenant resources'),
            ('DEVELOPER', 'Standard Developer role with write access to projects and code'),
            ('SCRUM_MASTER', 'Scrum Master / Agile Coach role'),
            ('SOLUTION_ARCHITECT', 'Architect role with design authority'),
            ('DELIVERY_EXEC', 'Delivery Executive / Management oversight role')
        ON CONFLICT (id) DO NOTHING;
        """
    )

    # 2. Create atomic SECURITY DEFINER provisioning function with pinned search_path
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
            -- Validate required arguments
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

            -- Assign IDs (generate if not supplied)
            v_tenant_id := COALESCE(p_tenant_id, gen_random_uuid());
            v_user_id := COALESCE(p_user_id, gen_random_uuid());
            v_sub := COALESCE(p_google_sub, 'admin:' || lower(trim(p_admin_email)));

            -- 1. Create Tenant record
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

            -- 2. Create Administrator User record
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

            -- 3. Assign TENANT_ADMIN role in user_roles
            IF EXISTS (SELECT 1 FROM roles WHERE id = 'TENANT_ADMIN') THEN
                INSERT INTO user_roles (user_id, tenant_id, role_id, assigned_at)
                VALUES (v_user_id, v_tenant_id, 'TENANT_ADMIN', now())
                ON CONFLICT DO NOTHING;
            END IF;

            -- 4. Return generated record
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

    # 3. Restrict function execution: revoke from PUBLIC, grant to eng_app_user
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

    # 4. SCRUM-288 AC 3: eng_app_user cannot insert into the tenants table directly
    op.execute("REVOKE INSERT ON TABLE tenants FROM eng_app_user;")


def downgrade() -> None:
    # 1. Restore direct INSERT grant on tenants table for eng_app_user
    op.execute("GRANT INSERT ON TABLE tenants TO eng_app_user;")

    # 2. Drop the provisioning function
    op.execute(
        """
        DROP FUNCTION IF EXISTS provision_tenant(
            VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, VARCHAR, UUID, UUID
        );
        """
    )
