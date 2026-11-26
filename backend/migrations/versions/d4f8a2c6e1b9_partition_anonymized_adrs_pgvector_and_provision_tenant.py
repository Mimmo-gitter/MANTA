"""partition_anonymized_adrs_pgvector_and_provision_tenant

Revision ID: d4f8a2c6e1b9
Revises: c9a3f7e1d2b4
Create Date: 2026-11-26 22:05:00.000000

Jira Requirements:
- SCRUM-572 / TASK-317-1 (US-44):
  1. Convert `anonymized_adrs` into PostgreSQL 16 declarative LIST partitioning by `tenant_id`.
  2. Add dual pgvector embedding columns `embedding_1536` (vector(1536)) and
     `embedding_384` (vector(384)) with per-partition HNSW cosine indexes.
  3. Enforce ENABLE + FORCE ROW LEVEL SECURITY and `tenant_isolation_policy` on both the
     partitioned parent table and every child partition.
- SCRUM-573 / TASK-317-2 (US-44):
  1. Create idempotent `ensure_tenant_adr_partition(UUID)` SECURITY DEFINER helper.
  2. Backfill dedicated partitions for all existing tenants during migration.
  3. Update `provision_tenant(...)` to automatically create the tenant's child ADR partition,
     HNSW indexes, FORCE RLS, and policy upon registration.
"""

from typing import Sequence, Union

from alembic import op
from pgvector.sqlalchemy import Vector
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d4f8a2c6e1b9"
down_revision: Union[str, Sequence[str], None] = "c9a3f7e1d2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector;")

    # 1. Stage legacy table before creating declarative LIST-partitioned parent
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON anonymized_adrs;")
    op.execute("ALTER TABLE anonymized_adrs RENAME TO anonymized_adrs_legacy;")
    op.execute(
        "ALTER INDEX IF EXISTS idx_anonymized_adrs_tenant_id "
        "RENAME TO idx_anonymized_adrs_legacy_tenant_id;"
    )
    op.execute(
        "ALTER INDEX IF EXISTS idx_anonymized_adrs_tenant_status "
        "RENAME TO idx_anonymized_adrs_legacy_tenant_status;"
    )
    op.execute(
        "ALTER TABLE anonymized_adrs_legacy DROP CONSTRAINT IF EXISTS ck_anonymized_adrs_status;"
    )
    op.execute("ALTER TABLE anonymized_adrs_legacy DROP CONSTRAINT IF EXISTS anonymized_adrs_pkey;")
    op.execute(
        "ALTER TABLE anonymized_adrs_legacy "
        "DROP CONSTRAINT IF EXISTS anonymized_adrs_approved_by_fkey;"
    )

    # 2. Create declarative LIST(tenant_id)-partitioned anonymized_adrs parent
    op.execute(
        """
        CREATE TABLE anonymized_adrs (
            tenant_id UUID NOT NULL,
            id UUID NOT NULL,
            project_id UUID NOT NULL,
            source_repository_id UUID,
            source_commit_sha VARCHAR(40),
            source_content_hash VARCHAR(64),
            title TEXT NOT NULL,
            decision_summary TEXT,
            architecture_pattern VARCHAR(128),
            visibility VARCHAR(32) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'draft',
            sanitization_verified BOOLEAN NOT NULL,
            approved_by UUID REFERENCES users(id) ON DELETE SET NULL,
            approved_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            CONSTRAINT ck_anonymized_adrs_status
                CHECK (status IN ('draft', 'pending_review', 'approved', 'quarantined', 'rejected')),
            PRIMARY KEY (tenant_id, id)
        ) PARTITION BY LIST (tenant_id);
        """
    )

    # Add dual pgvector columns explicitly via op.add_column for schema tracking parity
    op.add_column("anonymized_adrs", sa.Column("embedding_1536", Vector(1536), nullable=True))
    op.add_column("anonymized_adrs", sa.Column("embedding_384", Vector(384), nullable=True))

    op.execute("CREATE INDEX idx_anonymized_adrs_tenant_id ON anonymized_adrs (tenant_id);")
    op.execute(
        "CREATE INDEX idx_anonymized_adrs_tenant_status ON anonymized_adrs (tenant_id, status);"
    )

    op.execute("ALTER TABLE anonymized_adrs ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE anonymized_adrs FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON anonymized_adrs;")
    op.execute(
        """
        CREATE POLICY tenant_isolation_policy ON anonymized_adrs
            AS PERMISSIVE FOR ALL
            TO eng_app_user
            USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON anonymized_adrs TO eng_app_user;")

    # 3. Create SECURITY DEFINER partition helper creating child partition + HNSW + FORCE RLS
    op.execute(
        """
        CREATE OR REPLACE FUNCTION ensure_tenant_adr_partition(p_tenant_id UUID)
        RETURNS TEXT
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = public, pg_temp
        AS $$
        DECLARE
            v_tenant_hex TEXT;
            v_part_name TEXT;
            v_idx_1536 TEXT;
            v_idx_384 TEXT;
        BEGIN
            IF p_tenant_id IS NULL THEN
                RAISE EXCEPTION 'p_tenant_id cannot be NULL' USING ERRCODE = 'null_value_not_allowed';
            END IF;

            v_tenant_hex := replace(p_tenant_id::text, '-', '');
            v_part_name := 'anonymized_adrs_t_' || v_tenant_hex;
            v_idx_1536 := 'idx_adr_t_' || v_tenant_hex || '_emb1536';
            v_idx_384 := 'idx_adr_t_' || v_tenant_hex || '_emb384';

            EXECUTE format(
                'CREATE TABLE IF NOT EXISTS public.%I '
                'PARTITION OF public.anonymized_adrs FOR VALUES IN (%L);',
                v_part_name,
                p_tenant_id::text
            );

            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS %I ON public.%I '
                'USING hnsw (embedding_1536 vector_cosine_ops) '
                'WITH (m = 16, ef_construction = 64) WHERE embedding_1536 IS NOT NULL;',
                v_idx_1536,
                v_part_name
            );

            EXECUTE format(
                'CREATE INDEX IF NOT EXISTS %I ON public.%I '
                'USING hnsw (embedding_384 vector_cosine_ops) '
                'WITH (m = 16, ef_construction = 64) WHERE embedding_384 IS NOT NULL;',
                v_idx_384,
                v_part_name
            );

            EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY;', v_part_name);
            EXECUTE format('ALTER TABLE public.%I FORCE ROW LEVEL SECURITY;', v_part_name);
            EXECUTE format('DROP POLICY IF EXISTS tenant_isolation_policy ON public.%I;', v_part_name);
            EXECUTE format(
                'CREATE POLICY tenant_isolation_policy ON public.%I '
                'AS PERMISSIVE FOR ALL TO eng_app_user '
                'USING (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::uuid) '
                'WITH CHECK (tenant_id = NULLIF(current_setting(''app.current_tenant_id'', true), '''')::uuid);',
                v_part_name
            );
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON public.%I TO eng_app_user;',
                v_part_name
            );

            RETURN v_part_name;
        END;
        $$;
        """
    )
    op.execute("REVOKE EXECUTE ON FUNCTION ensure_tenant_adr_partition(UUID) FROM PUBLIC;")

    # 4. Backfill partitions for all existing tenants + migrate rows from legacy table
    op.execute(
        """
        DO $$
        DECLARE
            rec RECORD;
        BEGIN
            FOR rec IN (
                SELECT id AS tid FROM tenants
                UNION
                SELECT DISTINCT tenant_id AS tid FROM anonymized_adrs_legacy
            ) LOOP
                PERFORM ensure_tenant_adr_partition(rec.tid);
            END LOOP;

            INSERT INTO anonymized_adrs (
                tenant_id, id, project_id, source_repository_id, source_commit_sha,
                source_content_hash, title, decision_summary, architecture_pattern,
                visibility, status, sanitization_verified, approved_by, approved_at,
                created_at, updated_at
            )
            SELECT
                tenant_id, id, project_id, source_repository_id, source_commit_sha,
                source_content_hash, title, decision_summary, architecture_pattern,
                visibility, status, sanitization_verified, approved_by, approved_at,
                created_at, updated_at
            FROM anonymized_adrs_legacy;
        END
        $$;
        """
    )
    op.execute("DROP TABLE anonymized_adrs_legacy;")

    # 5. Update 11-arg provision_tenant to auto-create child partition + RLS on tenant registration
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

            PERFORM ensure_tenant_adr_partition(v_tenant_id);

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


def downgrade() -> None:
    # Restore previous 11-arg provision_tenant without ensure_tenant_adr_partition
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
                id, slug, name, allowed_domain, industry, tier, status,
                max_projects, ai_processing_mode, retention_days, created_at, updated_at
            ) VALUES (
                v_tenant_id, lower(trim(p_slug)), trim(p_name),
                NULLIF(trim(p_allowed_domain), ''), NULLIF(trim(p_industry), ''),
                COALESCE(p_tier, 'starter'), 'active', 5,
                COALESCE(p_ai_processing_mode, 'cloud_zdr'), 90, now(), now()
            );

            INSERT INTO users (
                id, tenant_id, email, full_name, google_sub, role, is_active, created_at, updated_at
            ) VALUES (
                v_user_id, v_tenant_id, lower(trim(p_admin_email)), trim(p_admin_name),
                v_sub, 'TENANT_ADMIN', TRUE, now(), now()
            );

            IF EXISTS (SELECT 1 FROM roles WHERE id = 'TENANT_ADMIN') THEN
                INSERT INTO user_roles (user_id, tenant_id, role_id, assigned_at)
                VALUES (v_user_id, v_tenant_id, 'TENANT_ADMIN', now())
                ON CONFLICT DO NOTHING;
            END IF;

            RETURN QUERY
            SELECT v_tenant_id, v_user_id, lower(trim(p_slug))::VARCHAR(64), lower(trim(p_admin_email))::VARCHAR(255);
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

    op.execute(
        """
        CREATE TABLE anonymized_adrs_unpartitioned (
            tenant_id UUID NOT NULL,
            id UUID NOT NULL,
            project_id UUID NOT NULL,
            source_repository_id UUID,
            source_commit_sha VARCHAR(40),
            source_content_hash VARCHAR(64),
            title TEXT NOT NULL,
            decision_summary TEXT,
            architecture_pattern VARCHAR(128),
            visibility VARCHAR(32) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'draft',
            sanitization_verified BOOLEAN NOT NULL,
            approved_by UUID REFERENCES users(id) ON DELETE SET NULL,
            approved_at TIMESTAMP WITH TIME ZONE,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
            CONSTRAINT ck_anonymized_adrs_status
                CHECK (status IN ('draft', 'pending_review', 'approved', 'quarantined', 'rejected')),
            PRIMARY KEY (tenant_id, id)
        );
        """
    )
    op.execute(
        """
        INSERT INTO anonymized_adrs_unpartitioned (
            tenant_id, id, project_id, source_repository_id, source_commit_sha,
            source_content_hash, title, decision_summary, architecture_pattern,
            visibility, status, sanitization_verified, approved_by, approved_at,
            created_at, updated_at
        )
        SELECT
            tenant_id, id, project_id, source_repository_id, source_commit_sha,
            source_content_hash, title, decision_summary, architecture_pattern,
            visibility, status, sanitization_verified, approved_by, approved_at,
            created_at, updated_at
        FROM anonymized_adrs;
        """
    )
    op.execute("DROP TABLE anonymized_adrs CASCADE;")
    op.execute("ALTER TABLE anonymized_adrs_unpartitioned RENAME TO anonymized_adrs;")
    op.execute("CREATE INDEX idx_anonymized_adrs_tenant_id ON anonymized_adrs (tenant_id);")
    op.execute(
        "CREATE INDEX idx_anonymized_adrs_tenant_status ON anonymized_adrs (tenant_id, status);"
    )
    op.execute("ALTER TABLE anonymized_adrs ENABLE ROW LEVEL SECURITY;")
    op.execute("ALTER TABLE anonymized_adrs FORCE ROW LEVEL SECURITY;")
    op.execute("DROP POLICY IF EXISTS tenant_isolation_policy ON anonymized_adrs;")
    op.execute(
        """
        CREATE POLICY tenant_isolation_policy ON anonymized_adrs
            AS PERMISSIVE FOR ALL
            TO eng_app_user
            USING (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid)
            WITH CHECK (tenant_id = NULLIF(current_setting('app.current_tenant_id', true), '')::uuid);
        """
    )
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON anonymized_adrs TO eng_app_user;")

    op.execute("DROP FUNCTION IF EXISTS ensure_tenant_adr_partition(UUID);")
