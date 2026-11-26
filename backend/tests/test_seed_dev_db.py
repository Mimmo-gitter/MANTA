"""Unit and static contract tests for local development DB seeding (scripts/seed_dev_db.py)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import unittest
import uuid

import pytest
import yaml

from scripts import seed_dev_db

REPO_ROOT = Path(__file__).resolve().parents[2]
REALM_EXPORT_PATH = REPO_ROOT / "infra" / "keycloak" / "realm-export.json"
COMPOSE_PATH = REPO_ROOT / "backend" / "docker-compose.yml"


class TestSeedDevDbPlan(unittest.TestCase):
    def test_load_seed_plan_from_committed_realm_export(self) -> None:
        """Every pre-configured Keycloak user and tenant in realm-export.json must map cleanly."""
        plan = seed_dev_db.load_seed_plan(REALM_EXPORT_PATH)

        tenant_map = {str(t.id): t for t in plan.tenants}
        self.assertIn("11111111-1111-1111-1111-111111111111", tenant_map)
        self.assertIn("22222222-2222-2222-2222-222222222222", tenant_map)
        self.assertEqual(tenant_map["11111111-1111-1111-1111-111111111111"].name, "Tenant Alpha")
        self.assertEqual(tenant_map["11111111-1111-1111-1111-111111111111"].slug, "tenant-alpha")
        self.assertEqual(tenant_map["22222222-2222-2222-2222-222222222222"].name, "Tenant Beta")
        self.assertEqual(tenant_map["22222222-2222-2222-2222-222222222222"].slug, "tenant-beta")

        users_by_sub = {u.google_sub: u for u in plan.users}
        self.assertGreaterEqual(len(users_by_sub), 13)
        self.assertIn("user-alpha-admin", users_by_sub)
        self.assertIn("user-beta-admin", users_by_sub)
        self.assertIn("user-mobile-test", users_by_sub)

        mobile_user = users_by_sub["user-mobile-test"]
        self.assertEqual(str(mobile_user.tenant_id), "11111111-1111-1111-1111-111111111111")
        self.assertEqual(mobile_user.email, "bohdan.dev777@gmail.com")
        self.assertEqual(mobile_user.full_name, "Bohdan Shcherbak")
        self.assertIn("SCRUM_MASTER", mobile_user.roles)
        self.assertIn("DELIVERY_DIRECTOR", mobile_user.roles)
        self.assertNotIn("offline_access", mobile_user.roles)
        self.assertNotIn("DELIVERY_EXEC", mobile_user.roles)

        alpha_admin = users_by_sub["user-alpha-admin"]
        self.assertEqual(alpha_admin.role, "TENANT_ADMIN")
        self.assertEqual(alpha_admin.roles, ("TENANT_ADMIN", "SOLUTION_ARCHITECT"))

    def test_role_normalization_handles_aliases_and_non_app_roles(self) -> None:
        primary, all_roles = seed_dev_db.normalize_realm_roles(
            ["offline_access", "DELIVERY_EXEC", "SCRUM_MASTER", "uma_authorization"]
        )
        self.assertEqual(primary, "DELIVERY_DIRECTOR")
        self.assertEqual(all_roles, ("DELIVERY_DIRECTOR", "SCRUM_MASTER"))

        fallback_primary, fallback_all = seed_dev_db.normalize_realm_roles(["offline_access"])
        self.assertEqual(fallback_primary, "DEVELOPER")
        self.assertEqual(fallback_all, ("DEVELOPER",))

    def test_derive_seed_user_uuid_is_deterministic(self) -> None:
        tid = uuid.UUID("11111111-1111-1111-1111-111111111111")
        u1 = seed_dev_db.derive_seed_user_uuid(tid, "user-mobile-test")
        u2 = seed_dev_db.derive_seed_user_uuid(tid, "user-mobile-test")
        self.assertEqual(u1, u2)

        explicit_uuid = uuid.uuid4()
        self.assertEqual(seed_dev_db.derive_seed_user_uuid(tid, str(explicit_uuid)), explicit_uuid)

    def test_is_seed_enabled_flag_parsing(self) -> None:
        self.assertTrue(seed_dev_db.is_seed_enabled("true"))
        self.assertTrue(seed_dev_db.is_seed_enabled("1"))
        self.assertTrue(seed_dev_db.is_seed_enabled("YES"))
        self.assertFalse(seed_dev_db.is_seed_enabled("false"))
        self.assertFalse(seed_dev_db.is_seed_enabled("0"))
        self.assertFalse(seed_dev_db.is_seed_enabled("no"))

    def test_docker_compose_wires_db_init_before_app_workers_without_blocking_ci_services(
        self,
    ) -> None:
        """db-init must migrate & seed before app/sweeper/worker, while leaving postgres/pgbouncer/keycloak independent."""
        compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
        services = compose["services"]

        self.assertIn("db-init", services)
        db_init = services["db-init"]
        self.assertEqual(db_init.get("restart"), "no")
        self.assertEqual(db_init["depends_on"]["postgres"]["condition"], "service_healthy")

        cmd_joined = " ".join(db_init["command"])
        self.assertIn("alembic upgrade head", cmd_joined)
        self.assertIn("scripts.seed_dev_db", cmd_joined)
        self.assertIn(
            "../infra/keycloak/realm-export.json:/infra/keycloak/realm-export.json:ro",
            db_init["volumes"],
        )
        self.assertIn(
            "../infra/keycloak/realm-export.json:/infra/keycloak/realm-export.json:ro",
            services["app"]["volumes"],
        )

        for dependent in ("app", "sweeper", "worker"):
            self.assertEqual(
                services[dependent]["depends_on"]["db-init"]["condition"],
                "service_completed_successfully",
                f"{dependent} must wait for db-init to complete migrations and seeding",
            )

        # CI jobs start `postgres pgbouncer` and `postgres keycloak` standalone without db-init.
        for infra_svc in ("postgres", "pgbouncer", "keycloak"):
            deps = services[infra_svc].get("depends_on") or {}
            self.assertNotIn("db-init", deps)


class _InMemorySeedConnection:
    """Minimal fake AsyncConnection to verify seed_database_with_connection idempotency."""

    def __init__(self) -> None:
        self.roles: set[str] = set(seed_dev_db.CANONICAL_ROLES)
        self.tenants: dict[uuid.UUID, dict[str, object]] = {}
        self.users: dict[uuid.UUID, dict[str, object]] = {}
        self.user_roles: set[tuple[uuid.UUID, str]] = set()
        self.adr_partitions: set[object] = set()

    async def execute(self, statement: object, params: dict[str, object] | None = None):
        sql = " ".join(str(statement).split())
        params = params or {}

        if sql == "SELECT id FROM roles":
            return SimpleNamespace(fetchall=lambda: [(r,) for r in sorted(self.roles)])

        if sql.startswith("SELECT id FROM tenants WHERE id = :id"):
            tid = params["id"]
            found = tid if tid in self.tenants else None
            return SimpleNamespace(scalar_one_or_none=lambda: found)

        if sql.startswith("INSERT INTO tenants"):
            tid = params["id"]
            slug = params["slug"]
            slug_exists = any(t["slug"] == slug for t in self.tenants.values())
            if tid in self.tenants or slug_exists:
                return SimpleNamespace(scalar_one_or_none=lambda: None)
            self.tenants[tid] = dict(params)  # type: ignore[index]
            return SimpleNamespace(scalar_one_or_none=lambda: tid)

        if sql.startswith("SELECT id FROM users WHERE tenant_id = :tenant_id"):
            tid = params["tenant_id"]
            sub = params["google_sub"]
            email = params["email"]
            found = next(
                (
                    uid
                    for uid, row in self.users.items()
                    if row["tenant_id"] == tid
                    and (row["google_sub"] == sub or str(row["email"]).lower() == email)
                ),
                None,
            )
            return SimpleNamespace(scalar_one_or_none=lambda: found)

        if sql.startswith("INSERT INTO users"):
            uid = params["id"]
            if uid in self.users:
                return SimpleNamespace(scalar_one_or_none=lambda: None)
            self.users[uid] = dict(params)  # type: ignore[index]
            return SimpleNamespace(scalar_one_or_none=lambda: uid)

        if sql.startswith("SELECT count(*) FROM user_roles WHERE user_id = :user_id"):
            uid = params["user_id"]
            cnt = sum(1 for u, _ in self.user_roles if u == uid)
            return SimpleNamespace(scalar_one=lambda: cnt)

        if sql.startswith("INSERT INTO user_roles"):
            key = (params["user_id"], str(params["role_id"]))
            if key in self.user_roles:
                return SimpleNamespace(scalar_one_or_none=lambda: None)
            self.user_roles.add(key)  # type: ignore[arg-type]
            return SimpleNamespace(scalar_one_or_none=lambda: key[1])

        if sql == "SELECT to_regprocedure('ensure_tenant_adr_partition(uuid)')":
            return SimpleNamespace(scalar_one_or_none=lambda: "ensure_tenant_adr_partition(uuid)")

        if sql.startswith("SELECT ensure_tenant_adr_partition(:tid)"):
            tid = params["tid"]
            self.adr_partitions.add(tid)
            return SimpleNamespace(scalar_one=lambda: f"anonymized_adrs_t_{tid}")

        raise AssertionError(f"Unexpected SQL in fake connection: {sql}")


@pytest.mark.asyncio
async def test_seed_database_with_connection_is_idempotent() -> None:
    plan = seed_dev_db.load_seed_plan(REALM_EXPORT_PATH)
    conn = _InMemorySeedConnection()

    first = await seed_dev_db.seed_database_with_connection(conn, plan)  # type: ignore[arg-type]
    assert first.tenants_created == len(plan.tenants)
    assert first.users_created == len(plan.users)
    assert first.user_roles_created > 0

    second = await seed_dev_db.seed_database_with_connection(conn, plan)  # type: ignore[arg-type]
    assert second.tenants_created == 0
    assert second.users_created == 0
    assert second.user_roles_created == 0
    assert len(conn.tenants) == len(plan.tenants)
    assert len(conn.users) == len(plan.users)
    assert conn.adr_partitions == {t.id for t in plan.tenants}


if __name__ == "__main__":
    unittest.main()
