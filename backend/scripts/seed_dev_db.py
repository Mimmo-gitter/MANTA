"""Idempotent local development database seeder driven by infra/keycloak/realm-export.json.

Populates PostgreSQL (`manta_db`) `tenants`, `users`, and `user_roles` tables so every
pre-configured Keycloak test user (including Tenant Alpha, Tenant Beta, and `mobile-test`)
has matching application database rows on a fresh local stack.

Usage:
    python -m scripts.seed_dev_db
    python -m scripts.seed_dev_db --respect-env
    python -m scripts.seed_dev_db --realm-file ../infra/keycloak/realm-export.json --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import sys
from typing import Any, Sequence
import uuid

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import pool, text  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine  # noqa: E402

# Constant UUIDv5 namespace for deterministic user primary keys when Keycloak user IDs
# in realm-export.json are human-readable strings (e.g. "user-alpha-admin", "user-mobile-test").
SEED_USER_NAMESPACE = uuid.UUID("a4f3b8c0-7e2d-4c1a-9b5e-3d8f1a2c4e6b")

ROLE_ALIASES: dict[str, str] = {
    "DELIVERY_EXEC": "DELIVERY_DIRECTOR",
}

ROLE_PRIORITY: tuple[str, ...] = (
    "TENANT_ADMIN",
    "TOP_MANAGEMENT",
    "DELIVERY_DIRECTOR",
    "SOLUTION_ARCHITECT",
    "PROJECT_MANAGER",
    "SCRUM_MASTER",
    "DEVELOPER",
)
CANONICAL_ROLES: frozenset[str] = frozenset(ROLE_PRIORITY)

KNOWN_TENANT_METADATA: dict[str, dict[str, Any]] = {
    "11111111-1111-1111-1111-111111111111": {
        "slug": "tenant-alpha",
        "name": "Tenant Alpha",
        "allowed_domain": "alpha.manta.local",
        "tier": "enterprise",
        "max_projects": 50,
        "ai_processing_mode": "cloud_zdr",
        "retention_days": 90,
    },
    "22222222-2222-2222-2222-222222222222": {
        "slug": "tenant-beta",
        "name": "Tenant Beta",
        "allowed_domain": "beta.manta.local",
        "tier": "starter",
        "max_projects": 10,
        "ai_processing_mode": "cloud_zdr",
        "retention_days": 90,
    },
}


@dataclass(frozen=True)
class SeedTenant:
    id: uuid.UUID
    slug: str
    name: str
    allowed_domain: str | None
    tier: str
    max_projects: int
    ai_processing_mode: str
    retention_days: int


@dataclass(frozen=True)
class SeedUser:
    id: uuid.UUID
    tenant_id: uuid.UUID
    email: str
    full_name: str
    google_sub: str
    role: str
    roles: tuple[str, ...]
    is_active: bool


@dataclass(frozen=True)
class SeedPlan:
    tenants: tuple[SeedTenant, ...]
    users: tuple[SeedUser, ...]


@dataclass(frozen=True)
class SeedSummary:
    tenants_created: int
    tenants_total: int
    users_created: int
    users_total: int
    user_roles_created: int


def is_seed_enabled(env_value: str | None = None) -> bool:
    """Return True unless SEED_DEV_DATA explicitly disables local seeding."""
    raw = os.getenv("SEED_DEV_DATA", "true") if env_value is None else env_value
    return raw.strip().lower() in ("1", "true", "yes", "on")


def resolve_realm_export_path(explicit_path: str | Path | None = None) -> Path:
    """Locate infra/keycloak/realm-export.json across CLI arg, env var, container, or repo layout."""
    candidates: list[Path] = []
    if explicit_path:
        candidates.append(Path(explicit_path))

    env_path = os.getenv("KEYCLOAK_REALM_EXPORT_PATH", "").strip()
    if env_path:
        candidates.append(Path(env_path))

    candidates.extend(
        [
            Path("/infra/keycloak/realm-export.json"),
            BACKEND_DIR.parent / "infra" / "keycloak" / "realm-export.json",
            BACKEND_DIR / "infra" / "keycloak" / "realm-export.json",
        ]
    )

    for candidate in candidates:
        if candidate.is_file():
            return candidate

    searched = ", ".join(str(p) for p in candidates)
    raise FileNotFoundError(f"Keycloak realm export file not found. Searched: {searched}")


def get_admin_db_url() -> str:
    """Return the administrative database URL (same convention as scripts/verify_migrations.py)."""
    settings_migration_url: str | None = None
    settings_db_url: str | None = None
    try:
        from app.config import settings

        settings_migration_url = getattr(settings, "MIGRATION_DATABASE_URL", None)
        settings_db_url = getattr(settings, "DATABASE_URL", None)
    except ImportError:
        pass

    url = settings_migration_url or os.getenv("MIGRATION_DATABASE_URL")
    if url:
        return url

    base_url = settings_db_url or os.getenv(
        "DATABASE_URL",
        "postgresql+asyncpg://manta_user:manta_password@localhost:5432/manta_db",
    )
    if "eng_app_user" in base_url:
        admin_user = os.getenv("POSTGRES_USER", "manta_user")
        admin_pass = os.getenv("POSTGRES_PASSWORD", "manta_password")
        return re.sub(r"://[^@]+@", f"://{admin_user}:{admin_pass}@", base_url)
    return base_url


def normalize_realm_roles(raw_roles: Sequence[str] | None) -> tuple[str, tuple[str, ...]]:
    """Map Keycloak realmRoles to canonical MANTA roles and select the primary role."""
    normalized: set[str] = set()
    for raw in raw_roles or ():
        if not isinstance(raw, str):
            continue
        cleaned = raw.strip().upper()
        mapped = ROLE_ALIASES.get(cleaned, cleaned)
        if mapped in CANONICAL_ROLES:
            normalized.add(mapped)

    if not normalized:
        return "DEVELOPER", ("DEVELOPER",)

    ordered = tuple(role for role in ROLE_PRIORITY if role in normalized)
    return ordered[0], ordered


def derive_seed_user_uuid(tenant_id: uuid.UUID, google_sub: str) -> uuid.UUID:
    """Preserve UUID subjects verbatim; deterministically derive UUIDv5 for string IDs."""
    try:
        return uuid.UUID(google_sub)
    except ValueError:
        return uuid.uuid5(SEED_USER_NAMESPACE, f"{tenant_id}:{google_sub}")


def build_seed_plan_from_realm(realm_data: dict[str, Any]) -> SeedPlan:
    """Build a deterministic tenant/user seed plan from parsed Keycloak realm-export JSON."""
    tenants_by_id: dict[uuid.UUID, SeedTenant] = {}
    users: list[SeedUser] = []
    seen_tenant_emails: set[tuple[uuid.UUID, str]] = set()
    seen_tenant_subs: set[tuple[uuid.UUID, str]] = set()

    for raw_user in realm_data.get("users") or ():
        if not isinstance(raw_user, dict):
            continue

        attrs = raw_user.get("attributes") or {}
        raw_tenant_ids = attrs.get("tenant_id") or []
        if isinstance(raw_tenant_ids, str):
            raw_tenant_ids = [raw_tenant_ids]

        tenant_uuid: uuid.UUID | None = None
        for candidate_tid in raw_tenant_ids:
            if not isinstance(candidate_tid, str) or not candidate_tid.strip():
                continue
            try:
                parsed = uuid.UUID(candidate_tid.strip())
            except ValueError:
                continue
            if parsed.int != 0:
                tenant_uuid = parsed
                break

        if tenant_uuid is None:
            continue

        email = str(raw_user.get("email") or "").strip().lower()
        google_sub = str(raw_user.get("id") or raw_user.get("username") or "").strip()
        if not email or not google_sub:
            continue

        if tenant_uuid not in tenants_by_id:
            meta = KNOWN_TENANT_METADATA.get(str(tenant_uuid))
            short_id = str(tenant_uuid)[:8]
            tenants_by_id[tenant_uuid] = SeedTenant(
                id=tenant_uuid,
                slug=meta["slug"] if meta else f"tenant-{short_id}",
                name=meta["name"] if meta else f"Tenant {short_id}",
                allowed_domain=meta["allowed_domain"] if meta else None,
                tier=meta["tier"] if meta else "starter",
                max_projects=meta["max_projects"] if meta else 5,
                ai_processing_mode=meta["ai_processing_mode"] if meta else "cloud_zdr",
                retention_days=meta["retention_days"] if meta else 90,
            )

        if (tenant_uuid, email) in seen_tenant_emails or (
            tenant_uuid,
            google_sub,
        ) in seen_tenant_subs:
            continue
        seen_tenant_emails.add((tenant_uuid, email))
        seen_tenant_subs.add((tenant_uuid, google_sub))

        first_name = str(raw_user.get("firstName") or "").strip()
        last_name = str(raw_user.get("lastName") or "").strip()
        username = str(raw_user.get("username") or "").strip()
        full_name = f"{first_name} {last_name}".strip() or username or email.split("@")[0]

        primary_role, all_roles = normalize_realm_roles(raw_user.get("realmRoles"))
        users.append(
            SeedUser(
                id=derive_seed_user_uuid(tenant_uuid, google_sub),
                tenant_id=tenant_uuid,
                email=email,
                full_name=full_name,
                google_sub=google_sub,
                role=primary_role,
                roles=all_roles,
                is_active=bool(raw_user.get("enabled", True)),
            )
        )

    ordered_tenants = tuple(sorted(tenants_by_id.values(), key=lambda t: str(t.id)))
    return SeedPlan(tenants=ordered_tenants, users=tuple(users))


def load_seed_plan(realm_path: Path) -> SeedPlan:
    """Read and parse a Keycloak realm-export JSON file into a SeedPlan."""
    with realm_path.open("r", encoding="utf-8") as handle:
        realm_data = json.load(handle)
    return build_seed_plan_from_realm(realm_data)


async def seed_database_with_connection(conn: AsyncConnection, plan: SeedPlan) -> SeedSummary:
    """Idempotently apply a SeedPlan using an open administrative SQLAlchemy connection."""
    role_rows = await conn.execute(text("SELECT id FROM roles"))
    valid_roles = {row[0] for row in role_rows.fetchall()}

    tenants_created = 0
    for tenant in plan.tenants:
        existing_tenant = (
            await conn.execute(
                text("SELECT id FROM tenants WHERE id = :id"),
                {"id": tenant.id},
            )
        ).scalar_one_or_none()

        if existing_tenant is not None:
            continue

        inserted_tenant = (
            await conn.execute(
                text(
                    """
                    INSERT INTO tenants (
                        id, slug, name, allowed_domain, tier, status,
                        max_projects, ai_processing_mode, retention_days,
                        created_at, updated_at
                    ) VALUES (
                        :id, :slug, :name, :allowed_domain, :tier, 'active',
                        :max_projects, :ai_processing_mode, :retention_days,
                        now(), now()
                    )
                    ON CONFLICT DO NOTHING
                    RETURNING id
                    """
                ),
                {
                    "id": tenant.id,
                    "slug": tenant.slug,
                    "name": tenant.name,
                    "allowed_domain": tenant.allowed_domain,
                    "tier": tenant.tier,
                    "max_projects": tenant.max_projects,
                    "ai_processing_mode": tenant.ai_processing_mode,
                    "retention_days": tenant.retention_days,
                },
            )
        ).scalar_one_or_none()

        if inserted_tenant is None:
            # Handle the edge case where the slug is already taken by another tenant UUID
            fallback_slug = f"{tenant.slug}-{str(tenant.id)[:8]}"[:64]
            inserted_tenant = (
                await conn.execute(
                    text(
                        """
                        INSERT INTO tenants (
                            id, slug, name, allowed_domain, tier, status,
                            max_projects, ai_processing_mode, retention_days,
                            created_at, updated_at
                        ) VALUES (
                            :id, :slug, :name, :allowed_domain, :tier, 'active',
                            :max_projects, :ai_processing_mode, :retention_days,
                            now(), now()
                        )
                        ON CONFLICT DO NOTHING
                        RETURNING id
                        """
                    ),
                    {
                        "id": tenant.id,
                        "slug": fallback_slug,
                        "name": tenant.name,
                        "allowed_domain": tenant.allowed_domain,
                        "tier": tenant.tier,
                        "max_projects": tenant.max_projects,
                        "ai_processing_mode": tenant.ai_processing_mode,
                        "retention_days": tenant.retention_days,
                    },
                )
            ).scalar_one_or_none()

        if inserted_tenant is not None:
            tenants_created += 1

    # Ensure every seeded tenant owns an anonymized_adrs partition when US-44 migration is applied
    has_partition_fn = (
        await conn.execute(text("SELECT to_regprocedure('ensure_tenant_adr_partition(uuid)')"))
    ).scalar_one_or_none()
    if has_partition_fn is not None:
        for tenant in plan.tenants:
            await conn.execute(
                text("SELECT ensure_tenant_adr_partition(:tid)"),
                {"tid": tenant.id},
            )

    users_created = 0
    user_roles_created = 0

    for user in plan.users:
        existing_user_id = (
            await conn.execute(
                text(
                    """
                    SELECT id
                    FROM users
                    WHERE tenant_id = :tenant_id
                      AND (google_sub = :google_sub OR lower(email) = :email)
                    LIMIT 1
                    """
                ),
                {
                    "tenant_id": user.tenant_id,
                    "google_sub": user.google_sub,
                    "email": user.email,
                },
            )
        ).scalar_one_or_none()

        is_new_user = existing_user_id is None
        target_user_id = existing_user_id

        if is_new_user:
            target_user_id = (
                await conn.execute(
                    text(
                        """
                        INSERT INTO users (
                            id, tenant_id, email, full_name, google_sub,
                            role, is_active, created_at, updated_at
                        ) VALUES (
                            :id, :tenant_id, :email, :full_name, :google_sub,
                            :role, :is_active, now(), now()
                        )
                        ON CONFLICT DO NOTHING
                        RETURNING id
                        """
                    ),
                    {
                        "id": user.id,
                        "tenant_id": user.tenant_id,
                        "email": user.email,
                        "full_name": user.full_name,
                        "google_sub": user.google_sub,
                        "role": user.role,
                        "is_active": user.is_active,
                    },
                )
            ).scalar_one_or_none()

            if target_user_id is not None:
                users_created += 1
            else:
                target_user_id = (
                    await conn.execute(
                        text(
                            """
                            SELECT id
                            FROM users
                            WHERE tenant_id = :tenant_id
                              AND (google_sub = :google_sub OR lower(email) = :email)
                            LIMIT 1
                            """
                        ),
                        {
                            "tenant_id": user.tenant_id,
                            "google_sub": user.google_sub,
                            "email": user.email,
                        },
                    )
                ).scalar_one_or_none()
                is_new_user = False

        if target_user_id is None:
            continue

        should_insert_roles = is_new_user
        if not should_insert_roles:
            existing_roles_count = (
                await conn.execute(
                    text("SELECT count(*) FROM user_roles WHERE user_id = :user_id"),
                    {"user_id": target_user_id},
                )
            ).scalar_one()
            should_insert_roles = int(existing_roles_count) == 0

        if should_insert_roles:
            for role_id in user.roles:
                if valid_roles and role_id not in valid_roles:
                    continue
                inserted_role = (
                    await conn.execute(
                        text(
                            """
                            INSERT INTO user_roles (user_id, tenant_id, role_id, assigned_at)
                            VALUES (:user_id, :tenant_id, :role_id, now())
                            ON CONFLICT (user_id, role_id) DO NOTHING
                            RETURNING role_id
                            """
                        ),
                        {
                            "user_id": target_user_id,
                            "tenant_id": user.tenant_id,
                            "role_id": role_id,
                        },
                    )
                ).scalar_one_or_none()
                if inserted_role is not None:
                    user_roles_created += 1

    return SeedSummary(
        tenants_created=tenants_created,
        tenants_total=len(plan.tenants),
        users_created=users_created,
        users_total=len(plan.users),
        user_roles_created=user_roles_created,
    )


async def seed_database(
    realm_path: Path,
    db_url: str | None = None,
) -> SeedSummary:
    """Load the Keycloak realm export and idempotently seed tenants, users, and user_roles."""
    plan = load_seed_plan(realm_path)
    engine = create_async_engine(db_url or get_admin_db_url(), poolclass=pool.NullPool)
    try:
        async with engine.begin() as conn:
            return await seed_database_with_connection(conn, plan)
    finally:
        await engine.dispose()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Idempotently seed local PostgreSQL tenants/users from infra/keycloak/realm-export.json."
        )
    )
    parser.add_argument(
        "--realm-file",
        default=None,
        help="Path to Keycloak realm-export.json (auto-detected if omitted).",
    )
    parser.add_argument(
        "--respect-env",
        action="store_true",
        help="Skip seeding cleanly when SEED_DEV_DATA is false/0/no.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse realm-export.json and print planned rows without touching the database.",
    )
    args = parser.parse_args(argv)

    if args.respect_env and not is_seed_enabled():
        print("[seed_dev_db] SEED_DEV_DATA is disabled; skipping local dev seeding.", flush=True)
        return 0

    realm_path = resolve_realm_export_path(args.realm_file)
    plan = load_seed_plan(realm_path)

    if args.dry_run:
        print(
            f"[seed_dev_db] Dry-run from {realm_path}: "
            f"{len(plan.tenants)} tenants, {len(plan.users)} users.",
            flush=True,
        )
        for tenant in plan.tenants:
            print(f"  tenant {tenant.id} ({tenant.slug} / {tenant.name})", flush=True)
        for user in plan.users:
            roles_str = ", ".join(user.roles)
            print(
                f"  user   {user.id} tenant={user.tenant_id} sub={user.google_sub} "
                f"email={user.email} role={user.role} roles=[{roles_str}]",
                flush=True,
            )
        return 0

    summary = asyncio.run(seed_database(realm_path))
    print(
        f"[seed_dev_db] Seed complete from {realm_path}: "
        f"tenants created={summary.tenants_created}/{summary.tenants_total}, "
        f"users created={summary.users_created}/{summary.users_total}, "
        f"user_roles created={summary.user_roles_created}.",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
